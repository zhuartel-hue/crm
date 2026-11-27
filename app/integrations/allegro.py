"""Allegro: zamowienia (tylko odczyt) do Zamowien oraz oferty powiazane z produktami w magazynie.

Podlaczenie: aplikacja w apps.developer.allegro.pl (typ: aplikacja z przegladarka, adres powrotu
<adres Sygnavo>/integracje/allegro/powrot) -> Client ID i Client Secret w Sygnavo -> "Połącz z Allegro"
(logowanie na Allegro i zgoda). Token wazny 12 h, odswiezany sam; refresh token do 3 miesiecy, odnawia sie
przy kazdym odswiezeniu, wiec przy regularnym pobieraniu polaczenie nie wygasa.
"""
from __future__ import annotations

import base64
import datetime as dt
import secrets as pysecrets
from decimal import Decimal
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Integration, Order, Product
from . import http
from .common import (ExtItem, ExtOrder, IntegrationError, dec, iso, link_product, now, parse_time, secrets_of,
                     since, source_ref, store_secrets, upsert_order)

ENVS = {"prod": ("https://allegro.pl", "https://api.allegro.pl"),
        "sandbox": ("https://allegro.pl.allegrosandbox.pl", "https://api.allegro.pl.allegrosandbox.pl")}
ACCEPT = "application/vnd.allegro.public.v1+json"
PAGE = 100
MAX_PAGES = 50

STATUS_PL = {"BOUGHT": "kupione", "FILLED_IN": "uzupełnione", "READY_FOR_PROCESSING": "do realizacji",
             "CANCELLED": "anulowane"}
FULFILLMENT_PL = {"NEW": "nowe", "PROCESSING": "w realizacji", "READY_FOR_SHIPMENT": "do wysłania",
                  "READY_FOR_PICKUP": "do odbioru", "SENT": "wysłane", "PICKED_UP": "odebrane",
                  "CANCELLED": "anulowane", "SUSPENDED": "wstrzymane", "RETURNED": "zwrócone"}


def _env(integ: Integration) -> tuple[str, str]:
    return ENVS.get(integ.settings.get("env") or "prod", ENVS["prod"])


def _basic(sec: dict) -> str:
    raw = f"{sec.get('client_id', '')}:{sec.get('client_secret', '')}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def authorize_url(db: Session, integ: Integration, redirect_url: str) -> str:
    sec = secrets_of(integ)
    if not (sec.get("client_id") and sec.get("client_secret")):
        raise IntegrationError("Najpierw wpisz Client ID i Client Secret aplikacji Allegro.")
    integ.auth_state = pysecrets.token_urlsafe(32)
    db.flush()
    auth, _ = _env(integ)
    return f"{auth}/auth/oauth/authorize?" + urlencode({"response_type": "code", "client_id": sec["client_id"],
                                                        "redirect_uri": redirect_url, "state": integ.auth_state})


def _save_token(integ: Integration, sec: dict, body: dict) -> None:
    sec["access_token"] = body.get("access_token") or ""
    if body.get("refresh_token"):
        sec["refresh_token"] = body["refresh_token"]
    sec["expires_at"] = iso(now() + dt.timedelta(seconds=int(body.get("expires_in") or 43199)))
    sec["refresh_until"] = sec.get("refresh_until") if not body.get("refresh_token") else \
        iso(now() + dt.timedelta(days=90))
    store_secrets(integ, sec)


def finish(db: Session, integ: Integration, code: str, redirect_url: str) -> None:
    sec = secrets_of(integ)
    auth, _ = _env(integ)
    r = http.request("POST", f"{auth}/auth/oauth/token",
                     data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_url},
                     headers={"Authorization": _basic(sec)})
    if r.status_code != 200:
        raise IntegrationError(f"Allegro nie wydało tokenu (HTTP {r.status_code}). Spróbuj połączyć jeszcze raz.")
    _save_token(integ, sec, r.json())
    integ.auth_state = ""
    integ.state = "active"
    integ.last_error = ""
    me = api(db, integ, "GET", "/me")
    integ.name = (me.get("login") or integ.name or "Allegro")[:120]


