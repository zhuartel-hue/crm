import re

from sqlalchemy import select


def _new_invoice(client, kind, **extra):
    data = {
        "kind": kind, "issue_date": "2027-01-05", "sale_date": "2027-01-05", "issue_place": "Warszawa",
        "payment_method": "transfer", "due_date": "2027-01-12", "paid": "",
        "buyer_name": "Jan Kowalski", "buyer_nip": "", "buyer_address": "ul. Prosta 1\n00-001 Warszawa",
        "save_contractor": "1", "contractor_id": "", "car_id": "", "notes": "",
        "item_name": ["Usługa"], "item_qty": ["1"], "item_unit": ["szt."], "item_price": ["123,00"],
        "item_rate": ["23"], "item_purchase": [""], "prices_gross": "1", "action": "issue",
    }
    data.update(extra)
    r = client.post("/faktury/nowa", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_login_required():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anon:
        r = anon.get("/faktury", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"


def test_numbering_per_company_and_kind(client):
    a = _new_invoice(client, "vat")
    b = _new_invoice(client, "vat")
    from app.db import SessionLocal
    from app.models import Invoice

    with SessionLocal() as db:
        nums = {i.id: i.number for i in db.scalars(select(Invoice))}
    assert nums[a] == "FV/1/01/2027"
    assert nums[b] == "FV/2/01/2027"

    # Druga firma ma wlasna numeracje.
    client.post("/firma/2")
    c = _new_invoice(client, "vat")
    with SessionLocal() as db:
        assert db.get(Invoice, c).number == "FV/1/01/2027"
    # Link do faktury pierwszej firmy przelacza na nia.
    assert "FV/1/01/2027" in client.get(f"/faktury/{a}").text
    assert 'class="on">Margot' in client.get("/").text


def test_car_sale_invoice_and_pdf(client):
    r = client.post("/auta/nowe", data={
        "make": "Ford", "model": "Transit", "vin": "WF0XXXTTGXKA12345", "year": "2019", "mileage_km": "184000",
        "registration": "wx 12345", "purchase_price": "30 000", "asking_price": "36 900", "status": "available",
    }, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/faktury/nowa?auto=1").text
    assert "VIN: WF0XXXTTGXKA12345" in page and "przebieg: 184 000 km" in page
    assert "marż" not in page.lower()

    inv_id = _new_invoice(client, "vat", car_id="1",
                          item_name=["Samochód Ford Transit 2019, VIN: WF0XXXTTGXKA12345"],
                          item_price=["36 900,00"])
    from app.db import SessionLocal
    from app.models import Car, Invoice

    with SessionLocal() as db:
        inv = db.get(Invoice, inv_id)
        assert str(inv.total_net) == "30000.00"
        assert str(inv.total_vat) == "6900.00"
        assert db.get(Car, 1).status == "sold"

    from app import pdf
    with SessionLocal() as db:
        html = pdf.render_invoice_html(db.get(Invoice, inv_id))
    assert "procedura marży" not in html and "Faktura VAT" in html
    r = client.get(f"/faktury/{inv_id}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_issued_invoice_is_locked(client):
    inv_id = _new_invoice(client, "vat")
    r = client.get(f"/faktury/{inv_id}/edytuj", follow_redirects=False)
    assert r.status_code == 303
    r = client.post(f"/faktury/{inv_id}/usun", follow_redirects=False)
    assert r.status_code == 303
    assert client.get(f"/faktury/{inv_id}").status_code == 200


def test_draft_then_issue(client):
    inv_id = _new_invoice(client, "vat", action="draft")
    assert "Szkic" in client.get(f"/faktury/{inv_id}").text
    client.post(f"/faktury/{inv_id}/wystaw")
    assert "FV/1/01/2027" in client.get(f"/faktury/{inv_id}").text


def test_pages_render(client):
    _new_invoice(client, "vat")
    for url in ["/", "/faktury", "/kontrahenci", "/kontrahenci/1", "/auta", "/auta/nowe", "/ustawienia",
                "/faktury/nowa", "/kontrahenci/nowy"]:
        r = client.get(url)
        assert r.status_code == 200, url


def test_settings_change_pattern(client):
    page = client.get("/ustawienia").text
    data = {f: re.search(rf'name="{f}" value="([^"]*)"', page).group(1) for f in
            ["short_name", "name", "nip", "street", "postal_code", "city", "issue_place", "bank_name",
             "bank_account", "email", "phone", "default_payment_days"]}
    data.update(pattern_vat="{nr}/{RRRR}", numbering_reset="year")
    assert client.post("/ustawienia", data=data, follow_redirects=False).status_code == 303
    inv_id = _new_invoice(client, "vat")
    assert "1/2027" in client.get(f"/faktury/{inv_id}").text


def test_delete_car_and_contractor(client):
    from app.db import SessionLocal
    from app.models import Car, Contractor, Invoice

    client.post("/auta/nowe", data={"make": "Opel", "model": "Astra", "status": "available"})
    client.post("/auta/nowe", data={"make": "Skoda", "model": "Octavia", "status": "available"})
    with SessionLocal() as db:
        astra = db.scalars(select(Car).where(Car.model == "Astra")).one().id
        octavia = db.scalars(select(Car).where(Car.model == "Octavia")).one().id

    # szkic z autem i nowym kontrahentem: usuniecie odpina je od szkicu
    draft = _new_invoice(client, "vat", car_id=str(astra), action="draft", buyer_name="Szkic Testowy")
    with SessionLocal() as db:
        cid = db.get(Invoice, draft).contractor_id
    assert cid
    assert "Usuń auto" in client.get(f"/auta/{astra}").text
    client.post(f"/auta/{astra}/usun")
    client.post(f"/kontrahenci/{cid}/usun")
    with SessionLocal() as db:
        assert db.get(Car, astra) is None and db.get(Contractor, cid) is None
        inv = db.get(Invoice, draft)
        assert inv.car_id is None and inv.contractor_id is None and inv.buyer_name == "Szkic Testowy"

    # wystawiona faktura blokuje usuniecie
    issued = _new_invoice(client, "vat", car_id=str(octavia), buyer_name="Kupiec")
    with SessionLocal() as db:
        cid2 = db.get(Invoice, issued).contractor_id
    assert "Usuń auto" not in client.get(f"/auta/{octavia}").text
    client.post(f"/auta/{octavia}/usun")
    client.post(f"/kontrahenci/{cid2}/usun")
    with SessionLocal() as db:
        assert db.get(Car, octavia) is not None and db.get(Contractor, cid2) is not None
    assert "Dziennik" in client.get("/dziennik").text and "Usunięto auto" in client.get("/dziennik").text
