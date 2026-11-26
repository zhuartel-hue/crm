"""Polaczenie firmy z bankiem przez Enable Banking: zgoda w banku, pobieranie operacji w tle, dopasowanie wplat.

Przebieg:
1. start(): tworzy BankConnection (pending) z losowym `auth_state` i zwraca adres logowania w banku.
2. Bank odsyla na /bank/powrot?code=..&state=..; finish() zaklada sesje i zapisuje rachunki.
3. sync() pobiera operacje zaksiegowane (BOOK) od ostatniej synchronizacji (z zapasem 5 dni, powtorki
   odrzuca fingerprint) i przekazuje je do service.store(), ktore dopasowuje wplaty do faktur.
Banki zwykle pozwalaja na ok. 4 pobrania w tle dziennie, dlatego domyslnie co 6 godzin.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import secrets
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BankConnection, Company
from ..services import audit
from . import service
from .enablebanking import BankApiError, Client

log = logging.getLogger("sygnavo.bank")
DEFAULT_VALIDITY = 90 * 86400
FIRST_SYNC_DAYS = 90
OVERLAP_DAYS = 5
WARN_DAYS = 14

_ASPSP_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def banks(client: Client, country: str = "PL", psu_type: str = "business") -> list[dict]:
    """Lista bankow (nazwa, logo, maksymalna waznosc zgody); pamiec na 12 h."""
    import time

    key = (country, psu_type)
    hit = _ASPSP_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < 12 * 3600:
        return hit[1]
    rows = sorted(client.aspsps(country, psu_type), key=lambda a: (a.get("name") or "").lower())
    _ASPSP_CACHE[key] = (time.monotonic(), rows)
    return rows


def connections(db: Session, company_id: int) -> list[BankConnection]:
    return list(db.scalars(select(BankConnection).where(BankConnection.company_id == company_id,
                                                        BankConnection.state != "closed")
                           .order_by(BankConnection.id.desc())))


def start(db: Session, actor: str, client: Client, company: Company, aspsp_name: str, psu_type: str,
          redirect_url: str, psu_ip: str = "", country: str = "PL") -> str:
    aspsp_name = (aspsp_name or "").strip()
    if not aspsp_name:
        raise BankApiError("Wybierz bank.")
    psu_type = psu_type if psu_type in ("business", "personal") else "business"
    seconds = DEFAULT_VALIDITY
    for a in banks(client, country, psu_type):
        if a.get("name") == aspsp_name and a.get("maximum_consent_validity"):
            seconds = int(a["maximum_consent_validity"])
            break
    valid_until = now() + dt.timedelta(seconds=max(3600, seconds - 3600))
    conn = BankConnection(company_id=company.id, provider="enablebanking", aspsp_name=aspsp_name[:120],
                          aspsp_country=country, psu_type=psu_type, auth_state=secrets.token_urlsafe(32),
                          session_id="", accounts_json="[]", state="pending", last_error="", created_by=actor[:100])
    db.add(conn)
    db.flush()
    res = client.start_auth(aspsp_name, country, psu_type, valid_until, conn.auth_state, redirect_url, psu_ip)
    url = res.get("url") or ""
    if not url.startswith("https://"):
        raise BankApiError("Enable Banking nie zwrócił adresu logowania do banku.")
    audit(db, actor, "bank.connect", f"Rozpoczęto podłączanie banku {aspsp_name}", company_id=company.id, entity="bank")
    return url


def finish(db: Session, actor: str, client: Client, state: str, code: str = "", error: str = "",
           error_description: str = "", psu_ip: str = "") -> BankConnection:
    conn = db.scalar(select(BankConnection).where(BankConnection.auth_state == state, BankConnection.state == "pending")) \
        if state else None
    if conn is None:
        raise BankApiError("Nie rozpoznaję tego powrotu z banku. Zacznij podłączanie od nowa.")
    conn.auth_state = ""
    if error or not code:
        conn.state = "error"
        conn.last_error = f"Bank nie wydał zgody: {error_description or error or 'brak kodu'}"[:1000]
        return conn
    data = client.create_session(code, psu_ip)
    conn.session_id = (data.get("session_id") or "")[:80]
    until = ((data.get("access") or {}).get("valid_until") or "")
    try:
        conn.valid_until = dt.datetime.fromisoformat(until.replace("Z", "+00:00")).astimezone(dt.UTC).replace(tzinfo=None)
    except ValueError:
        conn.valid_until = now() + dt.timedelta(seconds=DEFAULT_VALIDITY)
    accounts = []
    for a in data.get("accounts") or []:
        if isinstance(a, str):
            accounts.append(dict(uid=a, iban="", name="", currency=""))
            continue
        accounts.append(dict(uid=a.get("uid") or "", iban=(a.get("account_id") or {}).get("iban") or "",
                             name=a.get("name") or a.get("product") or "", currency=a.get("currency") or ""))
    conn.accounts_json = json.dumps([a for a in accounts if a["uid"]], ensure_ascii=False)
    conn.state = "active" if conn.session_id and accounts else "error"
    conn.last_error = "" if conn.state == "active" else "Bank nie udostępnił żadnego rachunku."
    audit(db, actor, "bank.connect",
          f"Podłączono bank {conn.aspsp_name}: {len(accounts)} rachunków, zgoda do {conn.valid_until:%d.%m.%Y}",
          company_id=conn.company_id, entity="bank")
    return conn


def _amount(tx: dict) -> Decimal | None:
    try:
        amt = Decimal(str((tx.get("transaction_amount") or {}).get("amount") or ""))
    except InvalidOperation:
        return None
    amt = abs(amt)
    return -amt if (tx.get("credit_debit_indicator") or "").upper() == "DBIT" else amt


def _date(text: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def to_row(account: dict, tx: dict) -> dict | None:
    """Operacja z Enable Banking -> slownik dla service.store(). Tylko zaksiegowane (BOOK)."""
    if (tx.get("status") or "BOOK").upper() != "BOOK":
        return None
    amount = _amount(tx)
    booking = _date(tx.get("booking_date")) or _date(tx.get("value_date")) or _date(tx.get("transaction_date"))
    if amount is None or booking is None:
        return None
    incoming = amount > 0
    party = (tx.get("debtor") if incoming else tx.get("creditor")) or {}
    party_acc = (tx.get("debtor_account") if incoming else tx.get("creditor_account")) or {}
    remit = tx.get("remittance_information") or []
    title = " ".join(r for r in remit if isinstance(r, str)) if isinstance(remit, list) else str(remit)
    ref = tx.get("entry_reference") or tx.get("transaction_id") or ""
    ident = ref or json.dumps([booking.isoformat(), str(amount), title, party.get("name"), party_acc.get("iban")],
                              ensure_ascii=False)
    code = (tx.get("bank_transaction_code") or {}).get("code") or ""
    return dict(
        account=account.get("iban") or account.get("uid", ""), booking_date=booking,
        value_date=_date(tx.get("value_date")) or booking, amount=amount,
        currency=(tx.get("transaction_amount") or {}).get("currency") or account.get("currency") or "PLN",
        title=title, counterparty=party.get("name") or "", counterparty_account=party_acc.get("iban") or "",
        op_code=str(code)[:5], bank_ref=str(ref)[:40],
        fingerprint=hashlib.sha256(f"eb|{account.get('uid')}|{ident}".encode()).hexdigest(),
    )


def sync(db: Session, actor: str, client: Client, conn: BankConnection, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    total = {"added": 0, "skipped": 0, "auto": 0, "suggested": 0}
    if conn.state != "active":
        return total
    if conn.valid_until and conn.valid_until <= now():
        conn.state = "expired"
        conn.last_error = "Zgoda w banku wygasła. Odnów połączenie."
        return total
    since = (conn.synced_until - dt.timedelta(days=OVERLAP_DAYS)) if conn.synced_until \
        else today - dt.timedelta(days=FIRST_SYNC_DAYS)
    company = db.get(Company, conn.company_id)
    for acc in conn.accounts:
        rows = [r for r in (to_row(acc, t) for t in client.transactions(acc["uid"], since, today)) if r]
        for k, v in service.store(db, actor, company, rows, f"bank {conn.aspsp_name}").items():
            total[k] += v
    conn.synced_until = today
    conn.last_sync = now()
    conn.last_error = ""
    if total["added"]:
        audit(db, actor, "bank.sync",
              f"Bank {conn.aspsp_name}: {total['added']} nowych operacji, {total['auto']} wpłat zaksięgowanych, "
              f"{total['suggested']} propozycji", company_id=conn.company_id, entity="bank")
    return total


def sync_all(db: Session, actor: str = "system (bank)") -> int:
    from .enablebanking import is_configured

    if not is_configured(db):
        return 0
    client = Client.from_db(db)
    made = 0
    for conn in db.scalars(select(BankConnection).where(BankConnection.state == "active")).all():
        try:
            made += sync(db, actor, client, conn)["added"]
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            conn = db.get(BankConnection, conn.id)
            conn.last_error = str(e)[:1000]
            db.commit()
            log.warning("Bank %s (firma %s): %s", conn.aspsp_name, conn.company_id, e)
    return made


def disconnect(db: Session, actor: str, client: Client | None, conn: BankConnection) -> None:
    if client is not None and conn.session_id:
        try:
            client.delete_session(conn.session_id)
        except BankApiError as e:
            log.warning("Usuwanie zgody %s: %s", conn.id, e)
    conn.state = "closed"
    conn.session_id = ""
    audit(db, actor, "bank.disconnect", f"Odłączono bank {conn.aspsp_name}", company_id=conn.company_id, entity="bank")


def warnings(db: Session, company_id: int) -> list[str]:
    """Komunikaty do pokazania na Start i w Banku: zgoda wygasa, wygasla albo blad pobierania."""
    out = []
    for c in connections(db, company_id):
        if c.state == "active" and c.days_left is not None and c.days_left <= WARN_DAYS:
            out.append(f"Zgoda banku {c.aspsp_name} wygasa za {max(c.days_left, 0)} dni. Odnów ją w Banku.")
        elif c.state == "expired":
            out.append(f"Zgoda banku {c.aspsp_name} wygasła. Wpłaty nie są pobierane, odnów połączenie w Banku.")
        elif c.state == "active" and c.last_error:
            out.append(f"Bank {c.aspsp_name}: {c.last_error[:200]}")
    return out
