"""WZ: wydanie towaru, numeracja, podpis, PDF; aplikacja na telefon (manifest)."""
import base64
import io

from PIL import Image, ImageDraw

from test_app import _new_invoice


def _signature():
    img = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    ImageDraw.Draw(img).line([(20, 150), (300, 40), (580, 160)], fill=(0, 0, 0, 255), width=4)
    out = io.BytesIO()
    img.save(out, "PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def test_wz_from_invoice_sign_pdf(client):
    inv_id = _new_invoice(client, "vat", item_name=["Wtyk RJ45", "Kabel"], item_qty=["10", "2,5"],
                          item_unit=["szt.", "m"], item_price=["1,23", "5,00"], item_rate=["23", "23"])
    assert "Wystaw WZ" in client.get(f"/faktury/{inv_id}").text
    form = client.get(f"/wz/nowa?faktura={inv_id}").text
    assert "Wtyk RJ45" in form and "Jan Kowalski" in form
    data = {"invoice_id": str(inv_id), "issue_date": "2027-01-20", "contractor_id": "", "recipient_name": "Jan Kowalski",
            "recipient_address": "ul. Prosta 1\n00-001 Warszawa", "item_name": ["Wtyk RJ45", "Kabel"],
            "item_qty": ["10", "2,5"], "item_unit": ["szt.", "m"], "item_product": ["", ""], "notes": "", "action": "issue"}
    r = client.post("/wz/nowa", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    wid = int(r.headers["location"].rsplit("/", 1)[1])
    page = client.get(f"/wz/{wid}").text
    assert "WZ/1/01/2027" in page and "Podpis odbiorcy" in page
    r = client.post(f"/wz/{wid}/podpis", data={"signature": _signature(), "signed_by": "Jan Kowalski"})
    assert "Zapisano podpis" in r.text and "podpisana" in r.text
    pdf = client.get(f"/wz/{wid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert "WZ/1/01/2027" in client.get(f"/faktury/{inv_id}").text
    assert "WZ/1/01/2027" in client.get("/wz").text
    # pusty podpis odrzucony
    empty = Image.new("RGBA", (10, 10), (0, 0, 0, 0)); out = io.BytesIO(); empty.save(out, "PNG")
    r = client.post(f"/wz/{wid}/podpis", data={"signature": "data:image/png;base64," + base64.b64encode(out.getvalue()).decode(),
                                              "signed_by": "X"})
    assert "Podpis jest pusty" in r.text
    # wystawionej nie mozna usunac ani edytowac
    assert "nie można usunąć" in client.post(f"/wz/{wid}/usun").text
    assert client.get(f"/wz/{wid}/edytuj").status_code == 409


def test_wz_draft_validation_and_company_scope(client):
    data = {"issue_date": "2027-01-20", "recipient_name": "", "recipient_address": "", "contractor_id": "",
            "item_name": ["Towar"], "item_qty": ["1"], "item_unit": ["szt."], "item_product": [""], "notes": "",
            "action": "issue"}
    r = client.post("/wz/nowa", data=data)
    assert "Podaj odbiorcę" in r.text
    data.update(recipient_name="Firma X", action="save")
    r = client.post("/wz/nowa", data=data, follow_redirects=False)
    wid = int(r.headers["location"].rsplit("/", 1)[1])
    assert "szkic" in client.get(f"/wz/{wid}").text
    client.post("/firma/2")
    assert client.get(f"/wz/{wid}").status_code == 404
    client.post("/firma/1")
    assert "Usunięto szkic" in client.post(f"/wz/{wid}/usun").text


def test_pwa_manifest_and_sw(client):
    page = client.get("/").text
    assert 'rel="manifest"' in page and "apple-touch-icon" in page
    m = client.get("/static/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["display"] == "standalone"
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert client.get("/static/icons/icon-192.png").status_code == 200


def _new_product(client, **kw):
    data = {"code": "C6-1", "name": "Kabel C6", "unit": "m", "vat_rate": "23", "price_net": "2,00", "price_gross": "",
            "price_mode": "net", "ean": "", "description": "", "active": "1"}
    data.update(kw)
    r = client.post("/produkty/nowy", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def _doc(client, kind, pid, qty, extra_names=(), extra_qty=(), extra_units=(), extra_prices=(), price="2,00"):
    data = {"kind": kind, "issue_date": "2027-02-03", "contractor_id": "", "recipient_name": "Hurtownia Y",
            "recipient_address": "ul. Długa 2\n89-600 Chojnice", "item_name": ["Kabel C6", *extra_names],
            "item_qty": [qty, *extra_qty], "item_unit": ["m", *extra_units], "item_product": [str(pid), *[""] * len(extra_names)],
            "item_price": [price, *extra_prices], "item_rate": ["23", *["23"] * len(extra_names)],
            "notes": "", "action": "issue"}
    r = client.post("/wz/nowa", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_stock_pz_wz_prices_and_invoice_from_wz(client):
    pid = _new_product(client, stock="5", stock_note="bilans otwarcia")
    page = client.get(f"/produkty/{pid}").text
    assert "bilans otwarcia" in page
    # PZ zwieksza stan
    pz = _doc(client, "pz", pid, "100")
    assert "PZ/1/02/2027" in client.get(f"/wz/{pz}").text
    assert "Podpis odbiorcy" not in client.get(f"/wz/{pz}").text
    # WZ z cenami, dojazdem i roboczogodzinami zmniejsza stan
    wid = _doc(client, "wz", pid, "30", extra_names=["Dojazd", "Roboczogodziny"], extra_qty=["40", "3"],
               extra_units=["km", "rbh"], extra_prices=["1,50", "100"])
    view = client.get(f"/wz/{wid}").text
    assert "WZ/1/02/2027" in view and "Wystaw fakturę z WZ" in view
    # 30*2 + 40*1,5 + 3*100 = 420 netto -> 516,60 brutto
    assert "516,60" in view
    products = client.get("/produkty").text
    assert "75" in products  # 5 + 100 - 30
    assert "Hurtownia Y" in products and 'class="thumb"' not in products  # dostawca z PZ, bez zdjec
    page = client.get(f"/produkty/{pid}").text
    assert "PZ/1/02/2027" in page and "WZ/1/02/2027" in page and "-30" in page
    wz_list = client.get("/wz").text
    assert "516,60" in wz_list and "420,00" in wz_list and "96,60" in wz_list
    pdf = client.get(f"/wz/{wid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    # faktura z WZ
    r = client.post(f"/wz/{wid}/faktura", follow_redirects=False)
    assert r.status_code == 303 and "/faktury/" in r.headers["location"], r.text
    inv_page = client.get(r.headers["location"]).text
    assert "Roboczogodziny" in inv_page and "Hurtownia Y" in inv_page
    view = client.get(f"/wz/{wid}").text
    assert "Wystaw fakturę z WZ" not in view
    assert "ma już fakturę" in client.post(f"/wz/{wid}/faktura").text
    # PZ nie daje faktury
    assert "z WZ" in client.post(f"/wz/{pz}/faktura").text


def test_service_has_no_stock_and_nnn_pattern(client):
    from app.db import SessionLocal
    from app.models import Company, Product
    pid = _new_product(client, code="DOJ", name="Dojazd", unit="km", is_service="1", stock="9")
    with SessionLocal() as db:
        p = db.get(Product, pid)
        assert p.is_service and p.stock == 0
        db.get(Company, 1).pattern_wz = "{RRRR}/{MM}/{NNN}/WZ"
        db.commit()
    wid = _doc(client, "wz", pid, "12")
    assert "2027/02/001/WZ" in client.get(f"/wz/{wid}").text
    with SessionLocal() as db:
        assert db.get(Product, pid).stock == 0


def test_invoice_from_several_wz_and_payment_column(client):
    from app.db import SessionLocal
    from app.models import Delivery, Invoice
    pid = _new_product(client, stock="100")
    w1 = _doc(client, "wz", pid, "10")
    w2 = _doc(client, "wz", pid, "5", extra_names=["Dojazd"], extra_qty=["20"], extra_units=["km"], extra_prices=["1,50"])
    lst = client.get("/wz").text
    assert "Faktura z zaznaczonych WZ" in lst and "bez faktury" in lst
    assert f'name="ids" value="{w1}"' in lst
    # inny odbiorca nie przejdzie razem
    data = {"kind": "wz", "issue_date": "2027-02-04", "contractor_id": "", "recipient_name": "Inna Firma",
            "recipient_address": "", "item_name": ["Kabel C6"], "item_qty": ["1"], "item_unit": ["m"],
            "item_product": [str(pid)], "item_price": ["2,00"], "item_rate": ["23"], "notes": "", "action": "issue"}
    w3 = int(client.post("/wz/nowa", data=data, follow_redirects=False).headers["location"].rsplit("/", 1)[1])
    r = client.post("/wz/faktura", data={"ids": [str(w1), str(w3)]})
    assert "różnych odbiorców" in r.text
    with SessionLocal() as db:
        assert db.get(Delivery, w1).invoice_id is None
    # dwie WZ tego samego odbiorcy -> jeden szkic
    r = client.post("/wz/faktura", data={"ids": [str(w1), str(w2)], "back": "/wz?rodzaj=bez-faktury"},
                    follow_redirects=False)
    assert r.status_code == 303 and "/faktury/" in r.headers["location"], r.text
    with SessionLocal() as db:
        d1, d2 = db.get(Delivery, w1), db.get(Delivery, w2)
        assert d1.invoice_id and d1.invoice_id == d2.invoice_id
        inv = db.get(Invoice, d1.invoice_id)
        assert inv.status == "draft" and inv.buyer_name == "Hurtownia Y"
        assert [it.name for it in inv.items] == ["Kabel C6", "Kabel C6", "Dojazd"]
        assert all("C6-1" not in it.name for it in inv.items)  # bez kodu produktu na fakturze
        assert d1.number in inv.notes and d2.number in inv.notes
        # (10+5)*2 + 20*1,5 = 60 netto
        assert inv.total_net == 60
        inv_id = inv.id
    assert "ma już fakturę" in client.post("/wz/faktura", data={"ids": [str(w1)]}).text
    lst = client.get("/wz").text
    assert "szkic faktury" in lst and f'name="ids" value="{w1}"' not in lst
    assert f'name="ids" value="{w3}"' in client.get("/wz?rodzaj=bez-faktury").text
    assert f'value="{w1}"' not in client.get("/wz?rodzaj=bez-faktury").text
    # po wystawieniu i zaplacie: zapłacono
    with SessionLocal() as db:
        inv = db.get(Invoice, inv_id)
        inv.status, inv.number = "issued", "FV/TEST/1"
        db.commit()
    assert "nie zapłacono" in client.get("/wz").text
    with SessionLocal() as db:
        inv = db.get(Invoice, inv_id)
        inv.paid = inv.total_gross
        db.commit()
    assert ">zapłacono<" in client.get("/wz").text
    # inna firma nie wystawi faktury z cudzych WZ
    client.post("/firma/2")
    assert "Nie znaleziono" in client.post("/wz/faktura", data={"ids": [str(w3)]}).text
    client.post("/firma/1")
