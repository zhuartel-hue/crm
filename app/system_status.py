"""Stan wdrozen na serwerze (wersja produkcji i panelu testowego), z plikow pisanych przez deploy/serwer.

Pliki leza w /var/lib/sygnavo/stan na serwerze i sa widoczne w kontenerze jako /stan (tylko do odczytu).
Widzi to tylko organizacja wlasciciela platformy (pierwsza), bo to informacja o serwerze, nie o kliencie.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import Organisation

STAN_DIR = Path(os.environ.get("STAN_DIR", "/stan"))
LOG_LINES = 30


def platform_org_id(db: Session) -> int | None:
    return db.scalar(select(Organisation.id).order_by(Organisation.id).limit(1))


def _json(name: str) -> dict | None:
    try:
        return json.loads((STAN_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def status() -> dict:
    try:
        log = (STAN_DIR / "wydania.log").read_text(encoding="utf-8").splitlines()[-LOG_LINES:]
    except OSError:
        log = []
    return {"this_app": {"version": config.APP_VERSION, "test_panel": config.TEST_PANEL},
            "production": _json("produkcja.json"), "test_panel": _json("test.json"),
            "github": _json("github.json"), "deploy_log": log}
