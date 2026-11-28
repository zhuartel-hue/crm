"""Osoby kontaktowe: dodawanie przy kontrahencie, wyszukiwanie, poczta rozpoznaje firme po mailu osoby, import z Firmao."""
import json


def _contractor(client, name="Hurt Sp. z o.o.", nip="5260250995"):
    r = client.post("/kontrahenci/nowy", data={"name": name, "nip": nip, "is_company": "1", "street": "Długa 2",
                                              "postal_code": "00-002", "city": "Warszawa", "country": "PL",
                                              "website": "hurt.pl"}, follow_redirects=False)
    assert r.status_code == 303
    from app.db import SessionLocal
    from app.models import Contractor

    with SessionLocal() as db:
        return db.query(Contractor).filter_by(name=name).one().id


def test_contact_crud_and_contractor_card(client):
    from app.db import SessionLocal
    from app.models import Contact, Contractor

    cid = _contractor(client)
    assert "+ Osoba" in client.get(f"/kontrahenci/{cid}").text
    r = client.post("/kontakty/nowy", data={"first_name": "Anna", "last_name": "Nowak", "position": "Zakupy",
                                           "email": "anna@hurt.pl", "phone": "600100200", "contractor_id": str(cid),
                                           "back": "kontrahent"}, follow_redirects=False)
    assert r.headers["location"] == f"/kontrahenci/{cid}"
    card = client.get(f"/kontrahenci/{cid}").text
    assert "Anna Nowak" in card and "tel:600100200" in card
    with SessionLocal() as db:
        assert db.get(Contractor, cid).website == "hurt.pl"
        pid = db.query(Contact).one().id
    assert "Anna Nowak" in client.get("/kontakty?q=hurt").text
    assert "Hurt Sp. z o.o." in client.get("/kontrahenci?q=nowak").text
    assert "Hurt Sp. z o.o." in client.get(f"/faktury/nowa?kontrahent={cid}").text
    assert "Podaj imię" in client.post("/kontakty/nowy", data={"first_name": ""}).text
    client.post(f"/kontakty/{pid}/usun")
    with SessionLocal() as db:
        assert db.query(Contact).count() == 0 and db.get(Contractor, cid) is not None


def test_mail_matches_contractor_by_contact_email(client):
    from app.db import SessionLocal
    from app.mail.service import contractor_for
    from app.models import Contact

    cid = _contractor(client)
    with SessionLocal() as db:
        db.add(Contact(company_id=1, first_name="Jan", email="Jan@Hurt.pl", contractor_id=cid))
        db.commit()
        assert contractor_for(db, ["jan@hurt.pl"], 1).id == cid
        assert contractor_for(db, ["inny@x.pl"], 1) is None


def test_import_contacts_from_firmao(client):
    from app.db import SessionLocal
    from app.models import Contact

    customers = [{"id": 176, "name": "POLIPOL MEBLE", "nipNumber": "7811721460"}]
    contacts = [{"id": 285, "firstName": "Edyta", "lastName": "Owśnicka", "position": "Sekretarka",
                 "emails": ["sekretariat@polipol.pl"], "phones": ["+48 1", "+48 2"], "customer": {"id": 176}},
                {"id": 286, "firstName": "Adrian", "lastName": "Nowak", "email": "adrian@bank.pl"}]
    files = {"customers": ("c.json", json.dumps(customers).encode(), "application/json"),
             "contacts": ("p.json", json.dumps(contacts).encode(), "application/json")}
    r = client.post("/import/firmao", files=files)
    assert "Osoby kontaktowe:</b> 2 nowych" in r.text
    with SessionLocal() as db:
        e = db.query(Contact).filter_by(source_ref="firmao:285").one()
        assert e.contractor.name == "POLIPOL MEBLE" and e.phone == "+48 1" and e.phone2 == "+48 2"
        assert db.query(Contact).filter_by(source_ref="firmao:286").one().contractor_id is None
    r = client.post("/import/firmao", files={"contacts": ("p.json", json.dumps(contacts).encode(), "application/json")})
    assert "0 nowych, 2 zaktualizowanych" in r.text


