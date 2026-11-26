"""Zadania i kalendarz: zapis z formularza, licznik czasu, widoki (na dzis, kalendarz miesiac/tydzien/dzien)."""
from __future__ import annotations

import calendar
import datetime as dt

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .models import TASK_PRIORITIES, TASK_STATUSES, Contact, Contractor, Equipment, Task

DAYS = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]
MONTHS = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień",
          "październik", "listopad", "grudzień"]


def _dt(day: str, time: str) -> dt.datetime | None:
    if not day:
        return None
    d = dt.date.fromisoformat(day)
    t = dt.time.fromisoformat(time) if time else dt.time(0, 0)
    return dt.datetime.combine(d, t)


def apply_form(db: Session, t: Task, form) -> None:
    """Przepisuje pola z formularza. ValueError z komunikatem dla uzytkownika."""
    t.title = (form.get("title") or "").strip()[:300]
    t.description = (form.get("description") or "").strip()
    t.category = (form.get("category") or "").strip()[:100]
    t.address = (form.get("address") or "").strip()[:300]
    t.assignee = (form.get("assignee") or t.assignee or "").strip()[:100]
    t.priority = form.get("priority") if form.get("priority") in TASK_PRIORITIES else "normal"
    status = form.get("status") if form.get("status") in TASK_STATUSES else (t.status or "todo")
    set_status(t, status)
    cid = form.get("contractor_id") or ""
    t.contractor_id = int(cid) if cid.isdigit() and db.get(Contractor, int(cid)) else None
    pid = form.get("contact_id") or ""
    contact = db.get(Contact, int(pid)) if pid.isdigit() else None
    t.contact_id = contact.id if contact else None
    eid = form.get("equipment_id") or ""
    e = db.get(Equipment, int(eid)) if eid.isdigit() else None
    t.equipment_id = e.id if e and e.company_id == t.company_id else None
    if e is not None and not t.contractor_id:
        t.contractor_id = e.contractor_id
    if e is not None and not t.address:
        t.address = e.location[:300]
    t.all_day = not (form.get("start_time") or form.get("due_time"))
    try:
        t.start_at = _dt(form.get("start_date") or "", form.get("start_time") or "")
        t.due_at = _dt(form.get("due_date") or "", form.get("due_time") or "")
        mins = form.get("minutes_spent")
        if mins not in (None, ""):
            t.minutes_spent = max(0, int(mins))
    except ValueError:
        raise ValueError("Sprawdź daty i godziny.") from None
    if not t.title:
        raise ValueError("Wpisz, co jest do zrobienia.")
    no_time = not form.get("due_time") or not form.get("start_time")
    if t.start_at and t.due_at and (t.due_at.date() < t.start_at.date() if no_time else t.due_at < t.start_at):
        raise ValueError("Termin jest przed początkiem.")


def set_status(t: Task, status: str, now: dt.datetime | None = None) -> None:
    now = now or dt.datetime.now()
    if status in ("done", "cancelled"):
        stop_timer(t, now)
        if t.status != status:
            t.done_at = now
    else:
        t.done_at = None
    t.status = status


def start_timer(t: Task, now: dt.datetime | None = None) -> None:
    if t.timer_started_at is None:
        t.timer_started_at = now or dt.datetime.now()
        if t.status == "todo":
            t.status = "doing"


def stop_timer(t: Task, now: dt.datetime | None = None) -> None:
    if t.timer_started_at is not None:
        t.minutes_spent = t.total_minutes(now)
        t.timer_started_at = None


def fmt_minutes(m: int) -> str:
    h, m = divmod(int(m or 0), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def open_cond(company_id: int):
    return [Task.company_id == company_id, Task.status.in_(("todo", "doing"))]


def today_tasks(db: Session, company_id: int, today: dt.date | None = None, limit: int = 200) -> list[Task]:
    """Na dzis: otwarte z terminem albo poczatkiem do konca dzisiaj (takze zalegle)."""
    today = today or dt.date.today()
    end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time(0, 0))
    return list(db.scalars(select(Task).where(*open_cond(company_id), Task.parent_id.is_(None),
                                              or_(Task.due_at < end, and_(Task.due_at.is_(None), Task.start_at < end)))
                           .order_by(Task.due_at, Task.start_at, Task.id).limit(limit)))


def in_range(db: Session, company_id: int, start: dt.date, end: dt.date, done: bool = True) -> list[Task]:
    """Zadania, ktore zaczynaja sie albo maja termin w [start, end)."""
    a, b = dt.datetime.combine(start, dt.time(0, 0)), dt.datetime.combine(end, dt.time(0, 0))
    cond = [Task.company_id == company_id, Task.status != "cancelled",
            or_(and_(Task.start_at >= a, Task.start_at < b),
                and_(Task.start_at.is_(None), Task.due_at >= a, Task.due_at < b))]
    if not done:
        cond.append(Task.status != "done")
    return list(db.scalars(select(Task).where(*cond).order_by(Task.start_at, Task.due_at, Task.id)))


def calendar_view(db: Session, company_id: int, view: str, day: dt.date) -> dict:
    """Siatka kalendarza: view = miesiac / tydzien / dzien."""
    if view == "dzien":
        start, end = day, day + dt.timedelta(days=1)
        prev, nxt = day - dt.timedelta(days=1), end
        title = f"{DAYS[day.weekday()]} {day:%d.%m.%Y}"
    elif view == "tydzien":
        start = day - dt.timedelta(days=day.weekday())
        end = start + dt.timedelta(days=7)
        prev, nxt = start - dt.timedelta(days=7), end
        title = f"{start:%d.%m} – {end - dt.timedelta(days=1):%d.%m.%Y}"
    else:
        view = "miesiac"
        first = day.replace(day=1)
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        start = first - dt.timedelta(days=first.weekday())
        end = last + dt.timedelta(days=7 - last.weekday())
        prev = (first - dt.timedelta(days=1)).replace(day=1)
        nxt = last + dt.timedelta(days=1)
        title = f"{MONTHS[first.month - 1]} {first.year}"
    by_day: dict[dt.date, list[Task]] = {}
    for t in in_range(db, company_id, start, end):
        by_day.setdefault(t.when.date(), []).append(t)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days)]
    weeks = [days[i:i + 7] for i in range(0, len(days), 7)]
    return dict(view=view, day=day, start=start, end=end, prev=prev, next=nxt, title=title, by_day=by_day,
                days=days, weeks=weeks, month=day.month, DAYS=DAYS)
