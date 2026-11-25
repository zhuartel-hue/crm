"""Numeracja faktur: osobna dla kazdej firmy i rodzaju faktury, bez dziur."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Company, Invoice, NumberSequence


def period_for(company: Company, date: dt.date) -> str:
    return f"{date:%Y-%m}" if company.numbering_reset == "month" else f"{date:%Y}"


def format_number(pattern: str, seq: int, date: dt.date) -> str:
    return (
        pattern.replace("{nr}", str(seq))
        .replace("{MM}", f"{date:%m}")
        .replace("{RRRR}", f"{date:%Y}")
        .replace("{RR}", f"{date:%y}")
    )


def next_number(db: Session, company: Company, kind: str, issue_date: dt.date) -> tuple[int, str]:
    period = period_for(company, issue_date)
    seq_row = db.execute(
        select(NumberSequence)
        .where(NumberSequence.company_id == company.id, NumberSequence.kind == kind, NumberSequence.period == period)
        .with_for_update()
    ).scalar_one_or_none()
    if seq_row is None:
        seq_row = NumberSequence(company_id=company.id, kind=kind, period=period, last=0)
        db.add(seq_row)
    seq_row.last += 1
    pattern = company.pattern_margin if kind == "margin" else company.pattern_vat
    return seq_row.last, format_number(pattern, seq_row.last, issue_date)


def issue(db: Session, invoice: Invoice) -> None:
    """Nadaje numer i zatwierdza fakture. Po wystawieniu faktury nie mozna juz edytowac."""
    if invoice.status == "issued":
        return
    if not invoice.items:
        raise ValueError("Faktura nie ma pozycji.")
    if not invoice.buyer_name.strip():
        raise ValueError("Brak nabywcy.")
    seq, number = next_number(db, invoice.company, invoice.kind, invoice.issue_date)
    invoice.seq = seq
    invoice.number = number
    invoice.status = "issued"
    if invoice.car is not None:
        invoice.car.status = "sold"
