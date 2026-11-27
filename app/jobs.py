"""Zadania w tle: poczta, faktury cykliczne, faktury kosztowe z KSeF, bank.

Domyslnie (BACKGROUND_JOBS=app) dzialaja jako watki w procesie aplikacji, jak dotad. Przed
uruchomieniem kilku procesow aplikacji ustawiamy BACKGROUND_JOBS=off w aplikacji i wlaczamy osobny
proces `python -m app.worker` (usluga "worker" w deploy/serwer/docker-compose.yml). Kazde uruchomienie
zadania bierze blokade w PostgreSQL (pg_try_advisory_lock), wiec nawet przy dwoch procesach to samo
zadanie nie wykona sie dwa razy naraz.
"""
from __future__ import annotations

import logging
import threading
import zlib
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import text

from . import config
from .db import SessionLocal, engine


@dataclass(frozen=True)
class Job:
    name: str
    interval: float  # sekundy; <= 0 wylacza zadanie
    first_delay: float | None  # None = pierwszy raz po interval
    run: Callable


def _mail(db):
    from .mail import service as mail
    mail.sync_all(db)


def _recurring(db):
    from . import recurring
    recurring.run_due(db)


def _purchases(db):
    from .ksef import purchases
    purchases.fetch_all(db)


def _bank(db):
    from .bank import connect as bank_connect
    bank_connect.sync_all(db)


def _integrations(db):
    from .integrations import service as integrations
    integrations.sync_all(db)


def all_jobs() -> list[Job]:
    return [
        Job("mail-sync", config.MAIL_SYNC_MINUTES * 60, None, _mail),
        Job("recurring", config.RECURRING_CHECK_MINUTES * 60, 0, _recurring),
        Job("ksef-purchases", config.KSEF_PURCHASE_SYNC_HOURS * 3600, None, _purchases),
        Job("bank-sync", config.BANK_SYNC_HOURS * 3600, 120, _bank),
        Job("integrations-sync", config.INTEGRATIONS_SYNC_MINUTES * 60, 180, _integrations),
    ]


def run_once(job: Job) -> bool:
    """Jedno wykonanie zadania pod blokada. False = inny proces wlasnie je wykonuje."""
    log = logging.getLogger(f"sygnavo.{job.name}")
    if engine.dialect.name != "postgresql":
        with SessionLocal() as db:
            job.run(db)
        return True
    key = zlib.crc32(f"sygnavo-job:{job.name}".encode())
    with engine.connect() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar():
            log.info("Zadanie %s działa już w innym procesie, pomijam", job.name)
            return False
        try:
            with SessionLocal() as db:
                job.run(db)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            conn.commit()
    return True


def _loop(job: Job, stop: threading.Event) -> None:
    log = logging.getLogger(f"sygnavo.{job.name}")
    delay = job.interval if job.first_delay is None else job.first_delay
    while not stop.wait(delay):
        try:
            run_once(job)
        except Exception:  # noqa: BLE001
            log.exception("Zadanie %s nie powiodło się", job.name)
        delay = job.interval


def start(jobs: list[Job] | None = None) -> threading.Event | None:
    """Uruchamia watki zadan. Zwraca zdarzenie do zatrzymania albo None, gdy nic nie wystartowalo."""
    stop = threading.Event()
    started = False
    for job in jobs if jobs is not None else all_jobs():
        if job.interval <= 0:
            continue
        threading.Thread(target=_loop, args=(job, stop), name=job.name, daemon=True).start()
        started = True
    return stop if started else None
