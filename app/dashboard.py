"""Strona Start: kafelki do wyboru, ich kolejnosc, szerokosc i wysokosc. Uklad zapisany osobno dla kazdej firmy
i kazdego uzytkownika (app_settings), wiec kazdy klient Sygnavo ustawia Start po swojemu."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .models import AppSetting

COLS = 4  # szerokosc siatki na komputerze
MAX_H = 6  # wysokosc w rzedach po ok. 120 px

# klucz -> (nazwa w edytorze, domyslna szerokosc, domyslna wysokosc)
WIDGETS: dict[str, tuple[str, int, int]] = {
    "stat_month": ("Sprzedaż w tym miesiącu", 1, 1),
    "stat_unpaid": ("Nieopłacone", 1, 1),
    "stat_overdue": ("Po terminie (liczba)", 1, 1),
    "stat_ksef": ("Do zatwierdzenia w KSeF", 1, 1),
    "tasks": ("Zadania na dziś", 2, 3),
    "invoices": ("Ostatnie faktury", 2, 3),
    "mails": ("Ostatnie maile", 2, 3),
    "wz": ("Ostatnie WZ", 2, 3),
    "overdue": ("Po terminie (lista)", 2, 3),
    "offers": ("Oferty do przypomnienia", 2, 3),
    "orders": ("Zamówienia z terminem", 2, 3),
    "equipment": ("Przeglądy i gwarancje", 2, 3),
    "cars": ("Auta na placu", 2, 3),
}
DEFAULT_ORDER = ["stat_month", "stat_unpaid", "stat_overdue", "stat_ksef", "tasks", "invoices", "mails", "wz",
                 "overdue", "offers", "orders", "equipment"]


def _key(company_id: int, user: str) -> str:
    return f"start:{company_id}:{user}"[:60]


def default_layout(has_cars: bool = False) -> list[dict]:
    keys = DEFAULT_ORDER + (["cars"] if has_cars else [])
    return [{"k": k, "w": WIDGETS[k][1], "h": WIDGETS[k][2]} for k in keys]


def clean(items) -> list[dict]:
    """Tylko znane kafelki, kazdy raz, rozmiary w granicach."""
    out, seen = [], set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        k = it.get("k")
        if k not in WIDGETS or k in seen:
            continue
        seen.add(k)
        try:
            w, h = int(it.get("w", WIDGETS[k][1])), int(it.get("h", WIDGETS[k][2]))
        except (TypeError, ValueError):
            w, h = WIDGETS[k][1], WIDGETS[k][2]
        out.append({"k": k, "w": min(max(w, 1), COLS), "h": min(max(h, 1), MAX_H)})
    return out


def load(db: Session, company_id: int, user: str, has_cars: bool = False) -> tuple[list[dict], bool]:
    """(uklad, czy_wlasny)."""
    row = db.get(AppSetting, _key(company_id, user))
    if row is not None:
        try:
            return clean(json.loads(row.value)), True
        except ValueError:
            pass
    return default_layout(has_cars), False


def save(db: Session, company_id: int, user: str, items) -> list[dict]:
    layout = clean(items)
    key = _key(company_id, user)
    row = db.get(AppSetting, key) or AppSetting(key=key)
    row.value = json.dumps(layout)
    db.add(row)
    return layout


def reset(db: Session, company_id: int, user: str) -> None:
    row = db.get(AppSetting, _key(company_id, user))
    if row is not None:
        db.delete(row)
