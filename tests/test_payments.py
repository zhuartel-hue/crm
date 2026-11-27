"""Naleznosci: wplaty czesciowe, lista po terminie, przypomnienia mailem."""
import datetime as dt

from test_app import _new_invoice
from test_mail import FakeSmtp, _account, fake_mail  # noqa: F401


def _overdue_invoice(client, days=20, price="1230,00", buyer="Hurt Sp. z o.o."):
    issue = dt.date.today() - dt.timedelta(days=days + 14)
    due = dt.date.today() - dt.timedelta(days=days)
    return _new_invoice(client, "vat", issue_date=issue.isoformat(), sale_date=issue.isoformat(),
                        due_date=due.isoformat(), item_price=[price], buyer_name=buyer)


def test_partial_payments_and_receivables(client):
    late = _overdue_invoice(client)
    soon = _new_invoice(client, "vat", issue_date=dt.date.today().isoformat(), sale_date=dt.date.today().isoformat(),
                        due_date=(dt.date.today() + dt.timedelta(days=3)).isoformat(), buyer_name="Jan Nowak")
    page = client.get("/naleznosci").text
    assert "1\xa0230,00" in page and "20 dni po terminie" in page and "Jan Nowak" in page
    assert "Hurt Sp. z o.o." not in client.get("/naleznosci?filtr=7-dni").text
    assert "Jan Nowak" not in client.get("/naleznosci?filtr=po-terminie").text
    # wplata czesciowa
    r = client.post(f"/faktury/{late}/wplata", data={"amount": "230,00", "date": dt.date.today().isoformat(),
                                                    "method": "cash", "note": "zaliczka"})
    assert "Pozostało 1\xa0000,00 zł" in r.text
    view = client.get(f"/faktury/{late}").text
    assert "Gotówka" in view and "zaliczka" in view and "pozostało <b>1\xa0000,00 zł</b>" in view
    assert "1\xa0000,00" in client.get("/naleznosci").text
    # reszta: oznacz jako zaplacona
    client.post(f"/faktury/{late}/zaplacona")
    assert "Hurt Sp. z o.o." not in client.get("/naleznosci").text
    from app.db import SessionLocal
    from app.models import Invoice, InvoicePayment
    with SessionLocal() as db:
        inv = db.get(Invoice, late)
        assert inv.to_pay == 0 and len(inv.payments) == 2
        pid = inv.payments[0].id
    # usuniecie wplaty przywraca naleznosc
    client.post(f"/faktury/{late}/wplata/{pid}/usun")
    with SessionLocal() as db:
        assert str(db.get(Invoice, late).to_pay) == "230.00"
        assert db.query(InvoicePayment).count() == 1
    # bledy
    assert "Podaj kwotę" in client.post(f"/faktury/{soon}/wplata", data={"amount": ""}).text
    client.post("/firma/2")
    assert "Jan Nowak" not in client.get("/naleznosci").text


def test_reminder_email(client, fake_mail):  # noqa: F811
    late = _overdue_invoice(client, days=5)
    acc_id = _account(client)
    assert "Przypomnij o płatności" in client.get(f"/faktury/{late}").text
    form = client.get(f"/poczta/nowa?faktura={late}&przypomnienie=1").text
    assert "Przypomnienie o płatności" in form and "5 dni temu" in form and 'name="reminder" value="1"' in form
    r = client.post("/poczta/nowa", data={"account_id": str(acc_id), "to": "hurt@test.pl", "cc": "",
                                          "subject": "Przypomnienie o płatności", "body": "Prosimy o wpłatę.",
                                          "in_reply_to": "", "invoice_id": str(late), "attach_invoice": "1",
                                          "reminder": "1"})
    assert "Wysłano przypomnienie do hurt@test.pl" in r.text
    assert FakeSmtp.sent[-1]["To"] == "hurt@test.pl"
    assert "przypomnienia: 1" in r.text
    assert "1 · " in client.get("/naleznosci").text
