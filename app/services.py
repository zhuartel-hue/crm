"""Operacje wspolne dla API i serwera MCP (Claude). Kazda zmiana trafia do dziennika."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import calc, numbering
from .models import (
    CAR_STATUSES,
    INVOICE_KINDS,
    PAYMENT_METHODS,
    VAT_RATES,
    AuditLog,
    Car,
    Company,
    Contractor,
    Invoice,
    InvoiceItem,
)


class ServiceError(ValueError):
    """Blad, ktory pokazujemy uzytkownikowi (po polsku)."""


def audit(db: Session, actor: str, action: str, summary: str, *, company_id=None, entity="", entity_id=None) -> None:
    db.add(AuditLog(actor=actor, action=action, summary=summary, company_id=company_id, entity=entity, entity_id=entity_id))


def _money(v) -> str:
    return f"{calc.money(v):.2f}" if v is not None else None


def _dec(value, field: str, default=None) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        return calc.parse_decimal(str(value), default)
    except ValueError:
        raise ServiceError(f"Pole {field}: niepoprawna liczba {value!r}.")


def _date(value, field: str, default=None) -> dt.date | None:
    if not value:
        return default
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ServiceError(f"Pole {field}: data w formacie RRRR-MM-DD, otrzymano {value!r}.")


# --- firmy ----------------------------------------------------------------


def company_dict(c: Company) -> dict:
    return dict(id=c.id, short_name=c.short_name, name=c.name, nip=c.nip, city=c.city)


def list_companies(db: Session) -> list[dict]:
    return [company_dict(c) for c in db.scalars(select(Company).order_by(Company.id))]


def resolve_company(db: Session, ref) -> Company:
    """Firma po id albo nazwie (wystarczy fragment: "margot", "ari")."""
    companies = db.scalars(select(Company).order_by(Company.id)).all()
    if ref is None or ref == "":
        raise ServiceError("Podaj firmę: " + ", ".join(c.short_name for c in companies) + ".")
    if isinstance(ref, int) or str(ref).isdigit():
        for c in companies:
            if c.id == int(ref):
                return c
    text = str(ref).strip().lower()
    hits = [c for c in companies if text in c.short_name.lower() or text in c.name.lower()]
    if len(hits) == 1:
        return hits[0]
    raise ServiceError(f"Nie rozpoznaję firmy {ref!r}. Dostępne: " + ", ".join(c.short_name for c in companies) + ".")


# --- kontrahenci ------------------------------------------------------------


def contractor_dict(c: Contractor) -> dict:
    return dict(id=c.id, name=c.name, nip=c.nip, is_company=c.is_company, street=c.street,
                postal_code=c.postal_code, city=c.city, email=c.email, phone=c.phone)


def search_contractors(db: Session, query: str = "", limit: int = 20) -> list[dict]:
    stmt = select(Contractor)
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(or_(Contractor.name.ilike(like), Contractor.nip.ilike(like.replace("-", "")),
                              Contractor.city.ilike(like), Contractor.phone.ilike(like), Contractor.email.ilike(like)))
    return [contractor_dict(c) for c in db.scalars(stmt.order_by(Contractor.name).limit(min(limit, 100)))]


def create_contractor(db: Session, actor: str, data: dict) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise ServiceError("Podaj nazwę albo imię i nazwisko kontrahenta.")
    nip = (data.get("nip") or "").replace("-", "").replace(" ", "")
    c = Contractor(
        name=name, nip=nip, is_company=bool(data.get("is_company", bool(nip))),
        street=(data.get("street") or "").strip(), postal_code=(data.get("postal_code") or "").strip(),
        city=(data.get("city") or "").strip(), email=(data.get("email") or "").strip(),
        phone=(data.get("phone") or "").strip(), notes=(data.get("notes") or "").strip(),
    )
    db.add(c)
    db.flush()
    audit(db, actor, "contractor.create", f"Nowy kontrahent {c.name}", entity="contractor", entity_id=c.id)
    db.commit()
    return contractor_dict(c)


# --- auta -----------------------------------------------------------------


def car_dict(c: Car) -> dict:
    return dict(
        id=c.id, company=c.company.short_name, label=c.label, make=c.make, model=c.model, vin=c.vin, year=c.year,
        mileage_km=c.mileage_km, registration=c.registration, purchase_price=_money(c.purchase_price),
        asking_price=_money(c.asking_price), status=c.status, status_label=c.status_label,
        purchase_date=c.purchase_date.isoformat() if c.purchase_date else None,
    )


def search_cars(db: Session, company=None, query: str = "", status: str = "", limit: int = 20) -> list[dict]:
    stmt = select(Car)
    if company not in (None, ""):
        stmt = stmt.where(Car.company_id == resolve_company(db, company).id)
    if status:
        if status not in CAR_STATUSES:
            raise ServiceError("Status auta: " + ", ".join(CAR_STATUSES) + ".")
        stmt = stmt.where(Car.status == status)
    if query:
        for word in query.split():
            like = f"%{word}%"
            stmt = stmt.where(or_(Car.make.ilike(like), Car.model.ilike(like), Car.vin.ilike(like),
                                  Car.registration.ilike(like)))
    return [car_dict(c) for c in db.scalars(stmt.order_by(Car.id.desc()).limit(min(limit, 100)))]


def create_car(db: Session, actor: str, data: dict) -> dict:
    company = resolve_company(db, data.get("company"))
    make = (data.get("make") or "").strip()
    if not make:
        raise ServiceError("Podaj markę auta.")
    vin = (data.get("vin") or "").strip().upper()
    if vin and len(vin) != 17:
        raise ServiceError("VIN powinien mieć 17 znaków.")
    car = Car(
        company_id=company.id, make=make, model=(data.get("model") or "").strip(), vin=vin,
        year=int(data["year"]) if data.get("year") else None,
        mileage_km=int(data["mileage_km"]) if data.get("mileage_km") not in (None, "") else None,
        registration=(data.get("registration") or "").strip().upper(),
        purchase_date=_date(data.get("purchase_date"), "purchase_date"),
        purchase_price=_dec(data.get("purchase_price"), "purchase_price", Decimal("0")),
        purchase_document=(data.get("purchase_document") or "").strip(),
        asking_price=_dec(data.get("asking_price"), "asking_price"),
        status="available", notes=(data.get("notes") or "").strip(),
    )
    db.add(car)
    db.flush()
    car.company = company
    audit(db, actor, "car.create", f"Nowe auto {car.label} {car.vin}", company_id=company.id, entity="car", entity_id=car.id)
    db.commit()
    return car_dict(car)


# --- faktury --------------------------------------------------------------


def car_item_name(car: Car) -> str:
    parts = [f"Samochód {car.label}".strip()]
    if car.vin:
        parts.append(f"VIN: {car.vin}")
    if car.year:
        parts.append(f"rok produkcji: {car.year}")
    if car.mileage_km is not None:
        parts.append(f"przebieg: {car.mileage_km:,} km".replace(",", " "))
    if car.registration:
        parts.append(f"nr rej.: {car.registration}")
    return ", ".join(parts)


def invoice_dict(inv: Invoice, base_url: str = "", with_items: bool = True) -> dict:
    d = dict(
        id=inv.id, company=inv.company.short_name, kind=inv.kind, kind_label=inv.kind_label, status=inv.status,
        number=inv.number, issue_date=inv.issue_date.isoformat(), sale_date=inv.sale_date.isoformat(),
        due_date=inv.due_date.isoformat() if inv.due_date else None, payment_method=inv.payment_method,
        buyer=dict(contractor_id=inv.contractor_id, name=inv.buyer_name, nip=inv.buyer_nip, address=inv.buyer_address),
        car_id=inv.car_id, total_net=_money(inv.total_net), total_vat=_money(inv.total_vat),
        total_gross=_money(inv.total_gross), paid=_money(inv.paid), to_pay=_money(inv.to_pay), notes=inv.notes,
    )
    if inv.kind == "margin":
        d["margin_total"] = _money(inv.margin_total)
        d["vat_from_margin"] = _money(inv.total_vat)
    if base_url:
        d["url"] = f"{base_url}/faktury/{inv.id}"
        d["pdf_url"] = f"{base_url}/faktury/{inv.id}/pdf"
    if with_items:
        d["items"] = [
            dict(name=i.name, quantity=str(i.quantity.normalize()), unit=i.unit, unit_price=_money(i.unit_price),
                 vat_rate=i.vat_rate, net=_money(i.net), vat=_money(i.vat), gross=_money(i.gross),
                 **({"purchase_price": _money(i.purchase_price), "margin": _money(i.margin)} if inv.kind == "margin" else {}))
            for i in inv.items
        ]
    return d


def get_invoice(db: Session, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, int(invoice_id))
    if inv is None:
        raise ServiceError(f"Nie ma faktury o id {invoice_id}.")
    return inv


def list_invoices(db: Session, company=None, kind: str = "", month: str = "", status: str = "",
                  unpaid_only: bool = False, query: str = "", limit: int = 30) -> list[Invoice]:
    stmt = select(Invoice)
    if company not in (None, ""):
        stmt = stmt.where(Invoice.company_id == resolve_company(db, company).id)
    if kind:
        if kind not in INVOICE_KINDS:
            raise ServiceError("Rodzaj faktury: vat albo margin.")
        stmt = stmt.where(Invoice.kind == kind)
    if month:
        try:
            y, m = (int(x) for x in month.split("-"))
            start = dt.date(y, m, 1)
        except ValueError:
            raise ServiceError("Miesiąc w formacie RRRR-MM.")
        end = dt.date(y + (m == 12), m % 12 + 1, 1)
        stmt = stmt.where(Invoice.issue_date >= start, Invoice.issue_date < end)
    if status:
        stmt = stmt.where(Invoice.status == status)
    if unpaid_only:
        stmt = stmt.where(Invoice.status == "issued", Invoice.paid < Invoice.total_gross)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(Invoice.number.ilike(like), Invoice.buyer_name.ilike(like), Invoice.buyer_nip.ilike(like)))
    return db.scalars(stmt.order_by(Invoice.issue_date.desc(), Invoice.id.desc()).limit(min(limit, 200))).all()


def create_invoice_draft(db: Session, actor: str, data: dict) -> Invoice:
    """Szkic faktury. Numer nadaje dopiero issue_invoice."""
    company = resolve_company(db, data.get("company"))
    kind = data.get("kind") or "vat"
    if kind not in INVOICE_KINDS:
        raise ServiceError("Rodzaj faktury: vat albo margin (VAT marża).")
    today = dt.date.today()
    issue_date = _date(data.get("issue_date"), "issue_date", today)
    payment = data.get("payment_method") or "transfer"
    if payment not in PAYMENT_METHODS:
        raise ServiceError("Płatność: " + ", ".join(PAYMENT_METHODS) + ".")
    due_days = data.get("due_days")
    due_days = company.default_payment_days if due_days in (None, "") else int(due_days)

    inv = Invoice(
        company_id=company.id, kind=kind, status="draft", issue_date=issue_date,
        sale_date=_date(data.get("sale_date"), "sale_date", issue_date),
        issue_place=company.issue_place or company.city, payment_method=payment,
        due_date=issue_date + dt.timedelta(days=due_days),
        prices_gross=True if kind == "margin" else bool(data.get("prices_gross", True)),
        paid=Decimal("0"), notes=(data.get("notes") or "").strip(),
        buyer_name="", buyer_nip="", buyer_address="",
    )
    inv.company = company

    # Nabywca: istniejacy kontrahent albo dane wpisane.
    buyer = data.get("buyer") or {}
    if buyer.get("contractor_id"):
        c = db.get(Contractor, int(buyer["contractor_id"]))
        if c is None:
            raise ServiceError(f"Nie ma kontrahenta o id {buyer['contractor_id']}.")
        inv.contractor_id = c.id
        inv.buyer_name, inv.buyer_nip, inv.buyer_address = c.name, c.nip, "\n".join(c.address_lines)
    else:
        inv.buyer_name = (buyer.get("name") or "").strip()
        inv.buyer_nip = (buyer.get("nip") or "").replace("-", "").replace(" ", "")
        inv.buyer_address = (buyer.get("address") or "").strip()
        if not inv.buyer_name:
            raise ServiceError("Podaj nabywcę: contractor_id albo name (i ewentualnie nip, address).")
        if buyer.get("save_as_contractor"):
            lines = inv.buyer_address.splitlines()
            postal, _, city = (lines[1] if len(lines) > 1 else "").partition(" ")
            c = Contractor(name=inv.buyer_name, nip=inv.buyer_nip, is_company=bool(inv.buyer_nip),
                           street=lines[0] if lines else "", postal_code=postal, city=city)
            db.add(c)
            db.flush()
            inv.contractor_id = c.id

    # Auto: pozycja z danymi auta, cena nabycia z karty auta.
    car = None
    if data.get("car_id"):
        car = db.get(Car, int(data["car_id"]))
        if car is None or car.company_id != company.id:
            raise ServiceError(f"Auto {data['car_id']} nie należy do firmy {company.short_name}.")
        if car.status == "sold":
            raise ServiceError(f"{car.label} ({car.vin}) jest już sprzedane.")
        inv.car_id = car.id
        inv.car = car

    items = data.get("items") or []
    if not items and car is not None:
        price = _dec(data.get("price"), "price", car.asking_price)
        if price is None:
            raise ServiceError("Auto nie ma ceny sprzedaży: podaj price (brutto).")
        items = [dict(name=car_item_name(car), quantity=1, unit="szt.", unit_price=price, vat_rate="23",
                      purchase_price=car.purchase_price)]
    if not items:
        raise ServiceError("Podaj pozycje (items) albo car_id.")
    inv.items = []
    for n, it in enumerate(items, start=1):
        name = (it.get("name") or "").strip()
        if not name:
            raise ServiceError(f"Pozycja {n}: brak nazwy.")
        rate = str(it.get("vat_rate") or "23")
        if rate not in VAT_RATES:
            raise ServiceError(f"Pozycja {n}: stawka VAT jedna z {', '.join(VAT_RATES)}.")
        price = _dec(it.get("unit_price"), f"items[{n}].unit_price")
        if price is None:
            raise ServiceError(f"Pozycja {n}: brak ceny.")
        purchase = _dec(it.get("purchase_price"), f"items[{n}].purchase_price")
        if kind == "margin" and purchase is None:
            if car is not None and n == 1:
                purchase = car.purchase_price
            else:
                raise ServiceError(f"Pozycja {n}: faktura marża wymaga ceny nabycia (purchase_price).")
        inv.items.append(InvoiceItem(
            name=name, quantity=_dec(it.get("quantity"), "quantity", Decimal("1")), unit=it.get("unit") or "szt.",
            unit_price=price, vat_rate=rate, purchase_price=purchase,
        ))
    calc.recalculate(inv)
    db.add(inv)
    db.flush()
    audit(db, actor, "invoice.draft", f"Szkic {inv.kind_label} dla {inv.buyer_name} na {calc.format_money(inv.total_gross)} zł",
          company_id=company.id, entity="invoice", entity_id=inv.id)
    db.commit()
    return inv


def issue_invoice(db: Session, actor: str, invoice_id: int) -> Invoice:
    inv = get_invoice(db, invoice_id)
    if inv.status == "issued":
        raise ServiceError(f"Faktura {inv.number} jest już wystawiona.")
    try:
        numbering.issue(db, inv)
    except ValueError as e:
        db.rollback()
        raise ServiceError(str(e))
    audit(db, actor, "invoice.issue", f"Wystawiono {inv.number} dla {inv.buyer_name} na {calc.format_money(inv.total_gross)} zł",
          company_id=inv.company_id, entity="invoice", entity_id=inv.id)
    db.commit()
    return inv


def mark_paid(db: Session, actor: str, invoice_id: int, amount=None) -> Invoice:
    inv = get_invoice(db, invoice_id)
    if inv.status != "issued":
        raise ServiceError("Płatność można zapisać tylko na wystawionej fakturze.")
    paid = inv.total_gross if amount in (None, "") else _dec(amount, "amount")
    if paid < 0:
        raise ServiceError("Kwota nie może być ujemna.")
    inv.paid = paid
    audit(db, actor, "invoice.paid", f"{inv.number}: zapłacono {calc.format_money(paid)} zł",
          company_id=inv.company_id, entity="invoice", entity_id=inv.id)
    db.commit()
    return inv


def sales_summary(db: Session, company, month: str) -> dict:
    c = resolve_company(db, company)
    invoices = [i for i in list_invoices(db, company=c.id, month=month, status="issued", limit=200)]
    vat = [i for i in invoices if i.kind == "vat"]
    margin = [i for i in invoices if i.kind == "margin"]
    s = lambda rows, attr: _money(sum((getattr(i, attr) for i in rows), Decimal("0")))  # noqa: E731
    unpaid = db.scalar(
        select(func.count()).select_from(Invoice).where(
            Invoice.company_id == c.id, Invoice.status == "issued", Invoice.paid < Invoice.total_gross)
    )
    return dict(
        company=c.short_name, month=month, invoices=len(invoices),
        vat_invoices=dict(count=len(vat), net=s(vat, "total_net"), vat=s(vat, "total_vat"), gross=s(vat, "total_gross")),
        margin_invoices=dict(count=len(margin), gross=s(margin, "total_gross"), margin=s(margin, "margin_total"),
                             vat_from_margin=s(margin, "total_vat")),
        unpaid_invoices_all_time=unpaid,
    )
