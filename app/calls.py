"""Polaczenia telefoniczne: historia rozmow przy kliencie i szybka notatka z rozmowy.

Na razie zrodla sa dwa: historia z Firmao (bez numerow, bo API Firmao ich nie oddaje; powiazanie przez zadanie)
i wpisy reczne (np. po rozmowie z komorki). Klienta szukamy po numerze telefonu (ostatnie 9 cyfr) w kontrahentach
i osobach kontaktowych tej firmy. Pozniej dojdzie centrala (iFON BIZNES) przez ten sam model.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import CALL_DIRECTIONS, CALL_STATUSES, Contact, Contractor, PhoneCall, Task


def digits(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("00"):
        d = d[2:]
    return d[-9:] if len(d) >= 9 else d


def match(db: Session, company_id: int, phone: str) -> tuple[int | None, int | None]:
    """(contractor_id, contact_id) po numerze telefonu; None gdy brak albo niejednoznaczne."""
    key = digits(phone)
    if len(key) < 9:
        return None, None
    like = f"%{key[-3:]}%"
    people = [p for p in db.scalars(select(Contact).where(Contact.company_id == company_id,
                                                         or_(Contact.phone.like(like), Contact.phone2.like(like))))
              if key in (digits(p.phone), digits(p.phone2))]
    if len(people) == 1:
        return people[0].contractor_id, people[0].id
    firms = [c for c in db.scalars(select(Contractor).where(Contractor.company_id == company_id,
                                                            Contractor.phone.like(like)))
             if key == digits(c.phone)]
    if len(firms) == 1:
        return firms[0].id, None
    return None, None


def add(db: Session, company_id: int, *, direction: str, phone: str = "", note: str = "", status: str = "answered",
        started_at: dt.datetime | None = None, seconds: int = 0, user_label: str = "", contractor_id: int | None = None,
        contact_id: int | None = None, task_id: int | None = None) -> PhoneCall:
    if direction not in CALL_DIRECTIONS:
        raise ValueError("Nieznany kierunek połączenia.")
    if status not in CALL_STATUSES:
        raise ValueError("Nieznany wynik połączenia.")
    phone = re.sub(r"[^\d+ ]", "", phone or "").strip()[:40]
    if contractor_id is None and phone:
        contractor_id, found_contact = match(db, company_id, phone)
        contact_id = contact_id or found_contact
    c = PhoneCall(company_id=company_id, direction=direction, status=status,
                  started_at=(started_at or dt.datetime.now()).replace(microsecond=0), seconds=max(0, int(seconds or 0)),
                  phone=phone, user_label=user_label[:100], contractor_id=contractor_id, contact_id=contact_id,
                  task_id=task_id, note=(note or "").strip(), done=False, source_ref="")
    db.add(c)
    return c


# ---------------------------------------------------------------- import z Firmao

def _dt(v) -> dt.datetime | None:
    from .firmao_archive import _dt as fdt
    return fdt(v)


def import_firmao(db: Session, company_id: int, rows: list[dict]) -> dict:
    """phonecalls.json z Firmao. Numerow i klientow API nie oddaje, wiec klient przychodzi przez zadanie."""
    have = {c.source_ref: c for c in db.scalars(select(PhoneCall).where(PhoneCall.company_id == company_id,
                                                                         PhoneCall.source_ref.like("firmao:%")))}
    tasks = {t.source_ref: t for t in db.scalars(select(Task).where(Task.company_id == company_id,
                                                                     Task.source_ref.like("firmao:%")))}
    out = {"added": 0, "updated": 0, "skipped": 0, "with_task": 0, "with_client": 0}
    for r in rows:
        if r.get("deleted") or not r.get("id"):
            out["skipped"] += 1
            continue
        started = _dt(r.get("callStartDate")) or _dt(r.get("creationDate"))
        if started is None:
            out["skipped"] += 1
            continue
        ref = f"firmao:{r['id']}"
        c = have.get(ref)
        if c is None:
            c = PhoneCall(company_id=company_id, source_ref=ref, note="", phone="")
            db.add(c)
            out["added"] += 1
        else:
            out["updated"] += 1
        d = str(r.get("direction") or "").upper()
        c.direction = "in" if d in ("IN", "TRANSIT_IN") else ("out" if d in ("OUT", "TRANSIT_OUT") else "internal")
        if str(r.get("type") or "").upper() == "INTERNAL":
            c.direction = "internal"
        st = str(r.get("status") or "").upper()
        c.status = {"ANSWERED": "answered", "BUSY": "busy", "FAILED": "failed", "NO_ANSWER": "missed",
                    "REJECTED": "missed", "CANCEL": "missed" if c.direction == "in" else "cancelled"}.get(st, "other")
        c.started_at = started
        c.seconds = int(r.get("callLength") or 0)
        number = next((str(r.get(k)) for k in ("publicNumber", "calledNumber", "sourceDestination") if r.get(k)), "")
        if number and not c.phone:
            c.phone = re.sub(r"[^\d+ ]", "", number)[:40]
        u = r.get("user") or {}
        c.user_label = str(u.get("label") or "")[:100] if isinstance(u, dict) else ""
        c.done = bool(r.get("done"))
        tid = (r.get("task") or {}).get("id") if isinstance(r.get("task"), dict) else r.get("task")
        t = tasks.get(f"firmao:{tid}") if tid else None
        c.task_id = t.id if t else None
        if t is not None:
            out["with_task"] += 1
            if c.contractor_id is None:
                c.contractor_id = t.contractor_id
        if c.contractor_id is None and c.phone:
            c.contractor_id, c.contact_id = match(db, company_id, c.phone)
        out["with_client"] += c.contractor_id is not None
    db.flush()
    return out
