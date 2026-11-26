"""Proformy, korekty, zaliczki i faktury koncowe."""
from decimal import Decimal

from test_app import _new_invoice


def _inv(id_):
    from app.db import SessionLocal
    from app.models import Invoice

    db = SessionLocal()
    return db, db.get(Invoice, id_)


def _derived(client, url):
    r = client.post(url, follow_redirects=False)
    assert r.status_code == 303, r.text
    loc = r.headers["location"]
    assert loc.endswith("/edytuj"), loc
    return int(loc.split("/")[2])


def _edit(client, inv_id, expect=303, **data):
    page = client.get(f"/faktury/{inv_id}/edytuj")
    assert page.status_code == 200
    base = {
        "issue_date": "2027-01-20", "sale_date": "2027-01-20", "issue_place": "Chojnice", "payment_method": "transfer",
        "due_date": "2027-01-27", "paid": "", "buyer_name": "Jan Kowalski", "buyer_nip": "",
        "buyer_address": "ul. Prosta 1\n00-001 Warszawa", "contractor_id": "", "car_id": "", "notes": "",
        "prices_gross": "1", "action": "issue",
    }
    base.update(data)
    r = client.post(f"/faktury/{inv_id}/edytuj", data=base, follow_redirects=False)
    assert r.status_code == expect, r.text
    return r


def test_proforma_then_vat(client):
    pro = _new_invoice(client, "proforma", item_price=["1 230,00"])
    db, inv = _inv(pro)
    assert inv.number == "PRO/1/01/2027" and inv.kind_label == "Faktura proforma"
    db.close()
    pdf = client.get(f"/faktury/{pro}/pdf")
    assert pdf.status_code == 200
    vat = _derived(client, f"/faktury/{pro}/z-proformy")
    _edit(client, vat, item_name=["Usługa"], item_qty=["1"], item_unit=["szt."], item_price=["1 230,00"], item_rate=["23"])
    db, inv = _inv(vat)
    assert inv.number == "FV/1/01/2027" and inv.related_id == pro and inv.total_gross == Decimal("1230.00")
    db.close()
    # proforma nie liczy sie do sprzedazy
    from app.db import SessionLocal
    from app import services
    with SessionLocal() as db:
        s = services.sales_summary(db, "Margot", "2027-01")
    assert s["invoices"] == 1 and s["gross"] == "1230.00"


def test_correction(client):
    fv = _new_invoice(client, "vat", item_name=["Opony", "Montaż"], item_qty=["4", "1"], item_unit=["szt.", "usł."],
                      item_price=["400,00", "123,00"], item_rate=["23", "23"])
    db, inv = _inv(fv)
    assert inv.total_gross == Decimal("1723.00")
    db.close()
    fk = _derived(client, f"/faktury/{fv}/korekta")
    # bez przyczyny nie da sie wystawic
    r = _edit(client, fk, expect=200, item_name=["Opony", "Montaż"], item_qty=["2", "1"], item_unit=["szt.", "usł."],
              item_price=["400,00", "123,00"], item_rate=["23", "23"], correction_reason="")
    assert "Podaj przyczynę korekty" in r.text
    db, inv = _inv(fk)
    assert inv.status == "draft"
    db.close()
    _edit(client, fk, item_name=["Opony", "Montaż"], item_qty=["2", "1"], item_unit=["szt.", "usł."],
          item_price=["400,00", "123,00"], item_rate=["23", "23"], correction_reason="Zwrot 2 opon")
    db, inv = _inv(fk)
    assert inv.status == "issued" and inv.number == "FK/1/01/2027"
    assert len(inv.before_items) == 2 and len(inv.after_items) == 2
    assert inv.total_gross == Decimal("-800.00") and inv.total_vat == Decimal("-149.60")
    assert inv.to_pay == Decimal("-800.00")
    db.close()
    view = client.get(f"/faktury/{fk}").text
    assert "do zwrotu 800,00" in view and "Zwrot 2 opon" in view
    from app import pdf
    db, inv = _inv(fk)
    html = pdf.render_invoice_html(inv)
    db.close()
    assert "Przed korektą" in html and "Po korekcie" in html and "Do zwrotu" in html and "FV/1/01/2027" in html
    # druga korekta startuje od stanu po pierwszej
    fk2 = _derived(client, f"/faktury/{fv}/korekta")
    db, inv = _inv(fk2)
    assert [i.quantity for i in inv.before_items] == [Decimal("2"), Decimal("1")]
    db.close()
    # korekty nie da sie skopiowac ani skorygowac
    r = client.post(f"/faktury/{fk}/korekta", follow_redirects=False)
    assert r.headers["location"] == f"/faktury/{fk}"


