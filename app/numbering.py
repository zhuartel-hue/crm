"""Numeracja faktur: osobna dla kazdej firmy i serii (VAT, proforma, zaliczkowa, korekta), bez dziur."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import NUMBER_SERIES, Company, Invoice, NumberSequence


def period_for(company: Company, date: dt.date) -> str:
    return f"{date:%Y-%m}" if company.numbering_reset == "month" else f"{date:%Y}"


def format_number(pattern: str, seq: int, date: dt.date) -> str:
    return (
        pattern.replace("{nr}", str(seq))
        .replace("{NNN}", f"{seq:03d}")
        .replace("{MM}", f"{date:%m}")
        .replace("{RRRR}", f"{date:%Y}")
        .replace("{RR}", f"{date:%y}")
    )


PATTERNS = {"vat": "pattern_vat", "proforma": "pattern_proforma", "advance": "pattern_advance",
            "correction": "pattern_correction", "wz": "pattern_wz", "pz": "pattern_pz"}


def next_number(db: Session, company: Company, kind: str, issue_date: dt.date) -> tuple[int, str]:
    """Kolejny numer w serii danego rodzaju (koncowa liczy sie razem z fakturami VAT)."""
    kind = NUMBER_SERIES.get(kind, kind)
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
    pattern = getattr(company, PATTERNS[kind])
    return seq_row.last, format_number(pattern, seq_row.last, issue_date)


def issue(db: Session, invoice: Invoice) -> None:
    """Nadaje numer i zatwierdza fakture. Po wystawieniu faktury nie mozna juz edytowac."""
    if invoice.status == "issued":
        return
    if not invoice.items:
        raise ValueError("Faktura nie ma pozycji.")
    if not invoice.buyer_name.strip():
        raise ValueError("Brak nabywcy.")
    check(invoice)
    seq, number = next_number(db, invoice.company, invoice.kind, invoice.issue_date)
    invoice.seq = seq
    invoice.number = number
    invoice.status = "issued"
    if invoice.car is not None:
        # Proforma i zaliczka rezerwuja auto, faktura VAT i koncowa je sprzedaja.
        if invoice.kind in ("vat", "final"):
            invoice.car.status = "sold"
        elif invoice.kind in ("proforma", "advance") and invoice.car.status == "available":
            invoice.car.status = "reserved"


def check(invoice: Invoice) -> None:
    """Warunki wystawienia zalezne od rodzaju faktury."""
    if invoice.kind == "correction":
        if invoice.related is None or invoice.related.status != "issued":
            raise ValueError("Korekta musi wskazywać wystawioną fakturę.")
        if not (invoice.correction_reason or "").strip():
            raise ValueError("Podaj przyczynę korekty.")
        if invoice.issue_date < invoice.related.issue_date:
            raise ValueError("Data korekty nie może być wcześniejsza niż data faktury korygowanej.")
    elif invoice.kind == "advance":
        adv = invoice.advance_gross or 0
        if adv <= 0:
            raise ValueError("Podaj kwotę otrzymanej zaliczki.")
        if adv > (invoice.order_total or 0):
            raise ValueError("Zaliczka nie może być większa niż wartość zamówienia.")
    elif invoice.kind == "final":
        if not invoice.advances:
            raise ValueError("Faktura końcowa musi rozliczać co najmniej jedną zaliczkę.")
        if any(a.status != "issued" for a in invoice.advances):
            raise ValueError("Rozliczane zaliczki muszą być wystawione.")
