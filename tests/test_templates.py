"""Szablony maili: domyslne, edycja z podgladem, wybor przy wysylce."""
from test_app import _new_invoice
from test_mail import _account, fake_mail  # noqa: F401


def test_templates_crud_and_compose(client, fake_mail):  # noqa: F811
    page = client.get("/szablony").text
    assert "Wysyłka faktury" in page and "Przypomnienie o płatności" in page and "domyślny" in page
    # nowy szablon z podgladem
    form = client.get("/szablony/nowy?rodzaj=invoice").text
    assert "{do_zaplaty}" in form and "Podgląd" in form
    r = client.post("/szablony/nowy", data={"name": "Krótki", "kind": "invoice", "subject": "FV {numer}",
                                            "body": "Kwota {kwota} zł, {nieznany}", "is_default": "1"})
    assert "Nieznane znaczniki: {nieznany}" in r.text
    r = client.post("/szablony/nowy", data={"name": "Krótki", "kind": "invoice", "subject": "FV {numer} od {firma}",
                                            "body": "Kwota {kwota} zł do {termin}.", "is_default": "1"},
                    follow_redirects=False)
    assert r.status_code == 303
    from app.db import SessionLocal
    from app.models import EmailTemplate
    with SessionLocal() as db:
        t = db.query(EmailTemplate).filter_by(name="Krótki").one()
        assert t.is_default and db.query(EmailTemplate).filter_by(kind="invoice", is_default=True).count() == 1
        tid = t.id
        old = db.query(EmailTemplate).filter_by(kind="invoice", name="Wysyłka faktury").one().id
    # przy wysylce faktury podstawia domyslny, mozna wybrac inny
    inv = _new_invoice(client, "vat")
    _account(client)
    compose = client.get(f"/poczta/nowa?faktura={inv}").text
    assert "FV FV/1/01/2027 od ARKADIUSZ ERDMANN" in compose and "Kwota 123,00 zł do 12.01.2027." in compose
    compose = client.get(f"/poczta/nowa?faktura={inv}&szablon={old}").text
    assert "w załączniku przesyłam faktura vat FV/1/01/2027" in compose
    # usuniecie: domyslny przechodzi na pozostaly
    client.post(f"/szablony/{tid}/usun")
    with SessionLocal() as db:
        assert db.get(EmailTemplate, old).is_default
    assert "nie usunąć" in client.post(f"/szablony/{old}/usun").text
    client.post("/firma/2")
    assert client.get(f"/szablony/{old}").status_code == 404
