"""Oferty: numeracja, ceny, statusy, PDF, wysylka mailem, faktura i WZ z oferty, kopia, przypomnienie o waznosci."""
import datetime as dt

from test_mail import FakeSmtp, _account, fake_mail  # noqa: F401


def _offer(client, **extra):
    today = dt.date.today()
    data = {"title": "Serwis wózka", "issue_date": today.isoformat(),
            "valid_until": (today + dt.timedelta(days=14)).isoformat(), "status": "new", "probability": "60",
            "contractor_id": "", "recipient_name": "Hurt Sp. z o.o.", "recipient_address": "ul. Długa 2\n00-002 Warszawa",
            "contact_person": "Anna Nowak", "email": "hurt@test.pl", "notes": "Realizacja 3 dni.",
            "internal_notes": "Klient pyta o rabat.",
            "item_name": ["Przegląd", "Dojazd"], "item_qty": ["1", "40"], "item_unit": ["szt.", "km"],
            "item_price": ["500,00", "2,00"], "item_rate": ["23", "23"], "item_product": ["", ""]}
    data.update(extra)
    r = client.post("/oferty/nowa", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_offer_create_number_totals_pdf(client):
    from app.db import SessionLocal
    from app.models import Offer

    assert client.get("/oferty").status_code == 200
    assert 'href="/oferty"' in client.get("/").text
    oid = _offer(client)
    with SessionLocal() as db:
        o = db.get(Offer, oid)
        assert o.number.startswith("OF/1/") and o.status == "new" and o.probability == 60
        assert str(o.total_net) == "580.00" and str(o.total_gross) == "713.40"
    page = client.get(f"/oferty/{oid}").text
    assert "713,40" in page and "Klient pyta o rabat." in page
    pdf = client.get(f"/oferty/{oid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    oid2 = _offer(client)
    with SessionLocal() as db:
        assert db.get(Offer, oid2).number.startswith("OF/2/")

    # edycja nie zmienia numeru
    r = client.post(f"/oferty/{oid}/edytuj", data={"recipient_name": "Hurt Sp. z o.o.", "status": "new",
                                                   "issue_date": dt.date.today().isoformat(),
                                                   "item_name": ["Przegląd"], "item_qty": ["2"], "item_price": ["500"],
                                                   "item_rate": ["23"], "item_unit": ["szt."], "item_product": [""]},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        o = db.get(Offer, oid)
        assert o.number.startswith("OF/1/") and str(o.total_gross) == "1230.00" and len(o.items) == 1


def test_offer_validation(client):
    r = client.post("/oferty/nowa", data={"recipient_name": "X", "item_name": ["Bez ceny"], "item_qty": ["1"],
                                          "item_price": [""], "item_rate": ["23"], "item_unit": ["szt."]})
    assert "podaj cenę" in r.text
    r = client.post("/oferty/nowa", data={"recipient_name": "", "item_name": ["A"], "item_qty": ["1"],
                                          "item_price": ["1"], "item_rate": ["23"], "item_unit": ["szt."]})
    assert "Podaj klienta" in r.text
    r = client.post("/oferty/nowa", data={"recipient_name": "X", "probability": "150", "item_name": ["A"],
                                          "item_qty": ["1"], "item_price": ["1"], "item_rate": ["23"], "item_unit": ["szt."]})
    assert "od 0 do 100" in r.text


def test_offer_to_invoice_wz_copy_status_delete(client):
    from app.db import SessionLocal
    from app.models import Delivery, Invoice, Offer

    oid = _offer(client)
    r = client.post(f"/oferty/{oid}/faktura", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/edytuj")
    r = client.post(f"/oferty/{oid}/wz", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/wz/")
    with SessionLocal() as db:
        o = db.get(Offer, oid)
        assert o.status == "accepted"
        inv = db.get(Invoice, o.invoice_id)
        assert inv.status == "draft" and inv.total_gross == o.total_gross and "Do oferty" in inv.notes
        d = db.get(Delivery, o.delivery_id)
        assert d.status == "draft" and len(d.items) == 2 and d.total_gross == o.total_gross
    # drugi raz nie
    client.post(f"/oferty/{oid}/faktura")
    with SessionLocal() as db:
        assert db.query(Invoice).count() == 1
    # z faktura nie da sie usunac
    client.post(f"/oferty/{oid}/usun")
    with SessionLocal() as db:
        assert db.get(Offer, oid) is not None

    with SessionLocal() as db:
        cid = db.get(Invoice, db.get(Offer, oid).invoice_id).contractor_id
    if cid:
        assert "+ Oferta" in client.get(f"/kontrahenci/{cid}").text
    r = client.get(f"/oferty/nowa?kopia={oid}")
    assert "Przegląd" in r.text and "Hurt Sp. z o.o." in r.text

    client.post(f"/oferty/{oid}/status", data={"status": "done"})
    with SessionLocal() as db:
        assert db.get(Offer, oid).status == "done"

    oid2 = _offer(client)
    client.post(f"/oferty/{oid2}/usun")
    with SessionLocal() as db:
        assert db.get(Offer, oid2) is None
    assert "zrealizowana" in client.get("/oferty").text


def test_offer_expiry_on_dashboard(client):
    soon = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    _offer(client, valid_until=soon, recipient_name="Klient Pilny")
    _offer(client, recipient_name="Klient Spokojny")
    page = client.get("/").text
    assert "Klient Pilny" in page and "Klient Spokojny" not in page
    assert "Klient Pilny" in client.get("/oferty?status=otwarte").text


def test_send_offer_by_email(client, fake_mail):  # noqa: F811
    from app.db import SessionLocal
    from app.models import Offer

    oid = _offer(client)
    acc_id = _account(client)
    form = client.get(f"/poczta/nowa?oferta={oid}").text
    assert "Wysyłka oferty" in form and "hurt@test.pl" in form and "attach_offer" in form and "713,40" in form
    r = client.post("/poczta/nowa", data={"account_id": str(acc_id), "to": "hurt@test.pl", "cc": "",
                                          "subject": "Oferta", "body": "W załączniku.", "in_reply_to": "",
                                          "offer_id": str(oid), "attach_offer": "1"})
    assert r.status_code == 200 and "Wysłano" in r.text
    msg = FakeSmtp.sent[-1]
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert any(n and n.startswith("OF-1-") for n in names)
    with SessionLocal() as db:
        o = db.get(Offer, oid)
        assert o.status == "sent" and o.sent_at is not None


def test_offer_templates(client):
    from app.db import SessionLocal
    from app.models import OfferTemplate

    oid = _offer(client)
    r = client.post(f"/oferty/{oid}/wzor", data={"name": "Serwis standard"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        t = db.query(OfferTemplate).one()
        assert t.title == "Serwis wózka" and t.valid_days == 14
        tid = t.id
    assert "Serwis standard" in client.get("/oferty/wzory").text
    assert "Nowa ze wzoru" in client.get("/oferty").text
    page = client.get(f"/oferty/nowa?wzor={tid}").text
    assert "Przegląd" in page and "Dojazd" in page and "Realizacja 3 dni." in page and "Hurt Sp." not in page
    # ta sama nazwa nadpisuje
    client.post(f"/oferty/{oid}/wzor", data={"name": "Serwis standard"})
    client.post(f"/oferty/{oid}/wzor", data={"name": " "})
    with SessionLocal() as db:
        assert db.query(OfferTemplate).count() == 1
    client.post(f"/oferty/wzory/{tid}/usun")
    assert "Brak wzorów" in client.get("/oferty/wzory").text
