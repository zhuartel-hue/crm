"""Naleznosci: wplaty (takze czesciowe), lista nieoplaconych i po terminie, przypomnienia o platnosci.

Invoice.paid to suma wszystkich wplat: recznych (InvoicePayment) i z banku (BankTransaction.applied_amount).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import calc
from .models import PAYMENT_METHODS, Company, Invoice, InvoicePayment

# Rodzaje, za ktore klient placi (proforma to tylko wezwanie, korekta rozlicza sie z faktura).
PAYABLE_KINDS = ("vat", "final", "advance")
FILTERS = {"": "Wszystkie nieopłacone", "po-terminie": "Po terminie", "7-dni": "Termin w ciągu 7 dni"}


def add_payment(db: Session, inv: Invoice, amount: Decimal, date: dt.date | None = None, method: str = "transfer",
                note: str = "") -> InvoicePayment:
    if inv.status != "issued":
        raise ValueError("Wpłatę zapisuje się na wystawionej fakturze.")
    if amount is None or amount == 0:
        raise ValueError("Podaj kwotę wpłaty.")
    if amount < 0 and -amount > (inv.paid or Decimal("0")):
        raise ValueError("Zwrot większy niż dotychczasowe wpłaty.")
    if method not in PAYMENT_METHODS:
        method = "transfer"
    p = InvoicePayment(date=date or dt.date.today(), amount=calc.money(amount), method=method, note=(note or "")[:200])
    inv.payments.append(p)
    inv.paid = (inv.paid or Decimal("0")) + p.amount
    return p


def delete_payment(db: Session, inv: Invoice, payment_id: int) -> InvoicePayment:
    p = db.get(InvoicePayment, payment_id)
    if p is None or p.invoice_id != inv.id:
        raise ValueError("Nie ma takiej wpłaty.")
    inv.paid = (inv.paid or Decimal("0")) - p.amount
    inv.payments.remove(p)
    db.delete(p)
    return p


def receivables(db: Session, company: Company, filtr: str = "", q: str = "") -> list[Invoice]:
    today = dt.date.today()
    stmt = select(Invoice).where(Invoice.company_id == company.id, Invoice.status == "issued",
                                 Invoice.kind.in_(PAYABLE_KINDS), Invoice.paid < Invoice.total_gross)
    if filtr == "po-terminie":
        stmt = stmt.where(Invoice.due_date < today)
    elif filtr == "7-dni":
        stmt = stmt.where(Invoice.due_date >= today, Invoice.due_date <= today + dt.timedelta(days=7))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Invoice.number.ilike(like), Invoice.buyer_name.ilike(like), Invoice.buyer_nip.ilike(like)))
    return list(db.scalars(stmt.order_by(Invoice.due_date.is_(None), Invoice.due_date, Invoice.id)))


def summary(rows: list[Invoice]) -> dict:
    overdue = [i for i in rows if i.days_overdue > 0]
    return dict(count=len(rows), total=sum((i.to_pay for i in rows), Decimal("0")),
                overdue_count=len(overdue), overdue_total=sum((i.to_pay for i in overdue), Decimal("0")))


def reminder_draft(inv: Invoice) -> tuple[str, str]:
    c = inv.company
    subject = f"Przypomnienie o płatności: {inv.kind_label} {inv.display_number} – {c.name}"
    lines = ["Dzień dobry,", "",
             f"uprzejmie przypominamy o płatności za {inv.kind_label.lower()} {inv.display_number}"
             f" z dnia {inv.issue_date:%d.%m.%Y}."]
    lines.append(f"Pozostało do zapłaty: {calc.format_money(inv.to_pay)} zł"
                 + (f" (z {calc.format_money(inv.total_gross)} zł)" if inv.paid else "") + ".")
    if inv.due_date:
        days = inv.days_overdue
        lines.append(f"Termin płatności: {inv.due_date:%d.%m.%Y}" + (f", czyli {days} dni temu." if days else "."))
    if c.bank_account:
        lines.append(f"Numer konta: {c.bank_account}" + (f" ({c.bank_name})" if c.bank_name else "") + ".")
    lines.append(f"W tytule przelewu prosimy wpisać numer faktury: {inv.display_number}.")
    lines += ["", "Jeśli płatność została już wysłana, prosimy potraktować tę wiadomość jako nieaktualną.", "",
              "Pozdrawiam", c.name]
    return subject, "\n".join(lines)


def record_reminder(inv: Invoice) -> None:
    inv.reminder_count = (inv.reminder_count or 0) + 1
    inv.last_reminder_at = dt.datetime.now(ZoneInfo("Europe/Warsaw")).replace(tzinfo=None)
