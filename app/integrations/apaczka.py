"""Apaczka (API v2): wycena u wielu kurierow, zamowienie kuriera, etykieta PDF i sledzenie.

Klucze: panel.apaczka.pl -> Ustawienia -> API -> App ID i App Secret. Kazde zapytanie to POST
(form: app_id, request=JSON, expires, signature), podpis HMAC-SHA256 z "app_id:route:request:expires".
Zamowienie kuriera kosztuje, wiec dzieje sie tylko po kliknieciu "Zamów kuriera" przez uzytkownika.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal


from ..models import Integration, Shipment
from . import http
from .common import IntegrationError, dec, secrets_of

BASE = "https://www.apaczka.pl/api/v2"
TRACK_STATES = {"NEW": "ordered", "PICKUP": "in_transit", "IN_TRANSIT": "in_transit", "OUT_FOR_DELIVERY": "in_transit",
                "READY_TO_PICKUP": "in_transit", "DELIVERED": "delivered", "EXCEPTION": "problem",
                "RETURNED_TO_SHIPPER": "problem", "CANCELLED": "cancelled"}


def money(v) -> Decimal | None:
    """Apaczka podaje kwoty raz w groszach (liczba calkowita), raz w zlotych ("9.92")."""
    if v is None or v == "":
        return None
    if isinstance(v, str) and ("." in v or "," in v):
        return dec(v.replace(",", "."), None)
    d = dec(v, None)
    return None if d is None else (d / 100).quantize(Decimal("0.01"))


def signature(app_id: str, route: str, request: str, expires: int, secret: str) -> str:
    msg = f"{app_id}:{route}:{request}:{expires}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def call(integ: Integration, route: str, payload: dict | None = None) -> dict:
    sec = secrets_of(integ)
    app_id, secret = sec.get("app_id", ""), sec.get("app_secret", "")
    if not (app_id and secret):
        raise IntegrationError("Brak kluczy Apaczki. Wpisz App ID i App Secret.")
    route = route.strip("/") + "/"
    req = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
    expires = int(time.time()) + 600
    r = http.request("POST", f"{BASE}/{route}", data={"app_id": app_id, "request": req, "expires": str(expires),
                                                       "signature": signature(app_id, route, req, expires, secret)})
    try:
        body = r.json()
    except ValueError:
        raise IntegrationError(f"Apaczka: nieczytelna odpowiedź (HTTP {r.status_code}).") from None
    if int(body.get("status") or 0) != 200:
        raise IntegrationError(f"Apaczka: {body.get('message') or 'błąd'} (kod {body.get('status')}).")
    return body.get("response") or {}


def services(integ: Integration) -> list[dict]:
    res = call(integ, "service_structure")
    rows = res.get("services") or []
    return [{"id": str(s.get("service_id")), "name": s.get("name") or "", "supplier": s.get("supplier") or "",
             "door_to_door": s.get("door_to_door"), "point": s.get("pickup_point") or s.get("point_type") or ""}
            for s in rows if s.get("service_id") is not None]


def _addr(a: dict) -> dict:
    return {"country_code": a.get("country_code") or "PL", "name": (a.get("name") or "")[:50],
            "line1": (a.get("line1") or "")[:50], "line2": (a.get("line2") or "")[:50],
            "postal_code": a.get("postal_code") or "", "city": a.get("city") or "",
            "is_residential": 1 if a.get("is_residential") else 0, "contact_person": a.get("contact_person") or "",
            "email": a.get("email") or "", "phone": a.get("phone") or "",
            **({"foreign_address_id": a["point"]} if a.get("point") else {})}


def order_payload(integ: Integration, s: Shipment, service_id: str | None = None) -> dict:
    sender = integ.settings.get("sender") or {}
    receiver = json.loads(s.receiver_json or "{}")
    parcel = json.loads(s.parcel_json or "{}")
    o = {"service_id": int(service_id or s.service_id or 0) or None,
         "address": {"sender": _addr(sender), "receiver": _addr(receiver)},
         "pickup": {"type": integ.settings.get("pickup_type") or "SELF"},
         "shipment": [{"dimension1": int(parcel.get("l") or 30), "dimension2": int(parcel.get("w") or 20),
                       "dimension3": int(parcel.get("h") or 10), "weight": float(parcel.get("kg") or 1),
                       "is_nstd": 0, "shipment_type_code": "PACZKA"}],
         "content": (parcel.get("content") or "Towar")[:100], "comment": (parcel.get("comment") or "")[:100]}
    if s.cod_amount:
        o["cod"] = {"amount": int((s.cod_amount * 100).to_integral_value()),
                    "bankaccount": (integ.settings.get("cod_account") or "").replace(" ", "")}
    return {"order": {k: v for k, v in o.items() if v is not None}}


def valuation(integ: Integration, s: Shipment) -> list[dict]:
    """Ceny wszystkich uslug dla tej paczki, od najtanszej. Kwoty Apaczka podaje w groszach."""
    res = call(integ, "order_valuation", order_payload(integ, s))
    names = {x["id"]: x for x in services(integ)}
    out = []
    for sid, p in (res.get("price_table") or {}).items():
        gross = money(p.get("price_gross"))
        if gross is None:
            continue
        info = names.get(str(sid), {})
        out.append({"id": str(sid), "name": info.get("name") or f"Usługa {sid}", "gross": gross})
    return sorted(out, key=lambda x: x["gross"])


def send(integ: Integration, s: Shipment) -> None:
    res = call(integ, "order_send", order_payload(integ, s)).get("order") or {}
    s.external_id = str(res.get("id") or "")
    s.waybill = str(res.get("waybill_number") or "")
    s.tracking_url = (res.get("tracking_url") or "")[:300]
    s.state = "ordered"
    s.status_text = "zamówiona"
    price = money(res.get("price_gross"))
    if price is not None:
        s.price_gross = price
    s.last_error = ""


def label(integ: Integration, s: Shipment) -> bytes:
    res = call(integ, f"waybill/{s.external_id}")
    data = base64.b64decode(res.get("waybill") or "")
    if not data.startswith(b"%PDF"):
        raise IntegrationError("Apaczka nie zwróciła etykiety PDF.")
    return data


def track(integ: Integration, s: Shipment) -> None:
    if not s.waybill:
        return
    res = call(integ, f"tracking/{s.waybill}")
    events = res.get("data") or []
    if not events:
        return
    last = max(events, key=lambda e: e.get("updated_at") or "")
    s.state = TRACK_STATES.get((last.get("status") or "").upper(), s.state)
    s.status_text = (last.get("description") or last.get("status_original") or "")[:200]


def cancel(integ: Integration, s: Shipment) -> None:
    call(integ, f"cancel_order/{s.external_id}")
    s.state = "cancelled"
    s.status_text = "anulowana"


def test(integ: Integration) -> int:
    return len(services(integ))
