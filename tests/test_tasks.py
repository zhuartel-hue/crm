"""Zadania i kalendarz: dodawanie, na dzis, podzadania, licznik, zlecenie serwisowe ze sprzetu."""
import datetime as dt


def test_tasks_flow(client):
    today = dt.date.today()
    from test_equipment import _contractor
    cid = _contractor(client)
    # szybkie dodanie na dzis
    client.post("/zadania/szybkie", data={"title": "Zadzwonić do hotelu", "due_date": today.isoformat(), "back": "/zadania"})
    assert "Zadzwonić do hotelu" in client.get("/zadania").text
    assert "Zadzwonić do hotelu" in client.get("/").text
    # pelne zadanie z godzina i klientem
    r = client.post("/zadania/nowe", data={"title": "Montaż kamer", "contractor_id": str(cid), "priority": "high",
                                           "start_date": (today + dt.timedelta(days=2)).isoformat(), "start_time": "09:30",
                                           "due_date": (today + dt.timedelta(days=2)).isoformat(), "due_time": "15:00",
                                           "address": "ul. Lipowa 1"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    tid = int(r.headers["location"].rsplit("/", 1)[1])
    page = client.get(f"/zadania/{tid}").text
    assert "Montaż kamer" in page and "maps.google.com" in page
    assert "Montaż kamer" not in client.get("/zadania").text  # nie na dzis
    assert "Montaż kamer" in client.get("/zadania?widok=otwarte").text
    assert "Montaż kamer" in client.get(f"/kontrahenci/{cid}").text
    for w in ("miesiac", "tydzien"):
        assert "Montaż kamer" in client.get(f"/kalendarz?widok={w}&dzien={(today + dt.timedelta(days=2)).isoformat()}").text
    assert "09:30" in client.get(f"/kalendarz?widok=dzien&dzien={(today + dt.timedelta(days=2)).isoformat()}").text
    # podzadania i licznik
    client.post("/zadania/szybkie", data={"title": "Zamówić kamery", "parent_id": str(tid), "back": f"/zadania/{tid}"})
    from app.db import SessionLocal
    from app.models import Task
    with SessionLocal() as db:
        sub = db.get(Task, tid).subtasks[0]
        assert sub.title == "Zamówić kamery" and sub.contractor_id == cid
        sid = sub.id
    client.post(f"/zadania/{tid}/licznik")
    with SessionLocal() as db:
        t = db.get(Task, tid)
        assert t.timer_started_at and t.status == "doing"
        t.timer_started_at -= dt.timedelta(minutes=95)
        db.commit()
    client.post(f"/zadania/{tid}/status", data={"status": "done"})
    with SessionLocal() as db:
        t = db.get(Task, tid)
        assert t.status == "done" and t.timer_started_at is None and t.minutes_spent >= 95 and t.done_at
    assert "Montaż kamer" in client.get("/zadania?widok=zrobione").text
    assert "Wpisz, co jest do zrobienia" in client.post("/zadania/nowe", data={"title": ""}).text
    client.post(f"/zadania/{tid}/usun")
    with SessionLocal() as db:
        assert db.get(Task, tid) is None and db.get(Task, sid) is None


def test_service_order_from_equipment(client):
    from test_equipment import _contractor
    cid = _contractor(client)
    r = client.post("/sprzet/nowy", data={"name": "Centrala alarmowa", "contractor_id": str(cid), "location": "biuro"},
                    follow_redirects=False)
    eid = int(r.headers["location"].rsplit("/", 1)[1])
    page = client.get(f"/zadania/nowe?sprzet={eid}").text
    assert "Serwis: Centrala alarmowa" in page and "biuro" in page
    client.post("/zadania/nowe", data={"title": "Serwis: Centrala alarmowa", "equipment_id": str(eid), "category": "serwis"})
    assert "Serwis: Centrala alarmowa" in client.get(f"/sprzet/{eid}").text
    from app.db import SessionLocal
    from app.models import Task
    with SessionLocal() as db:
        t = db.query(Task).filter_by(equipment_id=eid).one()
        assert t.contractor_id == cid and t.address == "biuro"
