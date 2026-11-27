"""Import z Firmao: klienci i produkty z plikow JSON (eksport tools/firmao_export.py albo odczyt przez connector).

Rekord pamieta zrodlo (source_ref = "firmao:<id>"), wiec ponowny import aktualizuje zamiast dublowac.
Kontrahenta bez zrodla dopasowujemy po NIP, produkt po kodzie. Nic nie jest wysylane do Firmao.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import VAT_RATES, Contact, Contractor, Product

COUNTRIES = {"polska": "PL", "poland": "PL", "niemcy": "DE", "germany": "DE", "czechy": "CZ", "litwa": "LT",
             "ukraina": "UA", "wielka brytania": "GB", "holandia": "NL", "francja": "FR", "włochy": "IT"}
_POSTAL = re.compile(r"\b(\d{2}-\d{3})\s+([^,]+)")


def load(raw: bytes) -> list[dict]:
    """Plik JSON: lista rekordow albo koperta Firmao {"date": [...]} / {"data": [...]}."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"To nie jest poprawny plik JSON ({e}).") from None
    if isinstance(data, dict):
        data = data.get("date") or data.get("data") or data.get("result") or []
        if isinstance(data, str):
            return load(data.encode())
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError("Plik nie zawiera listy rekordów.")
    return data


def _first(v) -> str:
    """Pierwszy adres/telefon z pola, ktore bywa tekstem, lista tekstow albo lista obiektow."""
    if isinstance(v, list):
        for x in v:
            s = _first(x)
            if s:
                return s
        return ""
    if isinstance(v, dict):
        return _first(v.get("email") or v.get("value") or v.get("number") or v.get("phone") or "")
    return str(v or "").strip()


def _country(v) -> str:
    s = str(v or "").strip()
    if len(s) == 2:
        return s.upper()
    return COUNTRIES.get(s.lower(), "PL")


def _address(a: dict | None) -> tuple[str, str, str, str]:
    a = a or {}
    street, postal, city = (a.get("street") or "").strip(), (a.get("postCode") or "").strip(), (a.get("city") or "").strip()
    if street and not city:
        # Firmao czasem trzyma caly adres w jednym polu: "STREFOWA 15, 39-442 TARNOBRZEG, ..."
        m = _POSTAL.search(street)
        if m:
            postal, city = m.group(1), m.group(2).strip()
            street = street[:m.start()].strip(" ,")
    return street[:200], postal[:10], city[:100], _country(a.get("country"))


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _vat(v) -> str:
    if v is None or v == "":
        return "23"
    try:
        s = str(int(Decimal(str(v))))
    except (InvalidOperation, ValueError):
        s = str(v).lower()
    return s if s in VAT_RATES else "23"


def import_customers(db: Session, rows: list[dict], company_id: int) -> dict:
    mine = select(Contractor).where(Contractor.company_id == company_id)
    by_ref = {c.source_ref: c for c in db.scalars(mine.where(Contractor.source_ref != ""))}
    by_nip = {c.nip: c for c in db.scalars(mine.where(Contractor.nip != ""))}
    out = {"added": 0, "updated": 0, "skipped": 0}
    for r in rows:
        if r.get("deleted") or not r.get("id"):
            out["skipped"] += 1
            continue
        name = (r.get("name") or r.get("label") or "").strip()
        if not name:
            out["skipped"] += 1
            continue
        ref = f"firmao:{r['id']}"
        nip = re.sub(r"[^0-9A-Za-z]", "", str(r.get("nipNumber") or ""))[:20]
        c = by_ref.get(ref) or (by_nip.get(nip) if nip else None)
        if c is None:
            c = Contractor(company_id=company_id, name=name, notes="")
            db.add(c)
            out["added"] += 1
        else:
            out["updated"] += 1
        street, postal, city, country = _address(r.get("officeAddress") or r.get("correspondenceAddress"))
        c.name, c.source_ref = name[:300], ref
        c.is_company = not bool(r.get("naturalPerson"))
        c.nip = nip or c.nip or ""
        c.street, c.postal_code, c.city, c.country = street or c.street or "", postal or c.postal_code or "", \
            city or c.city or "", country
        c.email = (_first(r.get("emails")) or _first(r.get("email")) or c.email or "")[:200]
        c.phone = (_first(r.get("phones")) or _first(r.get("phoneNumber")) or _first(r.get("phone")) or c.phone or "")[:40]
        desc = (r.get("description") or "").strip()
        if desc and desc not in (c.notes or ""):
            c.notes = ((c.notes + "\n") if c.notes else "") + desc
        by_ref[ref] = c
        if nip:
            by_nip[nip] = c
    db.flush()
    return out


