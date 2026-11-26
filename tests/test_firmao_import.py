"""Import z Firmao: klienci i produkty z JSON, bez dubli przy powtorce."""
import json

CUSTOMERS = {"date": [
    {"id": 786, "name": "Krystyna Dałek Złota Rybka", "naturalPerson": False, "nipNumber": "958-123-13-25",
     "officeAddress": {"city": "Karwia", "country": "Polska", "postCode": "84-105", "street": "Wojska Polskiego 19"},
     "emails": [{"email": "rybka@test.pl"}], "phone": "+48 600 100 200", "description": "Stały klient"},
    {"id": 785, "name": "MegaMarket S.A.", "nipNumber": "8672239415",
     "officeAddress": {"country": "Polska", "street": "STREFOWA 15, 39-442 TARNOBRZEG, podkarpackie"}},
    {"id": 700, "name": "Jan Prywatny", "naturalPerson": True, "email": "jan@test.pl"},
    {"id": 701, "name": "", "deleted": False},
], "totalSize": 4}

PRODUCTS = [
    {"id": 551, "name": "Usługa zdalna | System antywłamaniowy", "productCode": "uz alarm", "product": True,
     "salePriceNet": 88.0, "salePriceVat": 23.0, "salePriceUnit": "szt.", "totalStoreState": -1.0, "saleable": True},
    {"id": 552, "name": "RH Dariusz", "productCode": "RH Dariusz", "product": False,
     "salePriceGroupA": {"netPrice": 1.0, "vat": 23.0, "unit": "rbh"}},
    {"id": 553, "name": "Grupa", "isGroup": True},
]


def _post(client, customers=None, products=None):
    files = {}
    if customers is not None:
        files["customers"] = ("customers.json", json.dumps(customers).encode(), "application/json")
    if products is not None:
        files["products"] = ("products.json", json.dumps(products).encode(), "application/json")
    return client.post("/import/firmao", files=files)


def test_import_and_reimport(client):
    from app.db import SessionLocal
    from app.models import Contractor, Product

    assert client.get("/import/firmao").status_code == 200
    r = _post(client, CUSTOMERS, PRODUCTS)
    assert "Klienci:</b> 3 nowych" in r.text and "Produkty:</b> 2 nowych" in r.text
    with SessionLocal() as db:
        c = db.query(Contractor).filter_by(source_ref="firmao:786").one()
        assert c.nip == "9581231325" and c.city == "Karwia" and c.postal_code == "84-105"
        assert c.email == "rybka@test.pl" and c.phone.startswith("+48") and c.notes == "Stały klient"
        m = db.query(Contractor).filter_by(source_ref="firmao:785").one()
        assert m.street == "STREFOWA 15" and m.postal_code == "39-442" and m.city.startswith("TARNOBRZEG")
        assert db.query(Contractor).filter_by(source_ref="firmao:700").one().is_company is False
        p = db.query(Product).filter_by(code="uz alarm").one()
        assert str(p.price_net) == "88.00" and p.vat_rate == "23" and p.is_service is False and p.stock == -1
        s = db.query(Product).filter_by(code="RH Dariusz").one()
        assert s.is_service is True and s.unit == "rbh" and str(s.price_net) == "1.00"

    r = _post(client, CUSTOMERS, PRODUCTS)
    assert "0 nowych, 3 zaktualizowanych" in r.text
    with SessionLocal() as db:
        assert db.query(Contractor).count() == 3 and db.query(Product).count() == 2


def test_matches_existing_by_nip_and_bad_file(client):
    from app.db import SessionLocal
    from app.models import Contractor

    with SessionLocal() as db:
        db.add(Contractor(company_id=1, name="Euro-Box", nip="9271003914"))
        db.commit()
    _post(client, [{"id": 784, "name": "Euro-Box Sp. z o.o.", "nipNumber": "9271003914"}])
    with SessionLocal() as db:
        assert db.query(Contractor).count() == 1
        assert db.query(Contractor).one().source_ref == "firmao:784"
    r = client.post("/import/firmao", files={"customers": ("c.json", b"nie json", "application/json")})
    assert "poprawny plik JSON" in r.text
    assert "Wybierz co najmniej jeden plik" in client.post("/import/firmao", data={"x": "1"}).text
