"""Zamowienia od klientow (i do dostawcow): pozycje, termin realizacji, status edytowalny jak w ofertach.

Numer zamowienie dostaje przy pierwszym zapisie (wzor jak w Firmao: 2026/11/001/Z/S). Zamowienie powstaje recznie
albo jednym kliknieciem z oferty; z zamowienia powstaje szkic faktury VAT albo szkic WZ.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import numbering, wz
from .models import Delivery, DeliveryItem, Invoice, Offer, Order, OrderItem

DEFAULT_DUE_DAYS = 7


def new(company_id: int, actor: str = "") -> Order:
    today = dt.date.today()
    return Order(company_id=company_id, direction="sale", status="new", issue_date=today,
                 due_date=today + dt.timedelta(days=DEFAULT_DUE_DAYS), title="", recipient_name="", recipient_address="",
                 contact_person="", email="", phone="", customer_ref="", prices_gross=False, notes="",
                 internal_notes="", created_by=actor[:100])


def set_items(db: Session, o: Order, names, qtys, units, product_ids, prices, rates) -> None:
    wz.set_items(db, o, names, qtys, units, product_ids, prices, rates, item_cls=OrderItem)


def save(db: Session, o: Order) -> None:
    if not o.items:
        raise ValueError("Zamówienie nie ma pozycji.")
    if not o.recipient_name.strip():
        raise ValueError("Podaj klienta." if o.direction == "sale" else "Podaj dostawcę.")
    if o.due_date is not None and o.due_date < o.issue_date:
        raise ValueError("Termin realizacji nie może być wcześniejszy niż data zamówienia.")
    wz.recalculate(o)
    if o.number is None:
        db.flush()
        o.seq, o.number = numbering.next_number(db, o.company, "order", o.issue_date)


def set_status(o: Order, status: str) -> None:
    from sqlalchemy.orm import object_session

    from . import offer_statuses
    if offer_statuses.find(object_session(o), o.company_id, status, "order") is None:
        raise ValueError("Nieznany status zamówienia.")
    o.status = status


def from_offer(o: Offer, actor: str = "") -> Order:
    """Nowe zamowienie z pozycji oferty (oferta zostaje, dostaje status 'zamówiona')."""
    z = new(o.company_id, actor)
    for f in ("contractor_id", "recipient_name", "recipient_address", "contact_person", "email", "prices_gross"):
        setattr(z, f, getattr(o, f))
    z.title = o.title or (o.heading or "")
    z.offer_id = o.id
    z.notes = o.notes
    for it in o.items:
        z.items.append(OrderItem(position=it.position, product_id=it.product_id, code=it.code, name=it.name,
                                 quantity=it.quantity, unit=it.unit, unit_price=it.unit_price, vat_rate=it.vat_rate))
    wz.recalculate(z)
    return z


def _move_on(o: Order) -> None:
    if o.status in ("new", "sent", "confirmed"):
        o.status = "in_progress"


def to_invoice(db: Session, actor: str, o: Order) -> Invoice:
    from .services import ServiceError, create_invoice_draft

    if o.invoice_id:
        raise ValueError("Z tego zamówienia jest już faktura.")
    if o.direction != "sale":
        raise ValueError("Fakturę sprzedaży tworzysz z zamówienia od klienta.")
    items = []
    for it in o.items:
        if it.unit_price is None:
            raise ValueError(f"Pozycja „{it.name}” nie ma ceny. Uzupełnij cenę w zamówieniu.")
        row = dict(name=it.name, quantity=str(it.quantity), unit=it.unit, vat_rate=it.vat_rate,
                   unit_price=str(it.unit_price))
        if it.product_id:
            row["product_id"] = it.product_id
        items.append(row)
    buyer = {"contractor_id": o.contractor_id} if o.contractor_id else \
        {"name": o.recipient_name, "address": o.recipient_address}
    try:
        inv = create_invoice_draft(db, actor, dict(company=o.company_id, kind="vat", buyer=buyer, items=items,
                                                   prices_gross=bool(o.prices_gross),
                                                   notes=f"Do zamówienia {o.number}"
                                                   + (f" (zamówienie klienta {o.customer_ref})" if o.customer_ref else "")))
    except ServiceError as e:
        raise ValueError(str(e)) from None
    o.invoice_id = inv.id
    _move_on(o)
    db.commit()
    return inv


def to_wz(db: Session, o: Order) -> Delivery:
    if o.delivery_id:
        raise ValueError("Z tego zamówienia jest już WZ.")
    kind = "wz" if o.direction == "sale" else "pz"
    d = Delivery(company_id=o.company_id, kind=kind, status="draft", issue_date=dt.date.today(),
                 contractor_id=o.contractor_id, recipient_name=o.recipient_name,
                 recipient_address=o.recipient_address, notes=f"Do zamówienia {o.number}", signed_by="",
                 prices_gross=o.prices_gross)
    for it in o.items:
        d.items.append(DeliveryItem(position=it.position, product_id=it.product_id, code=it.code, name=it.name,
                                    quantity=it.quantity, unit=it.unit, unit_price=it.unit_price,
                                    vat_rate=it.vat_rate))
    wz.recalculate(d)
    db.add(d)
    db.flush()
    o.delivery_id = d.id
    _move_on(o)
    db.commit()
    return d


def duplicate(o: Order, actor: str = "") -> Order:
    c = new(o.company_id, actor)
    if o.due_date:
        c.due_date = c.issue_date + (o.due_date - o.issue_date)
    for f in ("direction", "title", "contractor_id", "recipient_name", "recipient_address", "contact_person", "email",
              "phone", "prices_gross", "notes"):
        setattr(c, f, getattr(o, f))
    for it in o.items:
        c.items.append(OrderItem(position=it.position, product_id=it.product_id, code=it.code, name=it.name,
                                 quantity=it.quantity, unit=it.unit, unit_price=it.unit_price, vat_rate=it.vat_rate))
    wz.recalculate(c)
    return c


def due_soon(db: Session, company_id: int, days: int = 3, limit: int = 8) -> list[Order]:
    """Otwarte zamowienia z terminem w ciagu `days` dni albo po terminie (kafelek na Start)."""
    from . import offer_statuses
    open_keys = offer_statuses.keys(db, company_id, "open", "order")
    limit_date = dt.date.today() + dt.timedelta(days=days)
    return list(db.scalars(select(Order).where(Order.company_id == company_id, Order.status.in_(open_keys),
                                               Order.due_date.is_not(None), Order.due_date <= limit_date)
                           .order_by(Order.due_date).limit(limit)))