def _token(db: Session, integ: Integration) -> str:
    sec = secrets_of(integ)
    if not sec.get("refresh_token") and not sec.get("access_token"):
        raise IntegrationError("Allegro nie jest połączone. Kliknij „Połącz z Allegro”.")
    if sec.get("access_token") and (sec.get("expires_at") or "") > iso(now() + dt.timedelta(minutes=5)):
        return sec["access_token"]
    auth, _ = _env(integ)
    r = http.request("POST", f"{auth}/auth/oauth/token",
                     data={"grant_type": "refresh_token", "refresh_token": sec.get("refresh_token", "")},
                     headers={"Authorization": _basic(sec)})
    if r.status_code != 200:
        integ.state = "error"
        raise IntegrationError("Zgoda Allegro wygasła albo została cofnięta. Kliknij „Połącz z Allegro” ponownie.")
    _save_token(integ, sec, r.json())
    db.flush()
    return sec["access_token"]


def api(db: Session, integ: Integration, method: str, path: str, params=None, body=None) -> dict:
    _, base = _env(integ)
    headers = {"Authorization": f"Bearer {_token(db, integ)}", "Accept": ACCEPT}
    if body is not None:
        headers["Content-Type"] = ACCEPT
    r = http.request(method, base + path, params=params, json=body, headers=headers)
    if r.status_code == 401:
        raise IntegrationError("Allegro: brak dostępu (token). Połącz ponownie.")
    if r.status_code >= 400:
        try:
            errs = r.json().get("errors") or []
            msg = "; ".join(e.get("userMessage") or e.get("message") or "" for e in errs)[:300]
        except ValueError:
            msg = ""
        raise IntegrationError(f"Allegro: błąd HTTP {r.status_code}. {msg}".strip())
    return r.json() if r.content else {}


def _lines(a: dict | None, company: str = "") -> list[str]:
    if not a:
        return []
    name = " ".join(x for x in (a.get("firstName"), a.get("lastName")) if x)
    city = f"{a.get('zipCode') or a.get('postCode') or ''} {a.get('city') or ''}".strip()
    cc = a.get("countryCode") or ""
    return [x for x in (company or a.get("companyName") or "", name, a.get("street") or "",
                        city + (f", {cc}" if cc and cc != "PL" else "")) if x]


def parse_order(f: dict) -> ExtOrder:
    buyer = f.get("buyer") or {}
    delivery = f.get("delivery") or {}
    inv = f.get("invoice") or {}
    inv_addr = inv.get("address") or {}
    company = (inv_addr.get("company") or {})
    pay = f.get("payment") or {}
    total = dec(((f.get("summary") or {}).get("totalToPay") or {}).get("amount"))
    paid = dec((pay.get("paidAmount") or {}).get("amount"), None)
    if pay.get("type") == "CASH_ON_DELIVERY":
        payment = "cod"
    elif paid is not None and paid >= total > 0:
        payment = "paid"
    elif paid:
        payment = "partial"
    else:
        payment = "unpaid"
    items = [ExtItem(name=(li.get("offer") or {}).get("name") or "Pozycja",
                     quantity=dec(li.get("quantity"), Decimal("1")),
                     unit_gross=dec((li.get("price") or {}).get("amount")),
                     sku=(((li.get("offer") or {}).get("external") or {}).get("id") or ""),
                     external_id=str((li.get("offer") or {}).get("id") or ""))
             for li in f.get("lineItems") or []]
    pp = delivery.get("pickupPoint") or {}
    pp_addr = pp.get("address") or {}
    pickup = " ".join(x for x in (pp.get("id"), pp.get("name"), pp_addr.get("street"),
                                  f"{pp_addr.get('zipCode') or ''} {pp_addr.get('city') or ''}".strip()) if x)
    status = f.get("status") or ""
    ful = (f.get("fulfillment") or {}).get("status") or ""
    buyer_name = " ".join(x for x in (buyer.get("firstName"), buyer.get("lastName")) if x) or buyer.get("login", "")
    first = min((parse_time(li.get("boughtAt")) for li in f.get("lineItems") or [] if li.get("boughtAt")),
                default=None)
    return ExtOrder(
        external_id=f["id"], number=f"{buyer.get('login') or ''} {f['id'][:8]}".strip(),
        created=first or parse_time(f.get("updatedAt")) or now(), updated=parse_time(f.get("updatedAt")),
        external_status=FULFILLMENT_PL.get(ful) or STATUS_PL.get(status, status.lower()),
        cancelled=status == "CANCELLED", ready=status == "READY_FOR_PROCESSING", payment_status=payment,
        buyer_name=buyer_name, buyer_company=company.get("name") or buyer.get("companyName") or "",
        email=buyer.get("email") or "", phone=buyer.get("phoneNumber") or (delivery.get("address") or {}).get(
            "phoneNumber") or "",
        tax_id=company.get("taxId") or "", invoice_requested=bool(inv.get("required")),
        billing_lines=_lines(inv_addr, company.get("name") or "") if inv.get("required") else _lines(buyer.get("address")
                                                                                                     and {**buyer.get("address"), "firstName": buyer.get("firstName"), "lastName": buyer.get("lastName")}),
        delivery_lines=_lines(delivery.get("address")),
        shipping_method=(delivery.get("method") or {}).get("name") or "", pickup_point=pickup,
        shipping_gross=dec((delivery.get("cost") or {}).get("amount")), note=f.get("messageToSeller") or "",
        items=items)


