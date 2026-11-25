"""Wysylka faktur do KSeF z programu: przygotowanie XML, zapis statusu, numeru KSeF i UPO w bazie, dziennik."""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from .. import secretbox
from ..models import Company, Invoice
from ..services import ServiceError, audit, get_invoice
from . import ENVIRONMENTS, fa3, qr
from .client import KsefClient, KsefError
from .crypto import sha256_b64

# Klient do testow mozna podmienic: factory(env, nip, token) -> obiekt z send_invoice / invoice_status / upo.
client_factory = KsefClient


def enabled(company: Company) -> bool:
    return company.ksef_env in ENVIRONMENTS and bool(company.ksef_token_enc)


def can_send(inv: Invoice) -> bool:
    return inv.status == "issued" and inv.kind != "proforma" and inv.ksef_status in ("", "rejected", "error")


def _client(company: Company):
    if not enabled(company):
        raise ServiceError(f"KSeF dla firmy {company.short_name} nie jest włączony (Ustawienia → KSeF).")
    try:
        token = secretbox.open_(company.ksef_token_enc)
    except ValueError as e:
        raise ServiceError(str(e))
    return client_factory(company.ksef_env, company.nip, token)


def _ksef_number_of(inv: Invoice) -> str:
    return inv.ksef_number if inv.ksef_status == "accepted" else ""


def check_related(inv: Invoice) -> None:
    """Korekta i koncowa odwoluja sie do numerow KSeF faktur powiazanych: te musza byc juz przyjete
    albo nigdy niewysylane (wystawione przed wlaczeniem KSeF)."""
    linked = [inv.related] if inv.kind == "correction" and inv.related is not None else []
    linked += list(inv.advances) if inv.kind == "final" else []
    for other in linked:
        if other.ksef_status in ("sent", "rejected", "error"):
            raise ServiceError(f"Najpierw dokończ wysyłkę do KSeF faktury {other.number} "
                               f"(status: {status_label(other)}).")


def preview_xml(inv: Invoice) -> bytes:
    try:
        return fa3.build(inv, ksef_number_of=_ksef_number_of)
    except fa3.Fa3Error as e:
        raise ServiceError(str(e))


def send(db: Session, actor: str, invoice_id: int) -> Invoice:
    inv = get_invoice(db, invoice_id)
    if not can_send(inv):
        if inv.status != "issued":
            raise ServiceError("Najpierw wystaw fakturę.")
        if inv.kind == "proforma":
            raise ServiceError("Proforma nie jest fakturą i nie idzie do KSeF.")
        raise ServiceError(f"Faktura jest już w KSeF ({status_label(inv)}).")
    check_related(inv)
    company = inv.company
    client = _client(company)
    xml = preview_xml(inv)
    inv.ksef_xml = xml.decode("utf-8")
    inv.ksef_hash = sha256_b64(xml)
    inv.ksef_env = company.ksef_env
    inv.ksef_sent_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    inv.ksef_status = "sent"
    inv.ksef_message = ""
    db.commit()
    try:
        res = client.send_invoice(xml)
    except KsefError as e:
        inv.ksef_status = "error"
        inv.ksef_message = str(e)[:2000]
        audit(db, actor, "ksef.error", f"KSeF: błąd wysyłki {inv.number}: {inv.ksef_message[:200]}",
              company_id=inv.company_id, entity="invoice", entity_id=inv.id)
        db.commit()
        raise ServiceError(f"KSeF: {e}")
    _apply(inv, res)
    audit(db, actor, "ksef.send", f"KSeF ({ENVIRONMENTS[inv.ksef_env]['label']}): {inv.number} → "
          f"{status_label(inv)}{' ' + inv.ksef_number if inv.ksef_number else ''}",
          company_id=inv.company_id, entity="invoice", entity_id=inv.id)
    db.commit()
    return inv


def refresh(db: Session, actor: str, invoice_id: int) -> Invoice:
    """Ponowne sprawdzenie statusu (gdy KSeF przetwarzal fakture dluzej) i pobranie UPO."""
    inv = get_invoice(db, invoice_id)
    if not inv.ksef_session_ref or not inv.ksef_invoice_ref:
        raise ServiceError("Ta faktura nie była jeszcze przyjęta do przetwarzania w KSeF.")
    client = _client(inv.company)
    try:
        res = client.invoice_status(inv.ksef_session_ref, inv.ksef_invoice_ref)
        if res.status_code == 200 and not inv.ksef_upo:
            res.upo_xml = client.upo(inv.ksef_session_ref, inv.ksef_invoice_ref)
    except KsefError as e:
        raise ServiceError(f"KSeF: {e}")
    before = inv.ksef_status
    _apply(inv, res)
    if inv.ksef_status != before:
        audit(db, actor, "ksef.status", f"KSeF: {inv.number} → {status_label(inv)} {inv.ksef_number}".strip(),
              company_id=inv.company_id, entity="invoice", entity_id=inv.id)
    db.commit()
    return inv


def _apply(inv: Invoice, res) -> None:
    inv.ksef_session_ref = res.session_ref
    inv.ksef_invoice_ref = res.invoice_ref
    if res.status_code == 200:
        inv.ksef_status = "accepted"
        inv.ksef_number = res.ksef_number
        inv.ksef_message = ""
        if res.upo_xml:
            inv.ksef_upo = res.upo_xml
    elif res.status_code in (100, 150):
        inv.ksef_status = "sent"
        inv.ksef_message = res.status_text
    else:
        inv.ksef_status = "rejected"
        inv.ksef_message = (f"{res.status_code} {res.status_text}: " + "; ".join(map(str, res.details)))[:2000]


STATUS_LABELS = {"": "nie wysłano", "sent": "w trakcie", "accepted": "przyjęta", "rejected": "odrzucona",
                 "error": "błąd wysyłki"}


def status_label(inv: Invoice) -> str:
    return STATUS_LABELS.get(inv.ksef_status, inv.ksef_status)


def qr_url(inv: Invoice) -> str:
    if inv.ksef_status != "accepted" or not inv.ksef_xml or inv.ksef_env not in ENVIRONMENTS:
        return ""
    return qr.invoice_url(inv.ksef_env, inv.company.nip, inv.issue_date, inv.ksef_xml.encode("utf-8"))


def set_token(db: Session, actor: str, company: Company, env: str, token: str | None) -> None:
    if env != "off" and env not in ENVIRONMENTS:
        raise ServiceError("Środowisko KSeF: off, test, demo albo prod.")
    company.ksef_env = env
    if token:
        company.ksef_token_enc = secretbox.seal(token.strip())
    audit(db, actor, "ksef.settings", f"KSeF {company.short_name}: środowisko {env}"
          + (", nowy token" if token else ""), company_id=company.id)
    db.commit()
