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
    m = _new_invoice(client, "margin", item_purchase=["100,00"])
    from app.db import SessionLocal
    from app.models import Invoice

    with SessionLocal() as db:
        nums = {i.id: i.number for i in db.scalars(select(Invoice))}
    assert nums[a] == "FV/1/01/2027"
    assert nums[b] == "FV/2/01/2027"
    assert nums[m] == "FM/1/01/2027"

    # Druga firma ma wlasna numeracje.
    client.post("/firma/2")
    c = _new_invoice(client, "vat")
    with SessionLocal() as db:
        assert db.get(Invoice, c).number == "FV/1/01/2027"
    # Faktura pierwszej firmy nie jest widoczna z drugiej.
    assert client.get(f"/faktury/{a}").status_code == 404


def test_margin_invoice_with_car_and_pdf(client):
    r = client.post("/auta/nowe", data={
        "make": "Ford", "model": "Transit", "vin": "WF0XXXTTGXKA12345", "year": "2019", "mileage_km": "184000",
        "registration": "wx 12345", "purchase_price": "30 000", "asking_price": "36 150", "status": "available",
    }, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/faktury/nowa?rodzaj=margin&auto=1").text
    assert "VIN: WF0XXXTTGXKA12345" in page and "przebieg: 184 000 km" in page

    inv_id = _new_invoice(client, "margin", car_id="1",
                          item_name=["Samochód Ford Transit 2019, VIN: WF0XXXTTGXKA12345"],
                          item_price=["36 150,00"], item_purchase=["30 000,00"])
    from app.db import SessionLocal
    from app.models import Car, Invoice

    with SessionLocal() as db:
        inv = db.get(Invoice, inv_id)
        assert str(inv.margin_total) == "6150.00"
        assert str(inv.total_vat) == "1150.00"
        assert str(inv.total_gross) == "36150.00"
        assert db.get(Car, 1).status == "sold"

    from app import pdf
    with SessionLocal() as db:
        html = pdf.render_invoice_html(db.get(Invoice, inv_id))
    assert "procedura marży – towary używane" in html
    assert "1 150,00" not in html.replace(" ", " ")  # VAT od marzy nie trafia na fakture

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
                "/faktury/nowa?rodzaj=vat", "/faktury/nowa?rodzaj=margin", "/kontrahenci/nowy"]:
        r = client.get(url)
        assert r.status_code == 200, url


def test_settings_change_pattern(client):
    page = client.get("/ustawienia").text
    data = {f: re.search(rf'name="{f}" value="([^"]*)"', page).group(1) for f in
            ["short_name", "name", "nip", "street", "postal_code", "city", "issue_place", "bank_name",
             "bank_account", "email", "phone", "default_payment_days"]}
    data.update(pattern_vat="{nr}/{RRRR}", pattern_margin="M/{nr}/{RRRR}", numbering_reset="year")
    assert client.post("/ustawienia", data=data, follow_redirects=False).status_code == 303
    inv_id = _new_invoice(client, "vat")
    assert "1/2027" in client.get(f"/faktury/{inv_id}").text