def sync(db: Session, integ: Integration) -> dict:
    start = since(integ)
    created = updated = 0
    newest = None
    for page in range(MAX_PAGES):
        data = api(db, integ, "GET", "/order/checkout-forms",
                   params={"updatedAt.gte": iso(start).replace("Z", ".000Z"), "limit": PAGE, "offset": page * PAGE})
        forms = data.get("checkoutForms") or []
        for f in forms:
            e = parse_order(f)
            if e.updated and (newest is None or e.updated > newest):
                newest = e.updated
            exists = db.scalar(select(Order.id).where(Order.company_id == integ.company_id,
                                                      Order.source_ref == source_ref(integ, e.external_id)))
            if exists is None and not e.ready:
                continue  # kupione, ale bez ostatecznych danych albo anulowane zanim trafilo do nas
            _, is_new = upsert_order(db, integ, e)
            created += is_new
            updated += not is_new
        if len(forms) < PAGE:
            break
    if newest is not None:
        integ.cursor = iso(newest)
    elif not integ.cursor:
        integ.cursor = iso(start)
    return {"new": created, "updated": updated}


def sync_offers(db: Session, integ: Integration) -> dict:
    """Oferty sprzedawcy -> powiazania z produktami (sygnatura oferty = kod produktu)."""
    total = linked = 0
    for page in range(20):
        data = api(db, integ, "GET", "/sale/offers", params={"limit": 1000, "offset": page * 1000})
        offers = data.get("offers") or []
        for o in offers:
            sku = ((o.get("external") or {}).get("id") or "").strip()
            p = db.scalar(select(Product).where(Product.company_id == integ.company_id, Product.code == sku)
                          .limit(1)) if sku else None
            link = link_product(db, integ, str(o["id"]), p, sku=sku, name=o.get("name") or "",
                                stock=(o.get("stock") or {}).get("available"),
                                price=((o.get("sellingMode") or {}).get("price") or {}).get("amount"),
                                status=((o.get("publication") or {}).get("status") or "").lower())
            total += 1
            linked += link.product_id is not None
        if len(offers) < 1000:
            break
    return {"offers": total, "linked": linked}


def push_stock(db: Session, integ: Integration, link) -> None:
    """Wysyla stan magazynu produktu do oferty Allegro (tylko po kliknieciu uzytkownika)."""
    if link.product is None:
        raise IntegrationError("Oferta nie jest powiązana z produktem.")
    qty = max(int(link.product.stock or 0), 0)
    api(db, integ, "PATCH", f"/sale/product-offers/{link.external_id}", body={"stock": {"available": qty,
                                                                                         "unit": "UNIT"}})
    link.external_stock = Decimal(qty)
