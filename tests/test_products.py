import io

from PIL import Image

from tests.test_mcp import call, connect


def _png(w=2400, h=1200, color=(200, 30, 30, 128)):
    out = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(out, "PNG")
    return out.getvalue()


def _new(client, **data):
    base = {"code": "C17-6000", "name": "Wtyk RJ45 kat. 6", "unit": "szt.", "vat_rate": "23",
            "price_net": "10,00", "price_gross": "", "price_mode": "net", "ean": "", "description": "Opis",
            "active": "1"}
    base.update(data)
    files = [("photos", ("a.png", _png(), "image/png")), ("photos", ("zly.txt", b"nie obraz", "text/plain"))]
    return client.post("/produkty/nowy", data=base, files=files, follow_redirects=False)


def test_product_crud_photos_and_invoice_link(client):
    r = _new(client)
    assert r.status_code == 303
    pid = int(r.headers["location"].rsplit("/", 1)[1])
    page = client.get(f"/produkty/{pid}").text
    assert "C17-6000" in page and "główne" in page
    flash_page = client.get("/produkty").text
    assert "Wtyk RJ45" in flash_page and "12,30" in flash_page

    from app.db import SessionLocal
    from app.models import Product

    with SessionLocal() as db:
        p = db.get(Product, pid)
        assert len(p.photos) == 1  # plik tekstowy odrzucony
        ph = p.photos[0]
        assert (ph.width, ph.height) == (1600, 800)
        photo_id = ph.id
    img = client.get(f"/zdjecia/{photo_id}?mini=1")
    assert img.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(img.content)).size == (400, 200)

    # cena brutto przeliczana na netto
    r = client.post(f"/produkty/{pid}", data={"code": "C17-6000", "name": "Wtyk RJ45 kat. 6", "vat_rate": "23",
                                              "price_net": "", "price_gross": "24,60", "price_mode": "gross",
                                              "active": "1"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert str(db.get(Product, pid).price_net) == "20.00"

    # formularz faktury podpowiada produkt; zapis pozycji z product_id
    form = client.get("/faktury/nowa").text
    assert 'list="product-list"' in form and "C17-6000 · Wtyk RJ45 kat. 6" in form
    r = client.post("/faktury/nowa", data={
        "kind": "vat", "issue_date": "2026-11-25", "sale_date": "2026-11-25", "payment_method": "transfer",
        "prices_gross": "1", "buyer_name": "Firma X", "item_name": "Wtyk RJ45 kat. 6", "item_qty": "2",
        "item_unit": "szt.", "item_price": "24,60", "item_rate": "23", "item_product": str(pid), "action": "draft",
    }, follow_redirects=False)
    assert r.status_code == 303
    inv_id = int(r.headers["location"].split("/")[2])
    from app.models import Invoice
    with SessionLocal() as db:
        assert db.get(Invoice, inv_id).items[0].product_id == pid

    # usuniecie zdjecia i produktu; pozycja faktury zostaje bez powiazania
    client.post(f"/produkty/{pid}/zdjecie/{photo_id}/usun")
    assert client.get(f"/zdjecia/{photo_id}").status_code == 404
    client.post(f"/produkty/{pid}/usun")
    with SessionLocal() as db:
        assert db.get(Product, pid) is None
        it = db.get(Invoice, inv_id).items[0]
        assert it.product_id is None and it.name == "Wtyk RJ45 kat. 6"


def test_product_via_mcp(client):
    r = _new(client, code="K-1", name="Keystone kat. 6A", price_net="15,00")
    pid = int(r.headers["location"].rsplit("/", 1)[1])
    _, tok = connect(client, scopes=("read", "write"))
    at = tok["access_token"]
    found = call(client, at, "search_products", company="Margot", query="keystone")["structuredContent"]["products"]
    assert found[0]["id"] == pid and found[0]["price_gross"] == "18.45" and found[0]["photos"]
    draft = call(client, at, "create_invoice_draft", company="Margot", buyer={"name": "Y"},
                 items=[{"product_id": pid, "quantity": 4}])
    assert not draft["isError"], draft
    inv = draft["structuredContent"]["invoice"]
    assert inv["total_gross"] == "73.80" and inv["items"][0]["name"] == "Keystone kat. 6A"


def test_change_password(client):
    assert "Zmiana hasła" in client.get("/konto").text
    bad = client.post("/konto/haslo", data={"old": "zle", "new": "nowehaslo123", "repeat": "nowehaslo123"})
    assert "niepoprawne" in bad.text
    short = client.post("/konto/haslo", data={"old": "test-haslo", "new": "krotkie", "repeat": "krotkie"})
    assert "10 znaków" in short.text
    ok = client.post("/konto/haslo", data={"old": "test-haslo", "new": "nowehaslo123", "repeat": "nowehaslo123"},
                     follow_redirects=False)
    assert ok.status_code == 303
    client.post("/logout")
    assert client.post("/login", data={"login": "admin", "password": "test-haslo"}).status_code == 401
    r = client.post("/login", data={"login": "admin", "password": "nowehaslo123"}, follow_redirects=False)
    assert r.status_code == 303
    # przywrocenie hasla dla kolejnych testow
    client.post("/konto/haslo", data={"old": "nowehaslo123", "new": "test-haslo-1", "repeat": "test-haslo-1"})
