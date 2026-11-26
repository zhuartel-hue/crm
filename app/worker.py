"""Osobny proces zadan w tle: `python -m app.worker`.

Migracje bazy robi aplikacja przy starcie; worker czeka, az baza bedzie w najnowszej wersji.
"""
from __future__ import annotations

import logging
import signal
import threading
import time

from . import jobs


def _db_ready() -> bool:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from .db import engine
    from .migrate import ROOT

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log = logging.getLogger("sygnavo.worker")
    while not _db_ready():
        log.info("Czekam na aktualną wersję bazy (migracje robi aplikacja)")
        time.sleep(10)
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    stop = jobs.start()
    log.info("Worker działa")
    done.wait()
    if stop is not None:
        stop.set()
    log.info("Worker zatrzymany")


if __name__ == "__main__":
    main()
