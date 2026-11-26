"""Faktury cykliczne: harmonogram, tworzenie szkicu / wystawienie / wysylka mailem."""
import datetime as dt

from test_app import _new_invoice
from test_mail import FakeSmtp, _account, fake_mail  # noqa: F401


def test_schedule_math():
    from app import recurring
    assert recurring.add_months(dt.date(2027, 1, 31), 1, 0) == dt.date(2027, 2, 28)
    assert recurring.add_months(dt.date(2027, 11, 15), 3, 15) == dt.date(2028, 2, 15)
    assert recurring.first_date(dt.date(2027, 3, 20), 10) == dt.date(2027, 4, 10)
    assert recurring.first_date(dt.date(2027, 3, 5), 10) == dt.date(2027, 3, 10)
    assert recurring.fill("Abonament za {miesiac} ({okres})", dt.date(2027, 3, 1)) == "Abonament za marzec 2027 (03/2027)"


def _form(**kw):
    data = {"name": "Serwis X", "contractor_id": "", "buyer_nip": "", "recipient_name": "Firma X",
            "recipient_address": "ul. Krótka 1\n89-600 Chojnice", "interval_months": "1", "day_of_month": "10",
            "next_date": "2026-10-10", "end_date": "", "due_days": "14", "payment_method": "transfer", "mode": "draft",
            "email_to": "", "template_id": "", "active": "1", "invoicing": "1", "prices_gross": "",
            "item_name": ["Abonament za {miesiac}"], "item_qty": ["1"], "item_unit": ["mc"], "item_price": ["100,00"],
            "item_rate": ["23"], "item_product": [""], "notes": "Okres {okres}"}
    data.update(kw)
    return data


def test_recurring_draft_and_catch_up(client):
    r = client.post("/umowy/nowa", data=_form(), follow_redirects=False)
    assert r.status_code == 303, r.text
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    page = client.get(f"/umowy/{rid}").text
    assert "123,00 zł" in page and "10.10.2026" in page and "10.11.2026" in page
    assert "Serwis X" in client.get("/umowy").text
    from app import recurring
    from app.db import SessionLocal
    from app.models import Invoice, RecurringInvoice
    with SessionLocal() as db:
        made = recurring.run_due(db, today=dt.date(2026, 11, 25))
        assert made == 2  # pazdziernik i listopad
        invs = db.query(Invoice).filter_by(recurring_id=rid).order_by(Invoice.sale_date).all()
        assert [i.status for i in invs] == ["draft", "draft"]
        assert invs[0].items[0].name == "Abonament za październik 2026" and invs[0].notes == "Okres 10/2026"
        assert str(invs[1].total_gross) == "123.00" and invs[1].buyer_name == "Firma X"
        assert db.get(RecurringInvoice, rid).next_date == dt.date(2026, 12, 10)
        assert recurring.run_due(db, today=dt.date(2026, 11, 25)) == 0
    assert "Wystawione z tej umowy" in client.get(f"/umowy/{rid}").text
    # walidacja
    assert "podaj cenę" in client.post("/umowy/nowa", data=_form(item_price=[""])).text
    assert "adres e-mail" in client.post("/umowy/nowa", data=_form(mode="send")).text


def test_recurring_issue_and_send(client, fake_mail):  # noqa: F811
    _account(client)
    r = client.post("/umowy/nowa", data=_form(mode="send", email_to="klient@x.pl",
                                                  next_date=dt.date.today().isoformat(),
                                                  day_of_month=str(min(dt.date.today().day, 28))),
                    follow_redirects=False)
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    r = client.post(f"/umowy/{rid}/teraz", follow_redirects=False)
    assert r.status_code == 303 and "/faktury/" in r.headers["location"]
    page = client.get(r.headers["location"]).text
    assert "FV/1/" in page
    msg = FakeSmtp.sent[-1]
    assert msg["To"] == "klient@x.pl" and "FV/1/" in msg["Subject"]
    assert [a.get_filename() for a in msg.iter_attachments()][0].startswith("FV-1-")


def test_recurring_from_invoice(client):
    inv = _new_invoice(client, "vat")
    assert "Powtarzaj cyklicznie" in client.get(f"/faktury/{inv}").text
    form = client.get(f"/umowy/nowa?faktura={inv}").text
    assert "Jan Kowalski – co miesiąc" in form and "Usługa" in form and "2027-02-05" in form


def test_contract_number_end_and_template(client):
    from app import recurring
    from app.db import SessionLocal
    from app.models import EmailTemplate, Invoice, RecurringInvoice
    r = client.post("/umowy/nowa", data=_form(number="7/2026", name="", signed_date="2026-09-01",
                                              start_date="2026-10-01", mail_subject="Rozliczenie {numer}",
                                              mail_body="Dzień dobry, w załączniku dokument rozliczeniowy za {miesiac}.",
                                              tpl_save="new", tpl_name="Klienci prywatni"), follow_redirects=False)
    assert r.status_code == 303, r.text
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as db:
        c = db.get(RecurringInvoice, rid)
        assert c.name == "Umowa 7/2026" and c.invoicing and c.active
        t = db.get(EmailTemplate, c.template_id)
        assert t.name == "Klienci prywatni" and "rozliczeniowy" in t.body and t.kind == "recurring"
        recurring.run_due(db, today=dt.date(2026, 10, 12))
        inv = db.query(Invoice).filter_by(recurring_id=rid).one()
        assert inv.notes.startswith("Na podstawie umowy nr 7/2026 z dnia 01.09.2026.")
    page = client.get("/umowy").text
    assert "7/2026" in page and "bezterminowo" in page
    # zakonczenie umowy: brak kolejnych faktur, znika z aktywnych
    client.post(f"/umowy/{rid}/zakoncz")
    with SessionLocal() as db:
        c = db.get(RecurringInvoice, rid)
        assert not c.active and c.end_date == dt.date.today()
        assert recurring.run_due(db, today=dt.date(2027, 5, 1)) == 0
    assert "7/2026" not in client.get("/umowy").text and "7/2026" in client.get("/umowy?status=zakonczone").text
    with SessionLocal() as db:
        assert db.get(RecurringInvoice, rid).contract_status == "expired"
    assert "7/2026" in client.get("/umowy?status=s:expired").text
    client.post(f"/umowy/{rid}/wznow")
    with SessionLocal() as db:
        c = db.get(RecurringInvoice, rid)
        assert c.active and c.end_date is None and c.next_date >= dt.date.today() and c.contract_status == "active"
    # status z listy i wlasny status w ustawieniach
    client.post(f"/umowy/{rid}/status", data={"status": "accepted", "back": "/umowy"})
    with SessionLocal() as db:
        assert db.get(RecurringInvoice, rid).contract_status == "accepted"
    assert client.post(f"/umowy/{rid}/status", data={"status": "nieznany"}).status_code == 400
    page = client.get("/ustawienia/statusy-umow").text
    assert "zaakceptowana" in page and "wygasła" in page
    page = client.get("/umowy").text
    assert "szukaj statusu" in page and "zaakceptowana" in page
    # umowa bez faktur cyklicznych: bez pozycji, nic nie tworzy
    r = client.post("/umowy/nowa", data=_form(number="8/2026", invoicing="", item_name=[""], item_price=[""]),
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    rid2 = int(r.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as db:
        recurring.run_due(db, today=dt.date(2027, 5, 1))
        assert db.query(Invoice).filter_by(recurring_id=rid2).count() == 0
    # stary adres przekierowuje
    assert client.get("/cykliczne", follow_redirects=False).headers["location"] == "/umowy"
