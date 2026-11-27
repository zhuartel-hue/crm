"""Zadania w tle (app/jobs.py): wspolny mechanizm dla watkow w aplikacji i osobnego workera."""
import threading
import time

from app import jobs


def test_all_jobs_respect_config():
    # conftest wylacza zadania (0), wiec start nic nie uruchamia
    assert jobs.start() is None
    names = [j.name for j in jobs.all_jobs()]
    assert names == ["mail-sync", "recurring", "ksef-purchases", "bank-sync"]


def test_loop_runs_job_and_stops():
    calls = []
    job = jobs.Job("test", 0.05, 0, lambda db: calls.append(1))
    stop = jobs.start([job])
    time.sleep(0.2)
    stop.set()
    assert len(calls) >= 2


def test_failing_job_does_not_kill_loop():
    calls = []

    def boom(db):
        calls.append(1)
        raise RuntimeError("awaria")

    stop = jobs.start([jobs.Job("boom", 0.05, 0, boom)])
    time.sleep(0.2)
    stop.set()
    assert len(calls) >= 2


def test_same_job_never_runs_twice_at_once():
    """W PostgreSQL blokada doradcza; w SQLite (testy) zadanie po prostu sie wykonuje."""
    from app.db import engine

    if engine.dialect.name != "postgresql":
        assert jobs.run_once(jobs.Job("x", 1, 0, lambda db: None)) is True
        return
    started, release = threading.Event(), threading.Event()

    def slow(db):
        started.set()
        release.wait(5)

    job = jobs.Job("wolne", 1, 0, slow)
    t = threading.Thread(target=jobs.run_once, args=(job,))
    t.start()
    started.wait(5)
    assert jobs.run_once(job) is False
    release.set()
    t.join()
    assert jobs.run_once(jobs.Job("wolne", 1, 0, lambda db: None)) is True
