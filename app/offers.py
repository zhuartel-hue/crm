"""Oferty dla klientow: pozycje z cenami, waznosc i status, PDF, wysylka mailem.

Numer oferta dostaje przy pierwszym zapisie (wzor firmy pattern_offer). Z oferty jednym kliknieciem powstaje
szkic faktury VAT albo szkic WZ; mozna tez skopiowac oferte jako nowa.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import numbering, wz
from .models import OFFER_OPEN, OFFER_STATUSES, Delivery, DeliveryItem, Invoice, Offer, OfferItem, OfferTemplate

DEFAULT_VALID_DAYS = 14
EXPIRY_WARN_DAYS = 3


def new(company_id: int, actor: str = "") -> Offer:
    today = dt.date.today()
    return Offer(company_id=company_id, status="new", issue_date=today,
                 valid_until=today + dt.timedelta(days=DEFAULT_VALID_DAYS), title="", recipient_name="",
                 recipient_address="", contact_person="", email="", prices_gross=False, notes="", internal_notes="",
                 created_by=actor[:100])


def set_items(db: Session, o: Offer, names, qtys, units, product_ids, prices, rates) -> None:
    wz.set_items(db, o, names, qtys, units, product_ids, prices, rates, item_cls=OfferItem, require_price=True)


def save(db: Session, o: Offer) -> None:
    """Sprawdza oferte i nadaje numer przy pierwszym zapisie."""
    if not o.items:
        raise ValueError("Oferta nie ma pozycji.")
    if not o.recipient_name.strip():
        raise ValueError("Podaj klienta.")
    if o.valid_until is not None and o.valid_until < o.issue_date:
        raise ValueError("Data ważności nie może być wcześniejsza niż data oferty.")
    if o.probability is not None and not 0 <= o.probability <= 100:
        raise ValueError("Szansa to liczba od 0 do 100.")
    if o.number is None:
        db.flush()
        o.seq, o.number = numbering.next_number(db, o.company, "offer", o.issue_date)


def set_status(o: Offer, status: str) -> None:
    if status not in OFFER_STATUSES:
        raise ValueError("Nieznany status oferty.")
    o.status = status


def mark_sent(o: Offer) -> None:
    o.sent_at = dt.datetime.now().replace(microsecond=0)
    if o.status == "new":
        o.status = "sent"


def _accept(o: Offer) -> None:
    if o.status in OFFER_OPEN:
        o.status = "accepted"


def to_invoice(db: Session, actor: str, o: Offer) -> Invoice:
    """Szkic faktury VAT z pozycji oferty. Oferta przechodzi w status 'przyjęta'."""
    from .services import ServiceError, create_invoice_draft

    if o.invoice_id:
        raise ValueError("Z tej oferty jest już faktura.")
    items = []
    for it in o.items:
        row = dict(name=it.name, quantity=str(it.quantity), unit=it.unit, vat_rate=it.vat_rate,
                   unit_price=str(it.unit_price or 0))
        if it.product_id:
            row["product_id"] = it.product_id
        items.append(row)
    buyer = {"contractor_id": o.contractor_id} if o.contractor_id else \
        {"name": o.recipient_name, "address": o.recipient_address}
    try:
        inv = create_invoice_draft(db, actor, dict(company=o.company_id, kind="vat", buyer=buyer, items=items,
                                                   prices_gross=bool(o.prices_gross),
                                                   notes=f"Do oferty {o.number}"))
    except ServiceError as e:
        raise ValueError(str(e)) from None
    o.invoice_id = inv.id
    _accept(o)
    db.commit()
    return inv


def to_wz(db: Session, o: Offer) -> Delivery:
    """Szkic WZ z pozycji oferty (z cenami, zeby potem z WZ tez dalo sie wystawic fakture)."""
    if o.delivery_id:
        raise ValueError("Z tej oferty jest już WZ.")
    d = Delivery(company_id=o.company_id, kind="wz", status="draft", issue_date=dt.date.today(),
                 contractor_id=o.contractor_id, recipient_name=o.recipient_name,
                 recipient_address=o.recipient_address, notes=f"Do oferty {o.number}", signed_by="",
                 prices_gross=o.prices_gross)
    for it in o.items:
        d.items.append(DeliveryItem(position=it.position, product_id=it.product_id, code=it.code, name=it.name,
                                    quantity=it.quantity, unit=it.unit, unit_price=it.unit_price,
                                    vat_rate=it.vat_rate))
    wz.recalculate(d)
    db.add(d)
    db.flush()
    o.delivery_id = d.id
    _accept(o)
    db.commit()
    return d


def duplicate(o: Offer, actor: str = "") -> Offer:
    """Kopia oferty jako nowa (dzisiejsza data, ta sama dlugosc waznosci, status 'nowa')."""
    c = new(o.company_id, actor)
    if o.valid_until:
        c.valid_until = c.issue_date + (o.valid_until - o.issue_date)
    for f in ("title", "contractor_id", "recipient_name", "recipient_address", "contact_person", "email",
              "prices_gross", "notes", "probability"):
        setattr(c, f, getattr(o, f))
    for it in o.items:
        c.items.append(OfferItem(position=it.position, product_id=it.product_id, code=it.code, name=it.name,
                                 quantity=it.quantity, unit=it.unit, unit_price=it.unit_price, vat_rate=it.vat_rate))
    wz.recalculate(c)
    return c


EXPIRED_SHOW_DAYS = 7


def expiring(db: Session, company_id: int, days: int = EXPIRY_WARN_DAYS, limit: int = 8) -> list[Offer]:
    """Otwarte oferty, ktorym konczy sie waznosc albo minela w ostatnim tygodniu – do przypomnienia na Start
    (starsze, np. z archiwum Firmao, nie zasmiecaja pulpitu)."""
    today = dt.date.today()
    return list(db.scalars(select(Offer).where(
        Offer.company_id == company_id, Offer.status.in_(OFFER_OPEN), Offer.valid_until.is_not(None),
        Offer.valid_until <= today + dt.timedelta(days=days),
        Offer.valid_until >= today - dt.timedelta(days=EXPIRED_SHOW_DAYS)).order_by(Offer.valid_until).limit(limit)))


def render_pdf(o: Offer) -> bytes:
    from weasyprint import HTML

    from .pdf import HERE, _env

    html = _env.get_template("offer_pdf.html").render(o=o)
    return HTML(string=html, base_url=str(HERE)).write_pdf()


ITEM_FIELDS = ("position", "product_id", "code", "name", "quantity", "unit", "unit_price", "vat_rate")


def save_template(db: Session, o: Offer, name: str) -> OfferTemplate:
    """Wzor z oferty (bez klienta i dat). Ta sama nazwa nadpisuje istniejacy wzor."""
    name = name.strip()[:120]
    if not name:
        raise ValueError("Podaj nazwę wzoru.")
    t = db.scalar(select(OfferTemplate).where(OfferTemplate.company_id == o.company_id, OfferTemplate.name == name))
    if t is None:
        t = OfferTemplate(company_id=o.company_id, name=name)
        db.add(t)
    t.title, t.notes, t.prices_gross = o.title, o.notes, bool(o.prices_gross)
    t.valid_days = (o.valid_until - o.issue_date).days if o.valid_until and o.issue_date else None
    t.items_json = json.dumps([{f: (str(getattr(it, f)) if isinstance(getattr(it, f), Decimal) else getattr(it, f))
                                for f in ITEM_FIELDS} for it in o.items], ensure_ascii=False)
    db.commit()
    return t


def from_template(t: OfferTemplate, actor: str = "") -> Offer:
    o = new(t.company_id, actor)
    if t.valid_days is not None:
        o.valid_until = o.issue_date + dt.timedelta(days=t.valid_days)
    o.title, o.notes, o.prices_gross = t.title, t.notes, t.prices_gross
    for row in json.loads(t.items_json or "[]"):
        o.items.append(OfferItem(**{k: (Decimal(v) if k in ("quantity", "unit_price") and v is not None else v)
                                    for k, v in row.items() if k in ITEM_FIELDS}))
    wz.recalculate(o)
    return o


def templates(db: Session, company_id: int) -> list[OfferTemplate]:
    return list(db.scalars(select(OfferTemplate).where(OfferTemplate.company_id == company_id)
                           .order_by(OfferTemplate.name)))
