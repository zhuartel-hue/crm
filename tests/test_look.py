"""Wyglad faktury (logo, wzor) i ekran Start."""
import io


def _png():
    from PIL import Image

    im = Image.new("RGB", (400, 200), "white")
    for x in range(100, 300):
        for y in range(80, 120):
            im.putpixel((x, y), (10, 140, 220))
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def test_logo_and_template(client):
    from PIL import Image

    from app.db import SessionLocal
    from app.models import Company

    assert client.get("/ustawienia/wzor/nowoczesny").headers["content-type"] == "application/pdf"
    assert client.get("/ustawienia/wzor/zly").status_code == 404
    r = client.post("/ustawienia/wyglad", data={"invoice_template": "nowoczesny"},
                    files={"logo": ("logo.jpg", _png(), "image/jpeg")}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        c = db.query(Company).filter_by(short_name="Margot").one()
        assert c.invoice_template == "nowoczesny" and c.logo_type == "image/png"
        w, h = Image.open(io.BytesIO(c.logo)).size
        assert w < 260 and h < 70  # przyciete z bialego tla
    assert client.get("/ustawienia/logo").headers["content-type"] == "image/png"
    assert "podgląd PDF" in client.get("/ustawienia").text
    r = client.post("/ustawienia/wyglad", files={"logo": ("x.jpg", b"nie obrazek", "image/jpeg")})
    assert "To nie jest obrazek" in r.text
    client.post("/ustawienia/wyglad", data={"logo_remove": "1", "invoice_template": "elegancki"})
    with SessionLocal() as db:
        c = db.query(Company).filter_by(short_name="Margot").one()
        assert c.logo is None and c.invoice_template == "elegancki"


def test_invoice_pdf_uses_template_and_logo():
    from app import pdf
    from app.models import Company, Invoice

    c = Company(name="Firma", short_name="F", nip="1", invoice_template="nowoczesny")
    c.logo, c.logo_type = pdf.prepare_logo(_png())
    import datetime as dt
    inv = Invoice(company=c, kind="vat", status="issued", number="1/2026", issue_date=dt.date.today(),
                  sale_date=dt.date.today(), buyer_name="X", buyer_address="", payment_method="transfer")
    html = pdf.render_invoice_html(inv)
    assert 't-nowoczesny' in html and 'src="data:image/png;base64,' in html
    assert 't-klasyczny' in pdf.render_invoice_html(inv, "klasyczny")


def test_dashboard_tiles(client):
    page = client.get("/").text
    for t in ("Ostatnie faktury", "Ostatnie maile", "Ostatnie WZ", "Po terminie"):
        assert t in page