def _price_group(r: dict) -> dict:
    g = r.get("salePriceGroupA")
    if isinstance(g, dict):
        return g
    for g in r.get("salePrices") or r.get("salePriceGroups") or []:
        if isinstance(g, dict):
            return g
    return {}


def import_products(db: Session, rows: list[dict], company_id: int) -> dict:
    mine = select(Product).where(Product.company_id == company_id)
    by_ref = {p.source_ref: p for p in db.scalars(mine.where(Product.source_ref != ""))}
    by_code = {p.code: p for p in db.scalars(mine.where(Product.code != ""))}
    out = {"added": 0, "updated": 0, "skipped": 0}
    for r in rows:
        if r.get("deleted") or r.get("isGroup") or not r.get("id") or not (r.get("name") or "").strip():
            out["skipped"] += 1
            continue
        ref = f"firmao:{r['id']}"
        code = (r.get("productCode") or "").strip()[:60]
        p = by_ref.get(ref) or (by_code.get(code) if code else None)
        if p is None:
            p = Product(company_id=company_id, name="", description="")
            db.add(p)
            out["added"] += 1
        else:
            out["updated"] += 1
        g = _price_group(r)
        p.name, p.code, p.source_ref = r["name"].strip()[:300], code or p.code or "", ref
        p.unit = (r.get("salePriceUnit") or g.get("unit") or p.unit or "szt.")[:20]
        net = _dec(r.get("salePriceNet") if r.get("salePriceNet") is not None else g.get("netPrice"))
        p.price_net = net if net is not None else p.price_net
        p.vat_rate = _vat(r.get("salePriceVat") if r.get("salePriceVat") is not None else g.get("vat"))
        p.ean = (r.get("ean") or p.ean or "")[:20]
        p.description = (r.get("description") or p.description or "").strip()
        p.is_service = r.get("product") is False
        p.active = r.get("saleable", True) is not False
        stock = r.get("totalStoreState")
        if stock is not None and not p.is_service:
            p.stock = Decimal(str(stock))
        by_ref[ref] = p
        if code:
            by_code[code] = p
    db.flush()
    return out


def import_contacts(db: Session, rows: list[dict], company_id: int) -> dict:
    """Osoby kontaktowe; firme wskazuje customer.id z Firmao (klientow importuj wczesniej albo razem)."""
    by_ref = {p.source_ref: p for p in db.scalars(select(Contact).where(Contact.company_id == company_id,
                                                                     Contact.source_ref != ""))}
    firms = {c.source_ref: c.id for c in db.scalars(select(Contractor).where(Contractor.company_id == company_id,
                                                                            Contractor.source_ref.like("firmao:%")))}
    out = {"added": 0, "updated": 0, "skipped": 0}
    for r in rows:
        if r.get("deleted") or not r.get("id"):
            out["skipped"] += 1
            continue
        first, last = (r.get("firstName") or "").strip(), (r.get("lastName") or "").strip()
        email = _first(r.get("emails")) or _first(r.get("email"))
        if not (first or last or email):
            out["skipped"] += 1
            continue
        ref = f"firmao:{r['id']}"
        p = by_ref.get(ref)
        if p is None:
            p = Contact(company_id=company_id, notes="")
            db.add(p)
            out["added"] += 1
        else:
            out["updated"] += 1
        cust = r.get("customer")
        cust_id = cust.get("id") if isinstance(cust, dict) else cust
        phones = [x for x in ([_first(x) for x in r.get("phones") or []] or [_first(r.get("phone"))]) if x]
        p.source_ref, p.first_name, p.last_name = ref, first[:100], last[:100]
        p.position = (r.get("position") or p.position or "")[:150]
        p.email = (email or p.email or "")[:200]
        p.phone = (phones[0] if phones else p.phone or "")[:40]
        p.phone2 = (phones[1] if len(phones) > 1 else p.phone2 or "")[:40]
        desc = (r.get("description") or "").strip()
        if desc and desc not in (p.notes or ""):
            p.notes = ((p.notes + "\n") if p.notes else "") + desc
        if cust_id:
            p.contractor_id = firms.get(f"firmao:{cust_id}", p.contractor_id)
        by_ref[ref] = p
    db.flush()
    return out
