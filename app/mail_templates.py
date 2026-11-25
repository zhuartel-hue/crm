"""Szablony maili: domyslne tresci, znaczniki i wypelnianie danymi faktury / WZ."""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calc
from .models import Company, Delivery, EmailTemplate, Invoice

PLACEHOLDERS = {
    "firma": "nazwa Twojej firmy",
    "konto": "numer konta firmy",
    "nabywca": "nazwa klienta",
    "numer": "numer dokumentu",
    "rodzaj": "rodzaj dokumentu (np. faktura VAT)",
    "data_wystawienia": "data wystawienia",
    "kwota": "kwota brutto",
    "do_zaplaty": "pozostało do zapłaty",
    "termin": "termin płatności",
    "dni_po_terminie": "ile dni po terminie",
}

DEFAULTS = [
    ("invoice", "Wysyłka faktury", "{rodzaj} {numer} – {firma}",
     "Dzień dobry,\n\nw załączniku przesyłam {rodzaj} {numer} na kwotę {kwota} zł brutto.\n"
     "Termin płatności: {termin}, konto: {konto}.\n\nPozdrawiam\n{firma}"),
    ("reminder", "Przypomnienie o płatności", "Przypomnienie o płatności: {rodzaj} {numer} – {firma}",
     "Dzień dobry,\n\nuprzejmie przypominamy o płatności za {rodzaj} {numer} z dnia {data_wystawienia}.\n"
     "Pozostało do zapłaty: {do_zaplaty} zł. Termin płatności minął {termin} ({dni_po_terminie} dni temu).\n"
     "Numer konta: {konto}. W tytule przelewu prosimy wpisać numer faktury: {numer}.\n\n"
     "Jeśli płatność została już wysłana, prosimy potraktować tę wiadomość jako nieaktualną.\n\nPozdrawiam\n{firma}"),
    ("wz", "Wysyłka WZ", "{rodzaj} {numer} – {firma}",
     "Dzień dobry,\n\nw załączniku przesyłam dokument {rodzaj} {numer} z dnia {data_wystawienia}.\n\nPozdrawiam\n{firma}"),
    ("recurring", "Faktura cykliczna", "{rodzaj} {numer} – {firma}",
     "Dzień dobry,\n\nw załączniku przesyłam {rodzaj} {numer} za bieżący okres na kwotę {kwota} zł brutto.\n"
     "Termin płatności: {termin}, konto: {konto}.\n\nPozdrawiam\n{firma}"),
]

_TAG = re.compile(r"\{([a-z_]+)\}")


def ensure_defaults(db: Session, company: Company) -> None:
    """Przy pierwszym uzyciu zaklada firmie domyslne szablony (mozna je potem zmieniac)."""
    have = set(db.scalars(select(EmailTemplate.kind).where(EmailTemplate.company_id == company.id)))
    added = False
    for kind, name, subject, body in DEFAULTS:
        if kind not in have:
            db.add(EmailTemplate(company_id=company.id, kind=kind, name=name, subject=subject, body=body, is_default=True))
            added = True
    if added:
        db.flush()


def for_kind(db: Session, company: Company, kind: str) -> list[EmailTemplate]:
    ensure_defaults(db, company)
    return list(db.scalars(select(EmailTemplate).where(EmailTemplate.company_id == company.id, EmailTemplate.kind == kind)
                           .order_by(EmailTemplate.is_default.desc(), EmailTemplate.name)))


def default_for(db: Session, company: Company, kind: str) -> EmailTemplate | None:
    rows = for_kind(db, company, kind)
    return rows[0] if rows else None


def context(company: Company, inv: Invoice | None = None, delivery: Delivery | None = None) -> dict:
    ctx = {"firma": company.name, "konto": company.bank_account or "", "nabywca": "", "numer": "", "rodzaj": "",
           "data_wystawienia": "", "kwota": "", "do_zaplaty": "", "termin": "", "dni_po_terminie": "0"}
    if inv is not None:
        ctx.update(nabywca=inv.buyer_name, numer=inv.display_number, rodzaj=inv.kind_label.lower(),
                   data_wystawienia=f"{inv.issue_date:%d.%m.%Y}", kwota=calc.format_money(inv.total_gross),
                   do_zaplaty=calc.format_money(max(inv.to_pay, 0)),
                   termin=f"{inv.due_date:%d.%m.%Y}" if inv.due_date else "",
                   dni_po_terminie=str(inv.days_overdue))
    if delivery is not None:
        ctx.update(nabywca=delivery.recipient_name, numer=delivery.display_number, rodzaj=delivery.kind_short,
                   data_wystawienia=f"{delivery.issue_date:%d.%m.%Y}", kwota=calc.format_money(delivery.total_gross))
    return ctx


def sample_context(company: Company) -> dict:
    today = dt.date.today()
    return {"firma": company.name, "konto": company.bank_account or "00 0000 0000 0000 0000 0000 0000",
            "nabywca": "Przykładowy Klient Sp. z o.o.", "numer": f"FV/7/{today:%m/%Y}", "rodzaj": "faktura VAT",
            "data_wystawienia": f"{today - dt.timedelta(days=21):%d.%m.%Y}", "kwota": "1 230,00",
            "do_zaplaty": "1 230,00", "termin": f"{today - dt.timedelta(days=7):%d.%m.%Y}", "dni_po_terminie": "7"}


def render(text: str, ctx: dict) -> str:
    """Podmienia tylko znane znaczniki; reszta tekstu (takze inne nawiasy) zostaje bez zmian."""
    return _TAG.sub(lambda m: str(ctx[m.group(1)]) if m.group(1) in ctx else m.group(0), text or "")


def unknown_tags(text: str) -> list[str]:
    return sorted({t for t in _TAG.findall(text or "") if t not in PLACEHOLDERS})
