"""Raporty sprzedazy i kosztow dla ksiegowej oraz weryfikacja kosztow (foldery, zalaczniki, reczne dodanie)."""
import datetime as dt
from decimal import Decimal

from test_app import _new_invoice
from test_kinds import _derived, _edit


def test_sales_report(client):
    fv = _new_invoice(client, "vat", item_name=["Opony", "Książka"], item_qty=["4", "1"], item_unit=["szt.", "szt."],
                      item_price=["400,00", "108,00"], item_rate=["23", "8"], buyer_nip="5552144076")
    _new_invoice(client, "proforma", item_price=["999,00"])  # proforma nie wchodzi
    _new_invoice(client, "vat", action="draft", item_price=["777,00"])  # szkic nie wchodzi
    _new_invoice(client, "vat", issue_date="2027-02-03", sale_date="2027-02-03", item_price=["555,00"])  # inny miesiac
    fk = _derived(client, f"/faktury/{fv}/korekta")
    _edit(client, fk, item_name=["Opony", "Książka"], item_qty=["2", "1"], item_unit=["szt.", "szt."],
          item_price=["400,00", "108,00"], item_rate=["23", "8"], correction_reason="Zwrot")
    from app import reports
    from app.db import SessionLocal

    with SessionLocal() as db:
        rep = reports.sales(db, 1, dt.date(2027, 1, 1), dt.date(2027, 1, 31))
    assert [r["number"] for r in rep["sections"]["invoices"]] == ["FV/1/01/2027"]
    assert [r["number"] for r in rep["sections"]["corrections"]] == ["FK/1/01/2027"]
    t = rep["totals"]
    assert t["invoices"]["gross"] == Decimal("1708.00") and t["corrections"]["gross"] == Decimal("-800.00")
    assert t["total"]["gross"] == Decimal("908.00") and t["total"]["count"] == 2
    rates = {r["rate"]: r for r in rep["rates"]["total"]}
    assert rates["23"]["gross"] == Decimal("800.00") and rates["8"]["gross"] == Decimal("108.00")
    assert rep["label"] == "styczeń 2027"

    page = client.get("/raporty/sprzedaz?od=2027-01-01&do=2027-01-31")
    assert page.status_code == 200
    assert "FV/1/01/2027" in page.text and "FK/1/01/2027" in page.text and "PRO/" not in page.text
    assert "Razem wg stawek VAT" in page.text and "Excel (CSV)" in page.text
    csv = client.get("/raporty/sprzedaz/csv?od=2027-01-01&do=2027-01-31")
    body = csv.content.decode("utf-8-sig")
    assert csv.headers["content-type"].startswith("text/csv") and "FV/1/01/2027;VAT;2027-01-05" in body
    assert ";-800,00;" in body
    druk = client.get("/raporty/sprzedaz/druk?od=2027-01-01&do=2027-01-31").text
    assert "Raport sprzedaży: styczeń 2027" in druk and "window.print()" in druk
    pdf = client.get("/raporty/sprzedaz/pdf?od=2027-01-01&do=2027-01-31")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    # luty i druga firma osobno
    assert "FV/1/01/2027" not in client.get("/raporty/sprzedaz?od=2027-02-01&do=2027-02-28").text
    client.post("/firma/2")
    assert "FV/1/01/2027" not in client.get("/raporty/sprzedaz?od=2027-01-01&do=2027-01-31").text


