"""Faktury cykliczne: harmonogram i tworzenie faktur (szkic, wystawienie albo wystawienie z wysylka mailem).

W nazwach pozycji i uwagach mozna uzyc {okres} (11/2026), {miesiac} (listopad 2026) i {rok} (2026):
program wpisze okres, za ktory jest faktura.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calc, mail_templates, pdf
from .models import (PAYMENT_METHODS, RECURRING_INTERVALS, RECURRING_MODES, VAT_RATES, Contractor, EmailTemplate,
                     Invoice, MailAccount, Product, RecurringInvoice)
from .services import ServiceError, audit, create_invoice_draft, issue_invoice

log = logging.getLogger("sygnavo.recurring")
MONTHS = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień",
          "październik", "listopad", "grudzień"]


def day_in_month(year: int, month: int, day: int) -> dt.date:
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, last if day <= 0 or day > last else day)


def add_months(d: dt.date, months: int, day: int) -> dt.date:
    m = d.month - 1 + months
    return day_in_month(d.year + m // 12, m % 12 + 1, day)


def first_date(start: dt.date, day: int) -> dt.date:
    """Pierwszy termin: dzien `day` w miesiacu startu, a jesli juz minal, w nastepnym."""
    d = day_in_month(start.year, start.month, day)
    return d if d >= start else add_months(d, 1, day)


def period_ctx(d: dt.date) -> dict:
    return {"okres": f"{d:%m/%Y}", "miesiac": f"{MONTHS[d.month - 1]} {d.year}", "rok": str(d.year)}


def fill(text: str, d: dt.date) -> str:
    return mail_templates.render(text, period_ctx(d))


def items(r: RecurringInvoice) -> list[dict]:
    try:
        return json.loads(r.items_json or "[]")
    except ValueError:
        return []


def set_items(db: Session, r: RecurringInvoice, names, qtys, units, prices, rates, product_ids) -> None:
    out = []
    for k, name in enumerate(names):
        name = (name or "").strip()
        if not name:
            continue
        n = len(out) + 1
        qty = calc.parse_decimal(qtys[k] if k < len(qtys) else "1", Decimal("1"))
        price = calc.parse_decimal(prices[k] if k < len(prices) else "", None)
        if qty is None or qty <= 0:
            raise ValueError(f"Pozycja {n}: ilość musi być większa od zera.")
        if price is None:
            raise ValueError(f"Pozycja {n}: podaj cenę.")
        rate = (rates[k] if k < len(rates) else "") or "23"
        if rate not in VAT_RATES:
            raise ValueError(f"Pozycja {n}: stawka VAT jedna z {', '.join(VAT_RATES)}.")
        pid = product_ids[k] if k < len(product_ids) else ""
        product = db.get(Product, int(pid)) if str(pid).isdigit() else None
        if product is not None and product.label == name:
            name = product.name
        out.append(dict(name=name[:500], quantity=str(qty), unit=((units[k] if k < len(units) else "") or "szt.")[:10],
                        unit_price=str(price), vat_rate=rate, product_id=product.id if product else None))
    if not out:
        raise ValueError("Dodaj przynajmniej jedną pozycję.")
    r.items_json = json.dumps(out, ensure_ascii=False)


def totals(r: RecurringInvoice) -> dict:
    net = vat = gross = Decimal("0")
    for it in items(r):
        line = calc.vat_line(Decimal(it["quantity"]), Decimal(it["unit_price"]), it["vat_rate"], r.prices_gross)
        net, vat, gross = net + line.net, vat + line.vat, gross + line.gross
    return dict(net=net, vat=vat, gross=gross)


def schedule(r: RecurringInvoice, count: int = 6) -> list[dt.date]:
    out, d = [], r.next_date
    while d and len(out) < count and (r.end_date is None or d <= r.end_date):
        out.append(d)
        d = add_months(d, r.interval_months, r.day_of_month)
    return out


def validate(r: RecurringInvoice) -> None:
    if not r.name.strip():
        raise ValueError("Podaj nazwę (np. „Abonament serwisowy – Firma X”).")
    if not r.contractor_id and not r.buyer_name.strip():
        raise ValueError("Wybierz kontrahenta albo wpisz nabywcę.")
    if r.interval_months not in RECURRING_INTERVALS:
        raise ValueError("Wybierz częstotliwość.")
    if r.mode not in RECURRING_MODES:
        raise ValueError("Wybierz, co program ma robić.")
    if r.payment_method not in PAYMENT_METHODS:
        r.payment_method = "transfer"
    if r.end_date and r.end_date < r.next_date:
        raise ValueError("Data końca jest przed najbliższą fakturą.")
    if r.mode == "send" and not r.email_to.strip():
        raise ValueError("Przy wysyłce mailem podaj adres e-mail klienta.")


def generate(db: Session, actor: str, r: RecurringInvoice, advance: bool = True) -> Invoice:
    """Tworzy fakture za termin r.next_date. advance=True przesuwa harmonogram na kolejny termin."""
    when = r.next_date
    buyer = {"contractor_id": r.contractor_id} if r.contractor_id else \
        {"name": r.buyer_name, "nip": r.buyer_nip, "address": r.buyer_address}
    rows = []
    for it in items(r):
        row = dict(it, name=fill(it["name"], when))
        if not row.get("product_id"):
            row.pop("product_id", None)
        rows.append(row)
    inv = create_invoice_draft(db, actor, dict(
        company=r.company_id, kind="vat", buyer=buyer, items=rows, prices_gross=r.prices_gross,
        payment_method=r.payment_method, due_days=r.due_days, issue_date=max(when, dt.date.today()).isoformat(),
        sale_date=when.isoformat(), notes=fill(r.notes, when)))
    inv.recurring_id = r.id
    db.commit()
    if r.mode in ("issue", "send"):
        inv = issue_invoice(db, actor, inv.id)
    if r.mode == "send":
        _send(db, actor, r, inv)
    if advance:
        r.next_date = add_months(when, r.interval_months, r.day_of_month)
        if r.end_date and r.next_date > r.end_date:
            r.active = False
    r.last_error = ""
    db.commit()
    return inv


def _send(db: Session, actor: str, r: RecurringInvoice, inv: Invoice) -> None:
    from .mail import service as mail

    acc = db.scalar(select(MailAccount).where(MailAccount.company_id == r.company_id, MailAccount.active)
                    .order_by(MailAccount.id).limit(1))
    if acc is None:
        raise ServiceError("Faktura wystawiona, ale nie wysłana: firma nie ma skrzynki pocztowej (Ustawienia → Poczta).")
    tpl = db.get(EmailTemplate, r.template_id) if r.template_id else None
    tpl = tpl or mail_templates.default_for(db, r.company, "recurring") or mail_templates.default_for(db, r.company, "invoice")
    ctx = mail_templates.context(r.company, inv)
    ctx.update(period_ctx(inv.issue_date or dt.date.today()))
    name = (inv.number or f"faktura-{inv.id}").replace("/", "-") + ".pdf"
    mail.send(db, acc, r.email_to, mail_templates.render(tpl.subject, ctx), mail_templates.render(tpl.body, ctx),
              attachments=[(name, "application/pdf", pdf.render_invoice(inv))], invoice_id=inv.id)
    audit(db, actor, "mail.send", f"Faktura cykliczna {inv.number} wysłana do {r.email_to}",
          company_id=r.company_id, entity="invoice", entity_id=inv.id)


def run_due(db: Session, today: dt.date | None = None, actor: str = "system (faktury cykliczne)") -> int:
    """Tworzy zalegle faktury (za kazdy termin <= dzis). Zwraca liczbe utworzonych."""
    today = today or dt.date.today()
    made = 0
    for r in db.scalars(select(RecurringInvoice).where(RecurringInvoice.active, RecurringInvoice.next_date <= today)
                        .order_by(RecurringInvoice.id)).all():
        for _ in range(24):  # zabezpieczenie: najwyzej 2 lata zaleglosci naraz
            if not r.active or r.next_date > today:
                break
            try:
                generate(db, actor, r)
                made += 1
            except Exception as e:  # noqa: BLE001
                db.rollback()
                r = db.get(RecurringInvoice, r.id)
                r.last_error = str(e)[:1000]
                db.commit()
                log.warning("Faktura cykliczna %s: %s", r.name, e)
                break
    return made


def from_invoice(inv: Invoice) -> RecurringInvoice:
    rows = [dict(name=i.name, quantity=str(i.quantity), unit=i.unit, unit_price=str(i.unit_price), vat_rate=i.vat_rate,
                 product_id=i.product_id) for i in inv.after_items]
    due = (inv.due_date - inv.issue_date).days if inv.due_date else 14
    start = add_months(inv.issue_date, 1, inv.issue_date.day)
    contractor: Contractor | None = inv.contractor
    return RecurringInvoice(
        company_id=inv.company_id, name=f"{inv.buyer_name} – co miesiąc", contractor_id=inv.contractor_id,
        buyer_name=inv.buyer_name, buyer_nip=inv.buyer_nip, buyer_address=inv.buyer_address,
        items_json=json.dumps(rows, ensure_ascii=False), prices_gross=inv.prices_gross, payment_method=inv.payment_method,
        due_days=max(0, due), interval_months=1, day_of_month=min(inv.issue_date.day, 28), next_date=start,
        mode="draft", email_to=(contractor.email.split(",")[0].strip() if contractor and contractor.email else ""),
        notes="", active=True, last_error="")