def test_contractor_notes(client):
    from app.db import SessionLocal
    from app.models import Contractor, ContractorNote

    with SessionLocal() as db:
        c = Contractor(company_id=1, name="Klient Notatek")
        db.add(c)
        db.commit()
        cid = c.id
    r = client.post(f"/kontrahenci/{cid}/notatki", data={"text": "Dzwonił w sprawie alarmu"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get(f"/kontrahenci/{cid}").text
    assert "Dzwonił w sprawie alarmu" in page and "admin" in page
    with SessionLocal() as db:
        nid = db.query(ContractorNote).one().id
    # edycja w miejscu: formularz jest na karcie, zapis zmienia tresc
    assert f'action="/kontrahenci/{cid}/notatki/{nid}"' in page
    r = client.post(f"/kontrahenci/{cid}/notatki/{nid}", data={"text": "Dzwonił w sprawie alarmu, oddzwonić jutro"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"#notatka-{nid}")
    with SessionLocal() as db:
        assert db.get(ContractorNote, nid).text == "Dzwonił w sprawie alarmu, oddzwonić jutro"
    client.post(f"/kontrahenci/{cid}/notatki/{nid}", data={"text": "  "})
    with SessionLocal() as db:
        assert db.get(ContractorNote, nid).text == "Dzwonił w sprawie alarmu, oddzwonić jutro"
    # stala notatka kontrahenta, wpisywana wprost na karcie
    client.post(f"/kontrahenci/{cid}/notatka-stala", data={"notes": "Płaci zawsze przelewem"})
    with SessionLocal() as db:
        assert db.get(Contractor, cid).notes == "Płaci zawsze przelewem"
    assert "Płaci zawsze przelewem" in client.get(f"/kontrahenci/{cid}").text
    client.post(f"/kontrahenci/{cid}/notatki/{nid}/usun")
    assert "Brak notatek." in client.get(f"/kontrahenci/{cid}").text


def test_contractor_mail_history_per_contact(client):
    import datetime as dt

    from app.db import SessionLocal
    from app.models import Contact, MailAccount, MailMessage

    cid = _contractor(client)
    with SessionLocal() as db:
        acc = MailAccount(company_id=1, email="biuro@margot.pl")
        db.add(acc)
        a = Contact(company_id=1, contractor_id=cid, first_name="Anna", email="anna@hurt.pl")
        b = Contact(company_id=1, contractor_id=cid, first_name="Bartek", email="bartek@hurt.pl")
        db.add_all([a, b])
        db.flush()
        now = dt.datetime(2026, 11, 20, 10, 0)
        db.add_all([
            MailMessage(account_id=acc.id, direction="in", subject="Zamówienie od Anny", from_addr="anna@hurt.pl", date=now),
            MailMessage(account_id=acc.id, direction="out", subject="Oferta dla Bartka", to_addrs="Bartek <bartek@hurt.pl>",
                        date=now),
            MailMessage(account_id=acc.id, direction="in", subject="Obcy mail", from_addr="ktos@inny.pl", date=now)])
        db.commit()
        aid = a.id
    page = client.get(f"/kontrahenci/{cid}/maile").text
    assert "Zamówienie od Anny" in page and "Oferta dla Bartka" in page and "Obcy mail" not in page
    page = client.get(f"/kontrahenci/{cid}/maile?osoba={aid}").text
    assert "Zamówienie od Anny" in page and "Oferta dla Bartka" not in page
    card = client.get(f"/kontrahenci/{cid}").text
    assert "Cała historia" in card and "Oferta dla Bartka" in card


def test_contractor_links_and_full_card(client):
    from app.db import SessionLocal
    from app.models import Contractor, Invoice
    from test_app import _new_invoice
    inv_id = _new_invoice(client, "vat", buyer_name="Firma Linkowa", due_date="2020-01-01")
    with SessionLocal() as db:
        cid = db.get(Invoice, inv_id).contractor_id
        other = Contractor(company_id=2, name="Obca Firma", is_company=True, country="PL")
        db.add(other)
        db.commit()
        other_id = other.id
    link = f'class="klink" href="/kontrahenci/{cid}">Firma Linkowa</a>'
    assert link in client.get("/faktury").text
    assert link in client.get("/naleznosci").text
    assert link in client.get(f"/faktury/{inv_id}").text
    card = client.get(f"/kontrahenci/{cid}").text
    for part in ("Do zapłaty", "Po terminie", 'id="faktury"', 'id="wz"', 'id="koszty"', 'id="historia"',
                 'id="maile"', 'id="notatki"', "Dane kontrahenta (edycja)", "Faktury (1)"):
        assert part in card, part
    assert "123,00" in card  # zaleglosc
    # karta innej firmy niedostepna
    assert client.get(f"/kontrahenci/{other_id}").status_code == 404


def test_contractor_files(client):
    from app.db import SessionLocal
    from app.models import Contractor
    with SessionLocal() as db:
        c = Contractor(company_id=1, name="Plikowa", is_company=True, country="PL")
        db.add(c)
        db.commit()
        cid = c.id
    r = client.post(f"/kontrahenci/{cid}/pliki", data={"title": "Skan pisma"},
                    files={"files": ("pismo.pdf", b"%PDF-1.4 test", "application/octet-stream")})
    assert "Dodano plików: 1" in r.text and "Skan pisma" in r.text
    import re
    fid = int(re.search(rf"/kontrahenci/{cid}/pliki/(\d+)", r.text).group(1))
    got = client.get(f"/kontrahenci/{cid}/pliki/{fid}")
    assert got.status_code == 200 and got.content.startswith(b"%PDF") and got.headers["content-type"] == "application/pdf"
    client.post("/firma/2")
    assert client.get(f"/kontrahenci/{cid}/pliki/{fid}").status_code == 404
    client.post("/firma/1")
    assert "Plik usunięty" in client.post(f"/kontrahenci/{cid}/pliki/{fid}/usun").text
