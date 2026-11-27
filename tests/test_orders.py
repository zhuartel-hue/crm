"""Zamowienia: numer jak w Firmao, status z listy, z oferty, faktura i WZ z zamowienia; polaczenia przy kliencie."""
import datetime as dt

from test_offers import _offer


def _order(client, **extra):
    today = dt.date.today()
    data = {"title": "Kamery Kowalski", "direction": "sale", "status": "new", "issue_date": today.isoformat(),
            "due_date": (today + dt.timedelta(days=5)).isoformat(), "customer_ref": "PO-7", "contractor_id": "",
            "recipient_name": "Jan Kowalski", "recipient_address": "ul. Krótka 1", "phone": "+48 600 100 200",
            "item_name": ["Kamera", "Montaż"], "item_qty": ["2", "1"], "item_unit": ["szt.", "usł."],
            "item_price": ["300,00", "200"], "item_rate": ["23", "23"], "item_product": ["", ""]}
    data.update(extra)
    r = client.post("/zamowienia/nowe", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_order_flow(client):
    from app.db import SessionLocal
    from app.models import Delivery, Invoice, Offer, Order

    assert client.get("/zamowienia").status_code == 200
    zid = _order(client)
    with SessionLocal() as db:
        z = db.get(Order, zid)
        today = dt.date.today()
        assert z.number == f"{today:%Y}/{today:%m}/001/Z/S" and str(z.total_gross) == "984.00"
        assert z.status_label == "nowe" and z.is_open and not z.overdue
    page = client.get("/zamowienia").text
    assert z.number in page and "szukaj statusu" in page and "Kamery Kowalski" in page
    client.post(f"/zamowienia/{zid}/status", data={"status": "ready", "back": "/zamowienia"})
    with SessionLocal() as db:
        assert db.get(Order, zid).status == "ready"
    assert client.post(f"/zamowienia/{zid}/status", data={"status": "xyz"}, follow_redirects=False).status_code == 303
    # faktura i WZ z zamowienia
    r = client.post(f"/zamowienia/{zid}/faktura", follow_redirects=False)
    assert r.status_code == 303 and "/faktury/" in r.headers["location"]
    client.post(f"/zamowienia/{zid}/wz")
    with SessionLocal() as db:
        z = db.get(Order, zid)
        inv, d = db.get(Invoice, z.invoice_id), db.get(Delivery, z.delivery_id)
        assert inv.status == "draft" and "Do zamówienia" in inv.notes and "PO-7" in inv.notes and len(inv.items) == 2
        assert d.kind == "wz" and d.status == "draft"
    # nie da sie usunac z faktura
    client.post(f"/zamowienia/{zid}/usun")
    with SessionLocal() as db:
        assert db.get(Order, zid) is not None
    # z oferty: pozycje przechodza, oferta "zamowiona"
    oid = _offer(client)
    form = client.get(f"/zamowienia/nowe?oferta={oid}").text
    assert "Przegląd" in form and f'name="offer_id" value="{oid}"' in form
    zid2 = _order(client, offer_id=str(oid), item_name=["Przegląd"], item_qty=["1"], item_unit=["szt."],
                  item_price=["500"], item_rate=["23"], item_product=[""])
    with SessionLocal() as db:
        assert db.get(Order, zid2).offer_id == oid and db.get(Offer, oid).status == "ordered"
        assert db.get(Order, zid2).number.endswith("/002/Z/S")
    client.post(f"/zamowienia/{zid2}/usun")
    with SessionLocal() as db:
        assert db.get(Order, zid2) is None
    # do dostawcy: bez cen, PZ zamiast WZ, bez faktury sprzedazy
    zid3 = _order(client, direction="purchase", item_price=["", ""], recipient_name="Hurtownia")
    client.post(f"/zamowienia/{zid3}/faktura")
    client.post(f"/zamowienia/{zid3}/wz")
    with SessionLocal() as db:
        z = db.get(Order, zid3)
        assert z.invoice_id is None and db.get(Delivery, z.delivery_id).kind == "pz"
    # statusy zamowien w ustawieniach, kafelek na Start
    assert "gotowe do wydania" in client.get("/ustawienia/statusy-zamowien").text
    assert "Zamówienia z terminem" in client.get("/").text
    # inna firma nie widzi
    client.post("/firma/2")
    assert client.get(f"/zamowienia/{zid}").status_code == 404
    client.post("/firma/1")


def test_order_validation_and_pattern(client):
    r = client.post("/zamowienia/nowe", data={"recipient_name": "", "item_name": ["A"], "item_qty": ["1"],
                                              "item_price": ["1"], "item_rate": ["23"], "item_unit": ["szt."]})
    assert "Podaj klienta" in r.text
    r = client.post("/zamowienia/nowe", data={"recipient_name": "X", "item_name": [""]})
    assert "nie ma pozycji" in r.text
    assert 'name="pattern_order"' in client.get("/ustawienia").text


def test_calls_with_contractor(client):
    from app.db import SessionLocal
    from app.models import Contractor, PhoneCall

    client.post("/kontrahenci/nowy", data={"name": "Telefoniczny Klient", "is_company": "1", "phone": "600 700 800"})
    with SessionLocal() as db:
        cid = db.query(Contractor).filter_by(name="Telefoniczny Klient").one().id
    # numer z kierunkowym: klient znaleziony sam
    client.post("/polaczenia/nowe", data={"direction": "in", "status": "answered", "phone": "+48 600-700-800",
                                          "note": "Pyta o przegląd", "minutes": "2,5"})
    with SessionLocal() as db:
        c = db.query(PhoneCall).one()
        assert c.contractor_id == cid and c.seconds == 150 and c.duration == "2:30"
    page = client.get(f"/kontrahenci/{cid}").text
    assert "Rozmowy telefoniczne" in page and "Pyta o przegląd" in page and "Zamówienia" in page
    assert "Pyta o przegląd" in client.get("/polaczenia").text
    assert "Telefoniczny Klient" in client.get("/polaczenia?q=700800").text
    client.post(f"/polaczenia/{c.id}", data={"note": "Umówiony na piątek"})
    with SessionLocal() as db:
        assert db.get(PhoneCall, c.id).note == "Umówiony na piątek"
