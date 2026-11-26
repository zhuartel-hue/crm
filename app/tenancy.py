"""Oddzielenie danych organizacji w jednym miejscu (etap B, zanim wlaczymy RLS w Postgresie).

Dla zapytania od zalogowanej osoby middleware ustawia liste firm jej organizacji. Kazde zapytanie ORM
(select, Session.get, ladowanie relacji) dostaje wtedy automatycznie warunek company_id IN (te firmy)
dla wszystkich tabel z kolumna company_id oraz id IN (te firmy) dla tabeli firm. Kod modulow nie musi
o tym pamietac. Zadania w tle (poczta, bank, KSeF) dzialaja bez tego filtra, bo obsluguja wszystkie firmy.

Tabele podrzedne bez company_id (pozycje faktur, zalaczniki maili) sa chronione przez rekord nadrzedny;
ich bezposrednie pobieranie po id sprawdza test izolacji (tests/test_users.py).
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager

from sqlalchemy import event, or_
from sqlalchemy.orm import Session, with_loader_criteria

_company_ids: contextvars.ContextVar[tuple[int, ...] | None] = contextvars.ContextVar("company_ids", default=None)


def set_scope(ids) -> contextvars.Token:
    return _company_ids.set(tuple(ids) if ids is not None else None)


def reset_scope(token: contextvars.Token) -> None:
    _company_ids.reset(token)


@contextmanager
def scope(ids):
    token = set_scope(ids)
    try:
        yield
    finally:
        reset_scope(token)


def current_scope() -> tuple[int, ...] | None:
    return _company_ids.get()


def _scoped_classes():
    from .db import Base
    from .models import Company

    out = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if cls is Company:
            continue
        col = mapper.columns.get("company_id")
        if col is not None:
            out.append((cls, bool(col.nullable)))
    return out


_CLASSES: list | None = None


@event.listens_for(Session, "do_orm_execute")
def _add_tenant_criteria(state) -> None:
    ids = _company_ids.get()
    if ids is None or not state.is_select or state.execution_options.get("skip_tenant_filter"):
        return
    global _CLASSES
    if _CLASSES is None:
        _CLASSES = _scoped_classes()
    from .models import Company

    opts = [with_loader_criteria(Company, lambda cls: cls.id.in_(ids), include_aliases=True)]
    for cls, nullable in _CLASSES:
        if nullable:
            opts.append(with_loader_criteria(
                cls, lambda c: or_(c.company_id.is_(None), c.company_id.in_(ids)), include_aliases=True))
        else:
            opts.append(with_loader_criteria(cls, lambda c: c.company_id.in_(ids), include_aliases=True))
    state.statement = state.statement.options(*opts)
