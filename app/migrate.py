"""Migracje bazy przy starcie programu.

Baza z etapu 1 i 2 powstala przez create_all, bez tabeli alembic_version: dostaje znacznik 0001
(ten sam stan) i od tego miejsca idzie juz zwyklymi migracjami. Kopia bazy przed aktualizacja:
deploy/install.sh i nocna kopia na iMacu.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from .db import engine

ROOT = Path(__file__).resolve().parent.parent


def upgrade() -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "companies" in tables and "alembic_version" not in tables:
            command.stamp(cfg, "0001")
        command.upgrade(cfg, "head")
