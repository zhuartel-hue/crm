"""Sprzet i serwis: urzadzenie u klienta, gwarancja, przeglady, historia serwisu."""
import datetime as dt


def _contractor(client):
    from app.db import SessionLocal
    from app.models import Contractor
    with SessionLocal() as db:
        c = Contractor(name="Hotel Pod Lipą", nip="5551112233", city="Chojnice", street="ul. Lipowa 1",
                       postal_code="89-600")
        db.add(c)
        db.commit()
        return c.id


def test_equipment_flow(client):
    cid = _contractor(client)
    assert "Hotel Pod Lipą" in client.get(f"/sprzet/nowy?kontrahent={cid}").text
    soon = (dt.date.today() + dt.timedelta(days=10)).isoformat()
    r = client.post("/sprzet/nowy", data={"name": "Rejestrator NVR 16", "category": "rejestrator", "serial_no": "SN-778",
                                          "inventory_no": "INW/1", "barcode": "590123", "contractor_id": str(cid),
                                          "location": "recepcja", "warranty_until": soon, "warranty_remind_days": "30",
                                          "service_interval_months": "12", "install_date": "2026-01-15", "price": "1 200,50"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    eid = int(r.headers["location"].rsplit("/", 1)[1])
    page = client.get(f"/sprzet/{eid}").text
    assert "SN-778" in page and "gwarancja do" in page
    from app.db import SessionLocal
    from app.models import Equipment
    with SessionLocal() as db:
        e = db.get(Equipment, eid)
        assert e.next_service == dt.date(2027, 1, 15) and str(e.price) == "1200.50"
    # wyszukiwanie po numerze seryjnym, kodzie i kliencie; widok gwarancji
    for q in ("SN-778", "590123", "Lipą"):
        assert "Rejestrator NVR 16" in client.get(f"/sprzet?q={q}").text
    assert "Rejestrator NVR 16" in client.get("/sprzet?widok=gwarancje").text
    assert "Rejestrator NVR 16" in client.get(f"/kontrahenci/{cid}").text
    # przeglad przesuwa termin
    client.post(f"/sprzet/{eid}/serwis", data={"date": "2026-11-20", "kind": "przeglad", "description": "Czyszczenie, test dysków"})
    with SessionLocal() as db:
        e = db.get(Equipment, eid)
        assert e.next_service == dt.date(2027, 11, 20) and e.services[0].description == "Czyszczenie, test dysków"
        sid = e.services[0].id
    assert "Czyszczenie, test dysków" in client.get(f"/sprzet/{eid}").text
    client.post(f"/sprzet/{eid}/serwis/{sid}/usun")
    # wycofanie
    client.post(f"/sprzet/{eid}", data={"name": "Rejestrator NVR 16", "active": ["1", "0"], "contractor_id": str(cid)})
    assert "Rejestrator NVR 16" not in client.get("/sprzet").text
    assert "Rejestrator NVR 16" in client.get("/sprzet?widok=wycofane").text
    assert "Podaj nazwę" in client.post("/sprzet/nowy", data={"name": ""}).text
    client.post(f"/sprzet/{eid}/usun")
    with SessionLocal() as db:
        assert db.get(Equipment, eid) is None
