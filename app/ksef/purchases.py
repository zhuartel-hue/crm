"""Faktury kosztowe: pobieranie z KSeF faktur, na ktorych firma jest nabywca, i ich weryfikacja w programie.

Pobiera przyrostowo po dacie trwalego zapisu w KSeF (PermanentStorage, rosnaco). Kursor (data ostatniej pobranej
faktury) jest w Company.ksef_purchase_cursor; kolejne okno zaczyna sie od niego, duble odrzuca numer KSeF.
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Company, Contractor, PurchaseInvoice
from ..services import ServiceError, audit
from . import incoming, service
from .client import KsefError

log = logging.getLogger("sygnavo.ksef")
WINDOW_DAYS = 90  # zakres jednego zapytania
MAX_PAGES = 50
PAGE_SIZE = 100


def _iso(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_ts(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def _dec(v) -> Decimal:
    return Decimal(str(v)) if v not in (None, "") else Decimal("0")


def _store(db: Session, company: Company, m: dict, xml: bytes) -> PurchaseInvoice:
    seller = m.get("seller") or {}
    try:
        data = incoming.parse(xml)
    except Exception:  # noqa: BLE001  - zly XML nie blokuje pobrania reszty
        log.exception("Nie mozna odczytac XML %s", m.get("ksefNumber"))
        data = {}
    nip = (seller.get("nip") or data.get("seller_nip") or "").strip()
    contractor = db.scalar(select(Contractor).where(Contractor.company_id == company.id, Contractor.nip == nip).limit(1)) if nip else None
    issue = m.get("issueDate") or ""
    p = PurchaseInvoice(
        company_id=company.id, ksef_env=company.ksef_env, ksef_number=m["ksefNumber"],
        invoice_number=(m.get("invoiceNumber") or data.get("number") or "")[:256],
        invoice_type=(m.get("invoiceType") or "")[:10],
        issue_date=dt.date.fromisoformat(issue[:10]) if issue else data.get("issue_date"),
        stored_at=(m.get("permanentStorageDate") or "")[:40], seller_nip=nip[:20],
        seller_name=(seller.get("name") or data.get("seller_name") or "")[:512],
        contractor_id=contractor.id if contractor else None,
        net=_dec(m.get("netAmount")), vat=_dec(m.get("vatAmount")), gross=_dec(m.get("grossAmount")),
        currency=(m.get("currency") or data.get("currency") or "PLN")[:3],
        due_date=data.get("due_date"), bank_account=(data.get("bank_account") or "")[:40],
        xml=xml.decode("utf-8", errors="replace"), status="new", notes="",
        paid_at=data.get("issue_date") if data.get("paid") else None,
    )
    db.add(p)
    return p


def fetch(db: Session, actor: str, company: Company, since: dt.date | None = None, now: dt.datetime | None = None) -> int:
    """Pobiera nowe faktury kosztowe. since: od kiedy przy pierwszym pobraniu (domyslnie 30 dni wstecz)."""
    client = service._client(company)
    now = now or dt.datetime.now(dt.timezone.utc)
    if since is not None:
        start = dt.datetime.combine(since, dt.time(), ZoneInfo("Europe/Warsaw"))
    elif company.ksef_purchase_cursor:
        start = _parse_ts(company.ksef_purchase_cursor)
    else:
        start = now - dt.timedelta(days=30)
    known = set(db.scalars(select(PurchaseInvoice.ksef_number).where(PurchaseInvoice.company_id == company.id)))
    added = 0
    cursor = company.ksef_purchase_cursor
    try:
        while start < now:
            end = min(start + dt.timedelta(days=WINDOW_DAYS), now)
            date_from, offset = _iso(start), 0
            for _ in range(MAX_PAGES):
                data = client.query_received(date_from, _iso(end), page_offset=offset, page_size=PAGE_SIZE)
                rows = data.get("invoices") or []
                for m in rows:
                    num = m.get("ksefNumber")
                    if not num:
                        continue
                    if num not in known:
                        _store(db, company, m, client.download(num))
                        known.add(num)
                        added += 1
                    if m.get("permanentStorageDate"):
                        cursor = m["permanentStorageDate"]
                db.flush()
                if not data.get("hasMore"):
                    break
                if data.get("isTruncated") and rows and rows[-1].get("permanentStorageDate"):
                    date_from, offset = rows[-1]["permanentStorageDate"], 0
                else:
                    offset += 1
            start = end
    except KsefError as e:
        db.rollback()
        company = db.get(Company, company.id)
        company.ksef_purchase_error = str(e)[:2000]
        db.commit()
        raise ServiceError(f"KSeF: {e}") from None
    company.ksef_purchase_cursor = cursor or _iso(now - dt.timedelta(minutes=5))
    company.ksef_purchase_synced_at = dt.datetime.now(ZoneInfo("Europe/Warsaw")).replace(tzinfo=None)
    company.ksef_purchase_error = ""
    if added:
        audit(db, actor, "purchase.fetch", f"Pobrano z KSeF {added} faktur kosztowych", company_id=company.id,
              entity="purchase")
    db.commit()
    return added


def fetch_all(db: Session) -> None:
    for company in db.scalars(select(Company).order_by(Company.id)):
        if not service.enabled(company):
            continue
        try:
            fetch(db, "system (KSeF w tle)", company)
        except ServiceError as e:
            log.warning("Faktury kosztowe %s: %s", company.short_name, e)
