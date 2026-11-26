"""Import wyciagow bankowych i dopasowanie wplat do faktur.

Zasady dopasowania (tylko wplywy, faktury wystawione z niezaplacona kwota):
1. Numer faktury w tytule przelewu (bez spacji, bez wielkosci liter) i kwota <= do zaplaty
   -> zaksiegowane automatycznie ("auto").
2. Dokladnie jedna faktura z kwota do zaplaty rowna wplacie i nazwa albo NIP nabywcy pasuje
   do nadawcy/tytulu -> zaksiegowane automatycznie.
3. Inaczej, jesli jest jedna faktura z ta sama kwota albo pasujacym nabywca -> propozycja
   ("suggested") do potwierdzenia jednym kliknieciem.
Zaksiegowanie dodaje kwote do Invoice.paid; cofniecie ja odejmuje.
"""
from __future__ import annotations

import datetime as dt

import re
import unicodedata
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import calc
from ..models import BankTransaction, Company, Invoice
from ..services import audit
from . import mt940

STATES = {
    "auto": "dopasowano automatycznie",
    "manual": "dopasowano ręcznie",
    "suggested": "propozycja",
    "none": "bez dopasowania",
    "ignored": "pominięto",
    "out": "wypływ",
}

_STOP = {"sp", "z", "o", "oo", "s", "a", "sa", "spolka", "firma", "ul", "pl", "zoo", "spzoo", "the", "i"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return s


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _norm(s))


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", _norm(s)) if w not in _STOP}


def company_for_account(db: Session, account: str) -> Company | None:
    digits = re.sub(r"\D", "", account)[-26:]
    if not digits:
        return None
    for c in db.scalars(select(Company)):
        if re.sub(r"\D", "", c.bank_account or "")[-26:] == digits:
            return c
    return None


def open_invoices(db: Session, company_id: int) -> list[Invoice]:
    return list(db.scalars(
        select(Invoice).where(Invoice.company_id == company_id, Invoice.status == "issued",
                              Invoice.kind != "correction", Invoice.paid < Invoice.total_gross)
        .order_by(Invoice.issue_date)
    ))


def _number_in_title(inv: Invoice, title_c: str) -> bool:
    if not inv.number:
        return False
    num = _compact(inv.number)
    if len(num) < 4 or num not in title_c:
        return False
    # "FV/1/11/2026" nie moze pasowac do tytulu "FV/11/11/2026": sprawdz, czy przed numerem nie stoi cyfra.
    i = title_c.find(num)
    while i != -1:
        before = title_c[i - 1] if i > 0 else ""
        after = title_c[i + len(num)] if i + len(num) < len(title_c) else ""
        if not (before.isdigit() and num[0].isdigit()) and not (after.isdigit() and num[-1].isdigit()):
            return True
        i = title_c.find(num, i + 1)
    return False


def _buyer_match(inv: Invoice, tx: BankTransaction) -> bool:
    nip = re.sub(r"\D", "", inv.buyer_nip or "")
    hay = re.sub(r"\D", "", tx.title or "")
    if len(nip) == 10 and nip in hay:
        return True
    buyer = _words(inv.buyer_name)
    sender = _words(tx.counterparty) | _words(tx.title)
    if not buyer:
        return False
    common = buyer & sender
    return len(common) >= min(2, len(buyer))


def find_match(tx: BankTransaction, invoices: list[Invoice]) -> tuple[Invoice | None, str]:
    if tx.amount <= 0:
        return None, "out"
    title_c = _compact(tx.title)
    by_number = [i for i in invoices if _number_in_title(i, title_c)]
    # Najdluzszy numer wygrywa (FV/1/11/2026 vs FV/11/11/2026 rozstrzygniete wyzej, to dodatkowe zabezpieczenie).
    if by_number:
        by_number.sort(key=lambda i: -len(i.number or ""))
        inv = by_number[0]
        if tx.amount <= inv.to_pay + Decimal("0.01"):
            return inv, "auto"
        return inv, "suggested"
    same_amount = [i for i in invoices if abs(i.to_pay - tx.amount) < Decimal("0.005")]
    buyer = [i for i in same_amount if _buyer_match(i, tx)]
    if len(buyer) == 1:
        return buyer[0], "auto"
    if len(same_amount) == 1:
        return same_amount[0], "suggested"
    by_buyer = [i for i in invoices if _buyer_match(i, tx) and tx.amount <= i.to_pay + Decimal("0.01")]
    if len(by_buyer) == 1:
        return by_buyer[0], "suggested"
    return None, "none"


