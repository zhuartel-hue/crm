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
    client.post(f"/kontrahenci/{cid}/notatki/{nid}/usun")
    assert "Brak notatek." in client.get(f"/kontrahenci/{cid}").text
