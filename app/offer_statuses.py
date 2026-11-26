"""Statusy ofert ustawiane przez firme: nazwa, kolor, grupa (otwarta / wygrana / przegrana), kolejnosc, ukrycie.
Wbudowane statusy (1:1 jak w Firmao) mozna przemianowac, przestawic i ukryc, ale nie usunac, bo opiera sie
na nich logika (np. "wysłana" po mailu, "zaakceptowana" po fakturze z oferty). Wlasne statusy mozna usuwac,
gdy zadna oferta ich nie uzywa. Zapis w app_settings, osobno dla kazdej firmy."""
from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import OFFER_LEGACY, OFFER_OPEN, OFFER_STATUSES, OFFER_WON, AppSetting, Offer

GROUPS = {"open": "otwarta", "won": "wygrana", "lost": "przegrana"}
COLORS = {"": "szary", "m": "niebieski", "ok": "zielony", "warn": "żółty", "due": "czerwony"}
MAX = 40


def _key(company_id: int) -> str:
    return f"offer_statuses:{company_id}"


def defaults() -> list[dict]:
    out = []
    for k, label in OFFER_STATUSES.items():
        group = "open" if k in OFFER_OPEN else ("won" if k in OFFER_WON else "lost")
        color = "ok" if group == "won" else ("due" if group == "lost" else ("m" if k == "sent" else ""))
        out.append({"k": k, "label": label, "group": group, "color": color, "hidden": False, "builtin": True})
    return out


def config(db: Session, company_id: int) -> list[dict]:
    cache = db.info.setdefault("offer_statuses", {})
    if company_id in cache:
        return cache[company_id]
    rows = defaults()
    saved = db.get(AppSetting, _key(company_id))
    if saved is not None:
        try:
            data = json.loads(saved.value)
            by_k = {r["k"]: r for r in rows}
            out = []
            for it in data:
                k = str(it.get("k") or "")[:12]
                base = by_k.pop(k, None)
                if base is None and not k.startswith("c"):
                    continue
                r = dict(base or {"k": k, "builtin": False})
                r["label"] = (str(it.get("label") or "").strip() or (base or {}).get("label") or k)[:60]
                r["group"] = it.get("group") if it.get("group") in GROUPS else (base or {}).get("group", "open")
                r["color"] = it.get("color") if it.get("color") in COLORS else ""
                r["hidden"] = bool(it.get("hidden"))
                out.append(r)
            rows = out + list(by_k.values())  # nowe wbudowane statusy na koncu
        except (ValueError, TypeError, AttributeError):
            pass
    cache[company_id] = rows
    return rows


def find(db: Session, company_id: int, key: str) -> dict | None:
    return next((r for r in config(db, company_id) if r["k"] == key), None)


def label(db: Session, company_id: int, key: str) -> str:
    r = find(db, company_id, key)
    return r["label"] if r else OFFER_LEGACY.get(key, key)


def keys(db: Session, company_id: int, group: str) -> list[str]:
    return [r["k"] for r in config(db, company_id) if r["group"] == group]


def visible(db: Session, company_id: int, current: str | None = None) -> dict[str, str]:
    """Statusy do wyboru (bez ukrytych; aktualny status oferty zawsze widoczny)."""
    return {r["k"]: r["label"] for r in config(db, company_id) if not r["hidden"] or r["k"] == current}


def usage(db: Session, company_id: int) -> dict[str, int]:
    return dict(db.execute(select(Offer.status, func.count()).where(Offer.company_id == company_id)
                           .group_by(Offer.status)).all())


def save(db: Session, company_id: int, rows: list[dict]) -> None:
    """rows: kolejnosc docelowa; pozycje z delete=True usuwane (tylko wlasne i nieuzywane)."""
    used = usage(db, company_id)
    current = {r["k"]: r for r in config(db, company_id)}
    out, n_custom = [], max([int(k[1:]) for k in current if k.startswith("c") and k[1:].isdigit()] or [0])
    for it in rows:
        k = it.get("k") or ""
        if k and k not in current:
            continue
        if it.get("delete"):
            if not k:
                continue
            if current[k].get("builtin"):
                raise ValueError(f"Status „{current[k]['label']}” jest wbudowany. Możesz go ukryć, ale nie usunąć.")
            if used.get(k):
                raise ValueError(f"Status „{current[k]['label']}” ma {used[k]} ofert. Zmień im status, potem usuń.")
            continue
        name = (it.get("label") or "").strip()[:60]
        if not k:
            if not name:
                continue
            n_custom += 1
            k = f"c{n_custom}"
        if not name:
            raise ValueError("Każdy status musi mieć nazwę.")
        out.append({"k": k, "label": name, "group": it.get("group") if it.get("group") in GROUPS else "open",
                    "color": it.get("color") if it.get("color") in COLORS else "", "hidden": bool(it.get("hidden"))})
    if len(out) > MAX:
        raise ValueError(f"Najwyżej {MAX} statusów.")
    if not any(not r["hidden"] for r in out):
        raise ValueError("Zostaw przynajmniej jeden widoczny status.")
    row = db.get(AppSetting, _key(company_id)) or AppSetting(key=_key(company_id))
    row.value = json.dumps(out, ensure_ascii=False)
    db.add(row)
    db.info.get("offer_statuses", {}).pop(company_id, None)


def reset(db: Session, company_id: int) -> None:
    used = usage(db, company_id)
    busy = [r["label"] for r in config(db, company_id) if not r.get("builtin") and used.get(r["k"])]
    if busy:
        raise ValueError("Własne statusy mają jeszcze oferty: " + ", ".join(busy) + ". Najpierw zmień im status.")
    row = db.get(AppSetting, _key(company_id))
    if row is not None:
        db.delete(row)
    db.info.get("offer_statuses", {}).pop(company_id, None)
