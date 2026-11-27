"""Faktury kosztowe pobierane z KSeF (klient KSeF podmieniony)."""
import datetime as dt

from test_app import _new_invoice


class FakeKsef:
    pages = []
    xmls = {}
    queries = []

    def __init__(self, env, nip, cred):
        self.nip = nip

    def query_received(self, date_from, date_to=None, page_offset=0, page_size=100):
        self.queries.append((date_from, date_to, page_offset))
        return self.pages.pop(0) if self.pages else {"hasMore": False, "isTruncated": False, "invoices": []}

    def download(self, num):
        return self.xmls[num]


def _meta(num, stored, gross="1230.00", nip="5551859956", name="Margot"):
    return {"ksefNumber": num, "invoiceNumber": f"FV/{num[-3:]}", "issueDate": "2026-11-20",
            "permanentStorageDate": stored, "seller": {"nip": nip, "name": name},
            "buyer": {"identifier": {"type": "Nip", "value": "5552144076"}},
            "netAmount": 1000.0, "vatAmount": 230.0, "grossAmount": float(gross), "currency": "PLN",
            "invoiceType": "Vat"}


def test_fetch_and_verify_purchase_invoices(client, monkeypatch):
    from app.db import SessionLocal
    from app.ksef import service
    from app.ksef import purchases
    from app.models import Company, Invoice, PurchaseInvoice

    # Prawdziwy XML FA(3) z faktury Margot, jako koszt dla firmy 2.
    inv_id = _new_invoice(client, "vat", due_date="2026-12-31", item_name=["Wtyk RJ45"], item_price=["1230,00"])
    with SessionLocal() as db:
        xml = service.preview_xml(db.get(Invoice, inv_id))
        c2 = db.get(Company, 2)
        c2.ksef_env, c2.ksef_auth = "test", "token"
        from app import secretbox
        c2.ksef_token_enc = secretbox.seal("tok")
        db.commit()
    FakeKsef.xmls = {"5551859956-20261120-AAA111-01": xml, "5551859956-20261121-BBB222-02": xml}
    FakeKsef.pages = [
        {"hasMore": True, "isTruncated": False, "invoices": [_meta("5551859956-20261120-AAA111-01", "2026-11-20T10:00:00Z")]},
        {"hasMore": False, "isTruncated": False, "invoices": [_meta("5551859956-20261121-BBB222-02", "2026-11-21T10:00:00Z")]},
    ]
    FakeKsef.queries = []
    monkeypatch.setattr(service, "client_factory", FakeKsef)
    client.post("/firma/2")
    page = client.get("/koszty").text
    assert "Pobierz z KSeF" in page and "Od dnia" in page
    r = client.post("/koszty/pobierz", data={"since": "2026-11-01"})
    assert "Pobrano z KSeF 2 nowych" in r.text
    assert [q[2] for q in FakeKsef.queries] == [0, 1]
    assert FakeKsef.queries[0][0].startswith("2026-10-31T23:00:00")  # polnoc w Warszawie
    with SessionLocal() as db:
        rows = db.query(PurchaseInvoice).order_by(PurchaseInvoice.id).all()
        assert len(rows) == 2 and rows[0].company_id == 2 and rows[0].status == "new"
        assert rows[0].seller_name == "Margot" and rows[0].due_date == dt.date(2026, 12, 31)
        assert rows[0].contractor_id is None or rows[0].contractor_id
        assert db.get(Company, 2).ksef_purchase_cursor == "2026-11-21T10:00:00Z"
        pid = rows[0].id
    page = client.get("/koszty").text
    assert "do weryfikacji" in page and "1\xa0230,00" in page
    view = client.get(f"/koszty/{pid}").text
    assert "Wtyk RJ45" in view and "31.12.2026" in view
    # weryfikacja i zaplata
    client.post(f"/koszty/{pid}", data={"action": "accepted"})
    r = client.post(f"/koszty/{pid}", data={"action": "paid", "paid_at": "2026-11-25"})
    assert "zatwierdzona" in r.text and "zapłacona 25.11.2026" in r.text
    assert client.get(f"/koszty/{pid}/xml").content.startswith(b"<?xml")
    assert "AAA111" not in client.get("/koszty?status=nieoplacone").text
    # reczne odhaczenie z listy (kolumny KSeF i Zapłacona)
    page = client.get("/koszty").text
    assert "W KSeF: 5551859956-20261120-AAA111-01" in page and "/zaplacona" in page
    r = client.post(f"/koszty/{pid}/zaplacona", data={"paid": "0", "back": "/koszty?status=nieoplacone"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/koszty?status=nieoplacone"
    with SessionLocal() as db:
        assert db.get(PurchaseInvoice, pid).paid_at is None
    client.post(f"/koszty/{pid}/zaplacona", data={"paid": "1", "back": "https://zly.example"})
    with SessionLocal() as db:
        assert db.get(PurchaseInvoice, pid).paid_at == dt.date.today()
    # ponowne pobranie: od kursora, bez dubli
    FakeKsef.pages = [{"hasMore": False, "isTruncated": False,
                       "invoices": [_meta("5551859956-20261121-BBB222-02", "2026-11-21T10:00:00Z")]}]
    FakeKsef.queries = []
    assert "Brak nowych" in client.post("/koszty/pobierz").text
    assert FakeKsef.queries[0][0] == "2026-11-21T10:00:00.000Z"
    # firma 1 nie widzi kosztow firmy 2
    client.post("/firma/1")
    assert client.get(f"/koszty/{pid}").status_code == 404
    assert "KSeF jest wyłączony" in client.get("/koszty").text
    # tlo: pobiera tylko dla firm z wlaczonym KSeF
    with SessionLocal() as db:
        purchases.fetch_all(db)