def _apply(db: Session, actor: str, tx: BankTransaction, inv: Invoice, state: str) -> None:
    amount = min(tx.amount, inv.to_pay) if inv.to_pay > 0 else Decimal("0")
    inv.paid = (inv.paid or Decimal("0")) + amount
    tx.invoice_id = inv.id
    tx.applied_amount = amount
    tx.state = state
    audit(db, actor, "invoice.paid",
          f"{inv.number}: wpłata z banku {calc.format_money(amount)} zł ({tx.booking_date:%d.%m.%Y}, {tx.counterparty[:60]})",
          company_id=inv.company_id, entity="invoice", entity_id=inv.id)


def store(db: Session, actor: str, target: Company, rows: list[dict], source: str) -> dict:
    """Zapisuje operacje (slowniki z polami BankTransaction, w tym fingerprint), pomija powtorzone i od razu
    dopasowuje wplaty do faktur. Uzywane przez import MT940 i przez polaczenie z bankiem (PSD2)."""
    added = skipped = auto = suggested = 0
    invoices = open_invoices(db, target.id)
    for row in rows:
        if db.scalar(select(BankTransaction.id).where(BankTransaction.company_id == target.id,
                                                     BankTransaction.fingerprint == row["fingerprint"])):
            skipped += 1
            continue
        tx = BankTransaction(
            company_id=target.id, account=row.get("account", "")[:40], booking_date=row["booking_date"],
            value_date=row.get("value_date") or row["booking_date"], amount=row["amount"],
            currency=(row.get("currency") or "PLN")[:3], title=(row.get("title") or "")[:1000],
            counterparty=(row.get("counterparty") or "")[:300],
            counterparty_account=(row.get("counterparty_account") or "")[:40], op_code=(row.get("op_code") or "")[:5],
            bank_ref=(row.get("bank_ref") or "")[:40], fingerprint=row["fingerprint"], source=source[:200],
            state="none", applied_amount=Decimal("0"),
        )
        db.add(tx)
        added += 1
        inv, state = find_match(tx, invoices)
        if inv is not None and state == "auto":
            _apply(db, actor, tx, inv, "auto")
            auto += 1
            invoices = [i for i in invoices if i.to_pay > 0]
        elif inv is not None:
            tx.invoice_id = inv.id
            tx.state = "suggested"
            suggested += 1
        else:
            tx.state = state
    db.flush()
    re_auto, re_sugg = rematch(db, actor, target.id)
    return {"added": added, "skipped": skipped, "auto": auto + re_auto, "suggested": suggested + re_sugg}


REMATCH_DAYS = 120


def rematch(db: Session, actor: str, company_id: int) -> tuple[int, int]:
    """Wplywy bez faktury z ostatnich miesiecy sprawdza jeszcze raz, bo faktura mogla powstac po wplacie
    (albo przyjsc z importu). Automatycznie ksieguje tylko, gdy w tytule jest numer faktury; dopasowanie
    po kwocie lub nazwie zostaje propozycja do potwierdzenia, i tylko do faktury wystawionej przed wplata."""
    since = dt.date.today() - dt.timedelta(days=REMATCH_DAYS)
    txs = list(db.scalars(select(BankTransaction).where(
        BankTransaction.company_id == company_id, BankTransaction.state == "none", BankTransaction.amount > 0,
        BankTransaction.invoice_id.is_(None), BankTransaction.booking_date >= since)
        .order_by(BankTransaction.booking_date)))
    if not txs:
        return 0, 0
    invoices = open_invoices(db, company_id)
    auto = suggested = 0
    for tx in txs:
        if not invoices:
            break
        inv, state = find_match(tx, invoices)
        if inv is None:
            continue
        if state == "auto" and _number_in_title(inv, _compact(tx.title)):
            _apply(db, actor, tx, inv, "auto")
            auto += 1
            invoices = [i for i in invoices if i.to_pay > 0]
        elif inv.issue_date <= tx.booking_date:
            tx.invoice_id = inv.id
            tx.state = "suggested"
            suggested += 1
    db.flush()
    return auto, suggested