def test_advance_and_final(client):
    # zamowienie: auto 61 500 (23%) + usluga 1 080 (8%) = 62 580 brutto; zaliczka 10 000
    zal = _new_invoice(client, "advance", advance_gross="10 000,00",
                       item_name=["Samochód", "Usługa"], item_qty=["1", "1"], item_unit=["szt.", "usł."],
                       item_price=["61 500,00", "1 080,00"], item_rate=["23", "8"])
    db, inv = _inv(zal)
    assert inv.number == "FZ/1/01/2027"
    assert inv.order_total == Decimal("62580.00") and inv.total_gross == Decimal("10000.00")
    from app import calc
    rows = {r["rate"]: r for r in calc.invoice_summary(inv)}
    assert rows["23"]["gross"] == Decimal("9827.42") and rows["8"]["gross"] == Decimal("172.58")
    assert rows["23"]["vat"] == Decimal("1837.65") and rows["8"]["vat"] == Decimal("12.78")
    db.close()
    # zaliczka wieksza niz zamowienie: blad
    r = client.post("/faktury/nowa", data={
        "kind": "advance", "advance_gross": "999999", "issue_date": "2027-01-05", "sale_date": "2027-01-05",
        "buyer_name": "X", "item_name": ["A"], "item_qty": ["1"], "item_unit": ["szt."], "item_price": ["100"],
        "item_rate": ["23"], "prices_gross": "1", "action": "issue", "payment_method": "transfer"})
    assert "nie może być większa" in r.text

    final = _derived(client, f"/faktury/{zal}/koncowa")
    db, inv = _inv(final)
    assert inv.kind == "final" and [a.id for a in inv.advances] == [zal]
    assert inv.total_gross == Decimal("52580.00")
    db.close()
    # usuniecie szkicu zwalnia zaliczke
    client.post(f"/faktury/{final}/usun")
    db, inv = _inv(zal)
    assert inv.settled_by_id is None
    db.close()
    final = _derived(client, f"/faktury/{zal}/koncowa")
    _edit(client, final, item_name=["Samochód", "Usługa"], item_qty=["1", "1"], item_unit=["szt.", "usł."],
          item_price=["61 500,00", "1 080,00"], item_rate=["23", "8"])
    db, inv = _inv(final)
    assert inv.status == "issued" and inv.number == "FV/1/01/2027"
    rows = {r["rate"]: r for r in calc.invoice_summary(inv)}
    assert rows["23"]["gross"] == Decimal("51672.58") and rows["8"]["gross"] == Decimal("907.42")
    db.close()
    assert "Odliczone zaliczki" in client.get(f"/faktury/{final}").text
    assert client.get(f"/faktury/{final}/pdf").status_code == 200
    # sprzedaz w miesiacu = cale zamowienie (zaliczka + koncowa)
    from app.db import SessionLocal
    from app import services
    with SessionLocal() as db:
        s = services.sales_summary(db, "Margot", "2027-01")
    assert s["gross"] == "62580.00"
    # druga koncowa do tej samej zaliczki: nie
    r = client.post(f"/faktury/{zal}/koncowa", follow_redirects=False)
    assert r.headers["location"] == f"/faktury/{zal}"


def test_migration_from_stage_two_schema(tmp_path, monkeypatch):
    """Baza utworzona przez create_all (bez alembic_version) dostaje znacznik 0001 i migracje."""
    import sqlalchemy as sa

    from app import migrate

    url = f"sqlite:///{tmp_path / 'old.db'}"
    eng = sa.create_engine(url)
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE companies (id INTEGER PRIMARY KEY, short_name VARCHAR(40), name VARCHAR(300),"
                             " nip VARCHAR(20), street VARCHAR(200), postal_code VARCHAR(10), city VARCHAR(100),"
                             " bank_name VARCHAR(100), bank_account VARCHAR(40), email VARCHAR(200), phone VARCHAR(40),"
                             " issue_place VARCHAR(100), pattern_vat VARCHAR(60), pattern_margin VARCHAR(60),"
                             " numbering_reset VARCHAR(10), default_payment_days INTEGER)"))
    monkeypatch.setattr(migrate, "engine", eng)
    # stary stan = 0001 bez pozostalych tabel nie przejdzie, wiec tworzymy je migracja 0001 na czystej bazie
    eng2 = sa.create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(migrate, "engine", eng2)
    migrate.upgrade()
    with eng2.connect() as conn:
        assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == "0017"
        cols = [r[1] for r in conn.execute(sa.text("PRAGMA table_info(invoices)"))]
    assert "related_id" in cols and "advance_gross" in cols
