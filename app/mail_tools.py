"""Poczta dla Claude (MCP): skrzynki firmy podlaczone w Sygnavo, wyszukiwanie, odczyt i wysylka.

Wysylka idzie przez te same skrzynki co w programie, wiec mail laduje w historii kontrahenta i faktury.
Wysylka wymaga uprawnienia "issue" (jak wystawienie faktury) i potwierdzenia uzytkownika w rozmowie.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import pdf
from . import services as svc
from .mail import service as mail
from .mail.service import MailError
from .models import Invoice, MailAccount, MailMessage

MAX_BODY = 20000


def _accounts(db: Session, company) -> list[MailAccount]:
    c = svc.resolve_company(db, company)
    return list(db.scalars(select(MailAccount).where(MailAccount.company_id == c.id, MailAccount.active.is_(True))
                           .order_by(MailAccount.id)))


def list_mailboxes(db: Session, company) -> list[dict]:
    return [{"id": a.id, "email": a.email, "name": a.display_name, "last_sync": a.last_sync.isoformat() if a.last_sync else None,
             "error": a.last_error or None} for a in _accounts(db, company)]


def _msg_dict(m: MailMessage, full: bool = False) -> dict:
    d = {"id": m.id, "mailbox": m.account.email, "direction": m.direction, "date": m.date.isoformat() if m.date else None,
         "from": f"{m.from_name} <{m.from_addr}>".strip() if m.from_name else m.from_addr, "to": m.to_addrs,
         "cc": m.cc_addrs, "subject": m.subject, "contractor_id": m.contractor_id, "invoice_id": m.invoice_id}
    text = m.body_text or mail.html_to_text(m.body_html or "")
    if full:
        d["body"] = text[:MAX_BODY] + ("\n[...skrócono]" if len(text) > MAX_BODY else "")
        d["message_id"] = m.message_id
    else:
        d["snippet"] = " ".join(text.split())[:200]
    return d


def search_mails(db: Session, company, query: str = "", contractor_id: int | None = None, limit: int = 20) -> list[dict]:
    ids = [a.id for a in _accounts(db, company)]
    if not ids:
        return []
    q = select(MailMessage).where(MailMessage.account_id.in_(ids))
    if contractor_id:
        q = q.where(MailMessage.contractor_id == int(contractor_id))
    for word in (query or "").split():
        like = f"%{word}%"
        q = q.where(or_(MailMessage.subject.ilike(like), MailMessage.from_addr.ilike(like),
                        MailMessage.from_name.ilike(like), MailMessage.to_addrs.ilike(like)))
    q = q.order_by(MailMessage.date.desc().nulls_last(), MailMessage.id.desc()).limit(max(1, min(int(limit or 20), 100)))
    return [_msg_dict(m) for m in db.scalars(q)]


def get_mail(db: Session, mail_id: int) -> dict:
    m = db.get(MailMessage, int(mail_id))
    # MailAccount jest filtrowany po firmach organizacji (app/tenancy.py): cudza skrzynka = brak.
    acc = db.get(MailAccount, m.account_id) if m is not None else None
    if m is None or acc is None:
        raise svc.ServiceError(f"Nie ma maila o id {mail_id}.")
    return _msg_dict(m, full=True)


def send_email(db: Session, actor: str, data: dict) -> dict:
    accounts = _accounts(db, data.get("company"))
    if not accounts:
        raise svc.ServiceError("Ta firma nie ma podłączonej skrzynki. Dodaj ją w Ustawieniach → Skrzynki pocztowe.")
    want = (data.get("mailbox") or "").strip().lower()
    acc = next((a for a in accounts if a.email.lower() == want), None) if want else accounts[0]
    if acc is None:
        raise svc.ServiceError("Nie ma takiej skrzynki w tej firmie: " + ", ".join(a.email for a in accounts))
    files, invoice_id = [], None
    if data.get("invoice_id"):
        inv = db.get(Invoice, int(data["invoice_id"]))
        if inv is None or inv.company_id != acc.company_id:
            raise svc.ServiceError("Nie ma takiej faktury w tej firmie.")
        if inv.status != "issued":
            raise svc.ServiceError("Wysyłać można tylko wystawioną fakturę, nie szkic.")
        invoice_id = inv.id
        if data.get("attach_pdf", True):
            files.append((f"{inv.number.replace('/', '_')}.pdf", "application/pdf", pdf.render_invoice(inv)))
    try:
        msg = mail.send(db, acc, data.get("to", ""), data.get("subject", ""), data.get("body", ""),
                        cc=data.get("cc", ""), attachments=files, in_reply_to=data.get("in_reply_to", ""),
                        invoice_id=invoice_id)
    except MailError as e:
        db.rollback()
        raise svc.ServiceError(str(e)) from None
    svc.audit(db, actor, "mail.send", f"Mail z {acc.email} do {data.get('to', '')}: {data.get('subject', '')}",
              company_id=acc.company_id, entity="invoice" if invoice_id else "", entity_id=invoice_id)
    db.commit()
    return {"sent": True, "from": acc.email, "to": data.get("to"), "mail_id": msg.id if msg is not None else None}