def import_file(db: Session, actor: str, data: bytes, filename: str, company: Company) -> dict:
    """Wczytuje plik MT940. Rachunek z pliku wskazuje firme; gdy nie pasuje do zadnej, uzywa `company`."""
    statements = mt940.parse(data)
    total = {"added": 0, "skipped": 0, "auto": 0, "suggested": 0}
    companies_seen: set[str] = set()
    for st in statements:
        target = company_for_account(db, st.account) or company
        companies_seen.add(target.short_name)
        rows = [dict(account=st.account, booking_date=t.booking_date, value_date=t.value_date, amount=t.amount,
                     currency=st.currency, title=t.title, counterparty=t.counterparty,
                     counterparty_account=t.counterparty_account, op_code=t.op_code, bank_ref=t.bank_ref,
                     fingerprint=t.fingerprint) for t in st.transactions]
        for k, v in store(db, actor, target, rows, filename).items():
            total[k] += v
    summary = (f"Wczytano {filename}: {total['added']} nowych operacji, {total['skipped']} już było, "
               f"{total['auto']} wpłat zaksięgowanych automatycznie, {total['suggested']} propozycji do potwierdzenia.")
    audit(db, actor, "bank.import", summary, company_id=company.id, entity="bank")
    return {**total, "companies": sorted(companies_seen), "summary": summary}


def _get(db: Session, company_id: int, tx_id: int) -> BankTransaction:
    tx = db.get(BankTransaction, tx_id)
    if tx is None or tx.company_id != company_id:
        raise ValueError("Nie ma takiej operacji.")
    return tx


def confirm(db: Session, actor: str, company_id: int, tx_id: int, invoice_id: int | None = None) -> BankTransaction:
    tx = _get(db, company_id, tx_id)
    if tx.state in ("auto", "manual"):
        raise ValueError("Ta wpłata jest już zaksięgowana.")
    if tx.amount <= 0:
        raise ValueError("To nie jest wpłata.")
    inv = db.get(Invoice, invoice_id or tx.invoice_id or 0)
    if inv is None or inv.company_id != company_id or inv.status != "issued":
        raise ValueError("Wybierz wystawioną fakturę tej firmy.")
    if inv.to_pay <= 0:
        raise ValueError(f"Faktura {inv.number} jest już zapłacona.")
    _apply(db, actor, tx, inv, "manual")
    return tx


def undo(db: Session, actor: str, company_id: int, tx_id: int) -> BankTransaction:
    tx = _get(db, company_id, tx_id)
    if tx.state in ("auto", "manual") and tx.invoice is not None:
        inv = tx.invoice
        inv.paid = max(Decimal("0"), (inv.paid or Decimal("0")) - (tx.applied_amount or Decimal("0")))
        audit(db, actor, "invoice.unpaid",
              f"{inv.number}: cofnięto wpłatę z banku {calc.format_money(tx.applied_amount)} zł",
              company_id=inv.company_id, entity="invoice", entity_id=inv.id)
    tx.invoice_id = None
    tx.applied_amount = Decimal("0")
    tx.state = "none" if tx.amount > 0 else "out"
    return tx


def ignore(db: Session, actor: str, company_id: int, tx_id: int) -> BankTransaction:
    tx = _get(db, company_id, tx_id)
    if tx.state in ("auto", "manual"):
        undo(db, actor, company_id, tx_id)
    tx.invoice_id = None
    tx.state = "ignored"
    return tx


def find_invoice_by_number(db: Session, company_id: int, text: str) -> Invoice | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        inv = db.get(Invoice, int(text))
        if inv is not None and inv.company_id == company_id:
            return inv
    c = _compact(text)
    for inv in db.scalars(select(Invoice).where(Invoice.company_id == company_id, Invoice.status == "issued")):
        if inv.number and _compact(inv.number) == c:
            return inv
    return None


def tx_dict(tx: BankTransaction) -> dict:
    return {
        "id": tx.id, "booking_date": tx.booking_date.isoformat(), "amount": f"{tx.amount:.2f}", "currency": tx.currency,
        "title": tx.title, "counterparty": tx.counterparty, "counterparty_account": tx.counterparty_account,
        "state": tx.state, "state_label": STATES.get(tx.state, tx.state),
        "invoice": tx.invoice.number if tx.invoice else None, "invoice_id": tx.invoice_id,
        "applied_amount": f"{(tx.applied_amount or 0):.2f}",
    }
