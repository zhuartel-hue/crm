"""Wspolne dla integracji: sekrety, zapis zamowienia z zewnatrz (bez dubli), dopasowanie klienta i produktu."""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import numbering, offer_statuses, wz
from ..models import Contractor, Integration, Order, OrderItem, Product, ProductLink
from ..secretbox import open_, seal

log = logging.getLogger("sygnavo.integrations")
FIRST_SYNC_DAYS = 30
OVERLAP = dt.timedelta(minutes=10)


class IntegrationError(Exception):
    """Blad, ktory pokazujemy uzytkownikowi (bez sekretow)."""


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def secrets_of(integ: Integration) -> dict:
    raw = open_(integ.secret) if integ.secret else ""
    try:
        v = json.loads(raw or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def store_secrets(integ: Integration, data: dict) -> None:
    integ.secret = seal(json.dumps({k: v for k, v in data.items() if v not in (None, "")}))


def update_settings(integ: Integration, **values) -> None:
    s = integ.settings
    s.update(values)
    integ.settings = s


def dec(v, default: Decimal | None = Decimal("0")) -> Decimal | None:
    if v is None or v == "":
        return default
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return default


def since(integ: Integration) -> dt.datetime:
    """Od kiedy pobierac zmiany: kursor minus zapas albo FIRST_SYNC_DAYS wstecz przy pierwszym razie."""
    if integ.cursor:
        try:
            return dt.datetime.fromisoformat(integ.cursor.replace("Z", "")) - OVERLAP
        except ValueError:
            pass
    days = int(integ.settings.get("first_days") or FIRST_SYNC_DAYS)
    return now() - dt.timedelta(days=days)


def iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat() + "Z"


def parse_time(v: str | None) -> dt.datetime | None:
    if not v:
        return None
    try:
        t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone(dt.UTC).replace(tzinfo=None) if t.tzinfo else t


@dataclass
class ExtItem:
    name: str
    quantity: Decimal
    unit_gross: Decimal
    sku: str = ""
    external_id: str = ""  # wariant w sklepie / oferta Allegro
    vat_rate: str = ""


@dataclass
class ExtOrder:
    """Zamowienie z zewnatrz w jednej postaci, niezaleznie od zrodla."""

    external_id: str
    number: str  # numer widoczny u zrodla (np. #1023 albo id Allegro)
    created: dt.datetime
    updated: dt.datetime | None = None
    external_status: str = ""
    cancelled: bool = False
    ready: bool = True  # Allegro: dane kupujacego ostateczne dopiero przy READY_FOR_PROCESSING
    payment_status: str = ""
    buyer_name: str = ""
    buyer_company: str = ""
    email: str = ""
    phone: str = ""
    tax_id: str = ""
    invoice_requested: bool = False
    billing_lines: list[str] = field(default_factory=list)
    delivery_lines: list[str] = field(default_factory=list)
    shipping_method: str = ""
    pickup_point: str = ""
    shipping_gross: Decimal = Decimal("0")
    note: str = ""
    items: list[ExtItem] = field(default_factory=list)


def source_ref(integ: Integration, external_id: str) -> str:
    return f"{integ.kind}:{integ.id}:{external_id}"[:120]


def _digits(v: str) -> str:
    return "".join(ch for ch in (v or "") if ch.isdigit())


def match_contractor(db: Session, company_id: int, e: ExtOrder) -> Contractor | None:
    """Istniejacy kontrahent firmy po NIP, potem po e-mailu. Nowych nie zakladamy (zeby nie zasmiecac bazy)."""
    nip = _digits(e.tax_id)
    if len(nip) == 10:
        c = db.scalar(select(Contractor).where(Contractor.company_id == company_id,
                                               func.replace(func.replace(Contractor.nip, "-", ""), " ", "") == nip)
                      .limit(1))
        if c is not None:
            return c
    mail = (e.email or "").strip().lower()
    if mail and "@" in mail:
        return db.scalar(select(Contractor).where(Contractor.company_id == company_id,
                                                  func.lower(Contractor.email).like(f"%{mail}%")).limit(1))
    return None


def match_product(db: Session, integ: Integration, it: ExtItem) -> Product | None:
    """Produkt z magazynu: najpierw zapisane powiazanie, potem kod produktu = SKU / sygnatura."""
    if it.external_id:
        link = db.scalar(select(ProductLink).where(ProductLink.integration_id == integ.id,
                                                   ProductLink.external_id == it.external_id))
        if link is not None and link.product_id:
            return link.product
    sku = (it.sku or "").strip()
    if not sku:
        return None
    p = db.scalar(select(Product).where(Product.company_id == integ.company_id, Product.code == sku)
                  .order_by(Product.active.desc(), Product.id).limit(1))
    if p is not None and it.external_id:
        link_product(db, integ, it.external_id, p, sku=sku, name=it.name)
    return p


def link_product(db: Session, integ: Integration, external_id: str, product: Product | None, *, sku: str = "",
                 name: str = "", stock=None, price=None, status: str = "") -> ProductLink:
    link = db.scalar(select(ProductLink).where(ProductLink.integration_id == integ.id,
                                               ProductLink.external_id == external_id))
    if link is None:
        link = ProductLink(company_id=integ.company_id, integration_id=integ.id, external_id=external_id[:80])
        db.add(link)
    if product is not None:
        link.product_id = product.id
    if sku:
        link.external_sku = sku[:120]
    if name:
        link.external_name = name[:300]
    if stock is not None:
        link.external_stock = dec(stock, None)
    if price is not None:
        link.external_price = dec(price, None)
    if status:
        link.external_status = status[:20]
    db.flush()
    return link


def _cancel_key(db: Session, company_id: int) -> str | None:
    return "cancelled" if offer_statuses.find(db, company_id, "cancelled", "order") is not None else None


def upsert_order(db: Session, integ: Integration, e: ExtOrder) -> tuple[Order, bool]:
    """Zapisuje zamowienie z zewnatrz. Nowe: pelne dane i pozycje. Istniejace: tylko status u zrodla, platnosc
    i dostawa (pozycji nie ruszamy, bo ktos mogl je juz poprawic albo wystawic fakture). Zwraca (zamowienie, nowe?)."""
    ref = source_ref(integ, e.external_id)
    o = db.scalar(select(Order).where(Order.company_id == integ.company_id, Order.source_ref == ref))
    created = o is None
    if created:
        o = Order(company_id=integ.company_id, direction="sale", status="new", issue_date=e.created.date(),
                  due_date=e.created.date() + dt.timedelta(days=int(integ.settings.get("due_days") or 2)),
                  title="", recipient_name="", recipient_address="", contact_person="", email="", phone="",
                  customer_ref="", prices_gross=True, notes="", internal_notes="", source_ref=ref,
                  created_by=integ.kind_label[:100], channel=integ.kind, integration_id=integ.id)
    o.external_status = (e.external_status or "")[:60]
    o.payment_status = e.payment_status[:20]
    o.shipping_method = e.shipping_method[:200]
    o.pickup_point = e.pickup_point[:300]
    o.delivery_address = "\n".join(x for x in e.delivery_lines if x)
    o.buyer_tax_id = e.tax_id[:30]
    o.invoice_requested = bool(e.invoice_requested)
    if created or (not o.invoice_id and not o.delivery_id):
        name = e.buyer_company or e.buyer_name or e.email or "Klient sklepu"
        o.recipient_name = name[:300]
        o.contact_person = (e.buyer_name if e.buyer_company else "")[:200]
        skip = {x.strip().lower() for x in (name, e.buyer_name, e.buyer_company) if x}
        o.recipient_address = "\n".join(x for x in (e.billing_lines or e.delivery_lines)
                                         if x and x.strip().lower() not in skip)
        o.email, o.phone = e.email[:200], e.phone[:40]
        o.customer_ref = e.number[:100]
        o.title = f"{integ.kind_label} {e.number}"[:300]
        if e.note:
            o.notes = e.note
        c = match_contractor(db, integ.company_id, e)
        o.contractor_id = c.id if c is not None else o.contractor_id
    if created:
        pos = 0
        for it in e.items:
            pos += 1
            p = match_product(db, integ, it)
            rate = it.vat_rate or (p.vat_rate if p is not None else "") or str(integ.settings.get("vat") or "23")
            o.items.append(OrderItem(position=pos, product_id=p.id if p else None, code=(p.code if p else it.sku)[:60],
                                     name=(p.name if p and integ.settings.get("use_product_names") else it.name)[:500],
                                     quantity=it.quantity, unit=(p.unit if p else "szt.")[:10],
                                     unit_price=it.unit_gross, vat_rate=rate))
        if e.shipping_gross and e.shipping_gross > 0:
            pos += 1
            o.items.append(OrderItem(position=pos, name=f"Wysyłka: {e.shipping_method or 'dostawa'}"[:500],
                                     quantity=Decimal("1"), unit="usł.", unit_price=e.shipping_gross,
                                     vat_rate=str(integ.settings.get("vat") or "23")))
        wz.recalculate(o)
        db.add(o)
        db.flush()
        o.seq, o.number = numbering.next_number(db, o.company, "order", o.issue_date)
    if e.cancelled and o.is_open and not o.invoice_id:
        key = _cancel_key(db, integ.company_id)
        if key:
            o.status = key
    return o, created