def test_period_parsing():
    from app import reports

    today = dt.date(2026, 11, 27)
    assert reports.parse_period(None, None, today) == (dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    assert reports.parse_period("2026-12-31", "2026-12-01", today) == (dt.date(2026, 12, 1), dt.date(2026, 12, 31))
    assert reports.parse_period("zle", "", today) == (dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    labels = [p[0] for p in reports.presets(today)]
    assert labels[:2] == ["Ten miesiąc", "Poprzedni miesiąc"]
    assert reports.presets(dt.date(2027, 1, 10))[3][1:] == (dt.date(2026, 10, 1), dt.date(2026, 12, 31))


def _add_cost(client, **extra):
    data = {"invoice_number": "FV 12/2026", "seller_nip": "555-185-99-56", "seller_name": "Hurtownia Test",
            "issue_date": "2026-11-20", "due_date": "2026-12-04", "net": "100,00", "vat": "23,00", "gross": ""}
    data.update(extra)
    files = {"files": ("skan faktury.pdf", b"%PDF-1.4 test", "application/pdf")}
    r = client.post("/koszty/nowa", data=data, files=files, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_cost_verification_folders_and_files(client):
    from app.db import SessionLocal
    from app.models import PurchaseFile, PurchaseInvoice

    client.post("/firma/2")
    a = _add_cost(client)
    b = _add_cost(client, invoice_number="FV 13/2026", issue_date="2026-11-21")
    with SessionLocal() as db:
        p = db.get(PurchaseInvoice, a)
        assert p.status == "new" and p.gross == Decimal("123.00") and p.seller_nip == "5551859956"
        assert p.is_manual and not p.in_ksef and len(p.files) == 1 and p.files[0].content_type == "application/pdf"
        fid = p.files[0].id
    # brak danych -> blad w formularzu
    r = client.post("/koszty/nowa", data={"invoice_number": "", "seller_name": "", "issue_date": "2026-11-20"})
    assert "Podaj numer faktury" in r.text
    # domyslny folder: Do weryfikacji
    page = client.get("/koszty").text
    assert "FV 12/2026" in page and "FV 13/2026" in page and 'class="on' in page
    view = client.get(f"/koszty/{a}").text
    assert f"/koszty/{a}/pliki/{fid}" in view and "Dodana ręcznie" in view and "Zweryfikuj i dalej" in view
    f = client.get(f"/koszty/{a}/pliki/{fid}")
    assert f.content == b"%PDF-1.4 test" and f.headers["content-type"] == "application/pdf"
    assert "sandbox" not in f.headers.get("content-security-policy", "")
    assert client.get(f"/koszty/{a}/podglad").status_code == 404  # bez XML z KSeF
    # zweryfikuj i dalej -> nastepna do weryfikacji
    r = client.post(f"/koszty/{a}", data={"action": "accepted_next"}, follow_redirects=False)
    assert r.headers["location"] == f"/koszty/{b}"
    r = client.post(f"/koszty/{b}", data={"action": "accepted_next"}, follow_redirects=False)
    assert r.headers["location"] == "/koszty?status=accepted"
    with SessionLocal() as db:
        p = db.get(PurchaseInvoice, a)
        assert p.status == "accepted" and p.verified_at and p.verified_by
    assert "FV 12/2026" not in client.get("/koszty").text
    acc = client.get("/koszty?status=accepted").text
    assert "FV 12/2026" in acc and "FV 13/2026" in acc
    assert "FV 12/2026" in client.get("/koszty?status=all").text
    # cofniecie do weryfikacji czysci znacznik
    client.post(f"/koszty/{a}", data={"action": "new"})
    with SessionLocal() as db:
        assert db.get(PurchaseInvoice, a).verified_at is None
    # dodatkowy zalacznik (zdjecie) i usuniecie
    client.post(f"/koszty/{a}/pliki", files={"files": ("foto.jpg", b"\xff\xd8\xffjpeg", "image/jpeg")})
    with SessionLocal() as db:
        jpg = db.query(PurchaseFile).filter_by(purchase_id=a, filename="foto.jpg").one()
    assert client.get(f"/koszty/{a}/pliki/{jpg.id}").headers["content-security-policy"] == "sandbox"
    client.post(f"/koszty/{a}/pliki/{jpg.id}/usun")
    with SessionLocal() as db:
        assert db.query(PurchaseFile).filter_by(purchase_id=a).count() == 1
    # raport kosztow
    rep = client.get("/raporty/koszty?od=2026-11-01&do=2026-11-30").text
    assert "FV 12/2026" in rep and "FV 13/2026" in rep and "246,00" in rep
    assert "FV 12/2026" not in client.get("/raporty/koszty?od=2026-11-01&do=2026-11-30&zakres=accepted").text
    csv = client.get("/raporty/koszty/csv?od=2026-11-01&do=2026-11-30").content.decode("utf-8-sig")
    assert "FV 13/2026;2026-11-21;Hurtownia Test;5551859956;100,00;23,00;123,00;PLN;zweryfikowana" in csv
    assert client.get("/raporty/koszty/pdf?od=2026-11-01&do=2026-11-30").content.startswith(b"%PDF")
    # izolacja: firma 1 nie widzi kosztu ani zalacznika firmy 2
    client.post("/firma/1")
    assert client.get(f"/koszty/{a}").status_code == 404
    assert client.get(f"/koszty/{a}/pliki/{fid}").status_code == 404
    assert "FV 12/2026" not in client.get("/raporty/koszty?od=2026-11-01&do=2026-11-30").text
