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
