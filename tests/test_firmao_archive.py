"""Archiwum z Firmao: faktury z pozycjami, korekty, koszty, WZ/PZ, oferty, powiazania i dalsza numeracja."""
import gzip
import json
from decimal import Decimal

CUSTOMERS = [{"id": 10, "name": "Klient Sp. z o.o.", "nipNumber": "8672239415",
              "officeAddress": {"street": "Długa 1", "postCode": "89-600", "city": "Chojnice"}}]
CONTACTS = [{"id": 20, "firstName": "Anna", "lastName": "Nowak", "customer": {"id": 10}}]
PRODUCTS = [{"id": 30, "name": "Czujnik", "productCode": "CZ1", "product": True, "salePriceNet": 100.0,
             "salePriceVat": 23.0, "totalStoreState": 5.0}]

TRANSACTIONS = [
    {"id": 1, "mode": "SALE", "type": "INVOICE", "transactionNumber": "2026/10/007", "invoiceDate": "2026-10-20",
     "transactionDate": "2026-10-19", "paymentDate": "2026-11-03", "paymentType": "TRANSFER",
     "paymentStatus": "PAID_AFTER", "restOfPaidValue": 0, "customer": {"id": 10}, "customerName": "Klient Sp. z o.o.",
     "nipNumber": "867-223-94-15", "customerAddress": {"street": "Długa 1", "postCode": "89-600", "city": "Chojnice"},
     "transactionNettoPrice": 200, "transactionVatPrice": 46, "transactionBruttoPrice": 246,
     "ksefUploadDetails": {"uploadStatus": "UPLOADED", "ksefNumber": "5551859956-20261020-ABCDEF-01"},
     "storageDocs": [{"id": 100, "storageDocNumber": "2026/10/003/WZ", "deleted": False}],
     "offers": [{"id": 200}]},
    {"id": 2, "mode": "SALE", "type": "INVOICE", "transactionNumber": "2026/11/004", "invoiceDate": "2026-11-05",
     "transactionDate": "2026-11-05", "paymentType": "COD", "paymentStatus": "NOT_PAID", "restOfPaidValue": 123,
     "customerName": "Jan Kowalski", "transactionNettoPrice": 100, "transactionVatPrice": 23,
     "transactionBruttoPrice": 123},
    {"id": 3, "mode": "SALE", "type": "CORRECTION", "transactionNumber": "2026/11/001/KOR", "invoiceDate": "2026-11-10",
     "transactionDate": "2026-11-10", "paymentStatus": "NOT_PAID", "restOfPaidValue": -123, "customer": {"id": 10},
     "customerName": "Klient Sp. z o.o.", "correctionCause": "Zwrot", "transactionNettoPrice": -100,
     "transactionVatPrice": -23, "transactionBruttoPrice": -123,
     "correctedDocumentsInfo": [{"id": 1, "transactionNumber": "2026/10/007"}]},
    {"id": 4, "mode": "PURCHASE", "type": "INVOICE", "transactionNumber": "FV 12/2026", "invoiceDate": "2026-11-02",
     "customerName": "Hurtownia", "nipNumber": "1234567890", "paymentStatus": "PAID_BEFORE",
     "paymentDate": "2026-11-09", "transactionNettoPrice": 50, "transactionVatPrice": 11.5,
     "transactionBruttoPrice": 61.5, "ksefUploadDetails": {"uploadStatus": "IMPORTED"}},
]
ENTRIES = [
    {"id": 1001, "transaction": {"id": 1}, "entryOrder": 1, "name": "Czujnik", "product": {"id": 30}, "quantity": 2,
     "unit": "SZT", "unitNettoPrice": 100, "vatPercent": 23, "nettoPrice": 200, "vatPrice": 46, "bruttoPrice": 246},
    {"id": 1002, "transaction": {"id": 2}, "entryOrder": 1, "name": "Montaż", "quantity": 1, "unit": "usługa",
     "unitNettoPrice": 100, "vatPercent": 23, "nettoPrice": 100, "vatPrice": 23, "bruttoPrice": 123},
    # korekta: para przed (odwrocenie, ujemne) / po
    {"id": 1003, "transaction": {"id": 3}, "entryOrder": 1, "name": "Czujnik", "baseEntryId": 1001, "quantity": -2,
     "unit": "szt.", "unitNettoPrice": -100, "vatPercent": 23, "nettoPrice": -200, "vatPrice": -46, "bruttoPrice": -246,
     "entryCorrectionTransaction": True},
    {"id": 1004, "transaction": {"id": 3}, "entryOrder": 2, "name": "Czujnik", "invalidEntry": {"id": 1003},
     "quantity": 1, "unit": "szt.", "unitNettoPrice": 100, "vatPercent": 23, "nettoPrice": 100, "vatPrice": 23,
     "bruttoPrice": 123, "entryCorrectionTransaction": True},
    {"id": 1005, "storageDoc": {"id": 100}, "entryOrder": 1, "name": "Czujnik", "product": {"id": 30}, "quantity": 2,
     "unit": "szt", "unitNettoPrice": 100, "vatPercent": 23, "nettoPrice": 200, "vatPrice": 46, "bruttoPrice": 246},
    {"id": 1006, "offer": {"id": 200}, "entryOrder": 1, "name": "Czujnik", "quantity": 2, "unit": "szt.",
     "unitNettoPrice": 100, "vatPercent": 23, "nettoPrice": 200, "vatPrice": 46, "bruttoPrice": 246},
]
STORAGEDOCS = [
    {"id": 100, "type": "OUTSIDE_RELEASE", "storageDocNumber": "2026/10/003/WZ", "storagedocDate": "2026-10-19",
     "customer": {"id": 10}, "customerName": "Klient Sp. z o.o.", "storageDocNettoPrice": 200,
     "storageDocVatPrice": 46, "storageDocBruttoPrice": 246, "receivingPerson": "Anna Nowak"},
    {"id": 101, "type": "OUTSIDE_INCOME", "storageDocNumber": "2026/09/001/PZ", "storagedocDate": "2026-09-01"},
]
OFFERS = [
    {"id": 200, "type": "OFFER", "number": "2026/10/002/O/S", "offerDate": "2026-10-10", "paymentDate": "2026-10-24",
     "offerStatus": {"key": "EXECUTED"}, "acceptProbability": 80, "customer": {"id": 10},
     "customerName": "Klient Sp. z o.o.", "nettoPrice": 200, "vatPrice": 46, "bruttoPrice": 246,
     "storageDocs": [{"id": 100}]},
    {"id": 201, "type": "ORDER", "number": "ZAM/1"},
]


def bundle():
    return {"customers": CUSTOMERS, "contacts": CONTACTS, "products": PRODUCTS, "transactions": TRANSACTIONS,
            "transactionentries": ENTRIES, "storagedocs": STORAGEDOCS, "offers": OFFERS}


def _margot(db):
    from app.models import Company
    return db.query(Company).filter_by(nip="5551859956").one()


def test_archive_import(client):
    from app import firmao_archive, numbering
    from app.db import SessionLocal
    from app.models import Delivery, Invoice, Offer, PurchaseInvoice

    with SessionLocal() as db:
        s = firmao_archive.import_all(db, bundle(), _margot(db), log=lambda *_: None)
        db.commit()
        assert s["faktury"]["added"] == 3 and s["zakupy"]["added"] == 1
        assert s["wz"]["added"] == 2 and s["oferty"]["added"] == 1 and s["oferty"]["skipped"] == 1
        assert s["powiazania"] == 3

        inv = db.query(Invoice).filter_by(source_ref="firmao:1").one()
        assert inv.number == "2026/10/007" and inv.kind == "vat" and inv.status == "issued"
        assert inv.paid == Decimal("246.00") and inv.to_pay == 0
        assert inv.ksef_status == "accepted" and inv.ksef_number.startswith("5551859956")
        assert inv.contractor_id is not None and inv.buyer_nip == "8672239415"
        assert [(i.name, i.quantity, i.unit, i.net) for i in inv.items] == [("Czujnik", Decimal("2.000"), "szt.",
                                                                              Decimal("200.00"))]
        assert inv.items[0].product_id is not None
        inv_id = inv.id

        unpaid = db.query(Invoice).filter_by(source_ref="firmao:2").one()
        assert unpaid.paid == 0 and unpaid.payment_method == "cash" and unpaid.ksef_status == "external"
        assert unpaid.items[0].unit == "usł."

        kor = db.query(Invoice).filter_by(source_ref="firmao:3").one()
        assert kor.kind == "correction" and kor.related_id == inv.id and kor.correction_reason == "Zwrot"
        assert [(i.quantity, i.unit_price, i.net) for i in kor.before_items] == [
            (Decimal("2.000"), Decimal("100.00"), Decimal("200.00"))]
        assert [(i.quantity, i.net) for i in kor.after_items] == [(Decimal("1.000"), Decimal("100.00"))]
        assert kor.total_gross == Decimal("-123.00")

        p = db.query(PurchaseInvoice).one()
        assert p.invoice_number == "FV 12/2026" and p.ksef_number == "FIRMAO-4" and p.paid_at is not None

        wz = db.query(Delivery).filter_by(kind="wz").one()
        assert wz.number == "2026/10/003/WZ" and wz.invoice_id == inv.id and wz.signed_by == "Anna Nowak"
        assert len(wz.items) == 1 and wz.items[0].unit == "szt."

        of = db.query(Offer).one()
        assert of.status == "done" and of.probability == 80 and of.invoice_id == inv.id and of.delivery_id == wz.id
        assert len(of.items) == 1

        m = _margot(db)
        assert m.pattern_vat == "{RRRR}/{MM}/{NNN}" and m.pattern_correction == "{RRRR}/{MM}/{NNN}/KOR"
        assert m.pattern_wz == "{RRRR}/{MM}/{NNN}/WZ" and m.pattern_pz == "{RRRR}/{MM}/{NNN}/PZ"
        assert m.pattern_offer == "{RRRR}/{MM}/{NNN}/O/S" and m.numbering_reset == "month"

        import datetime as dt
        assert numbering.next_number(db, m, "vat", dt.date(2026, 11, 20))[1] == "2026/11/005"
        assert numbering.next_number(db, m, "correction", dt.date(2026, 11, 20))[1] == "2026/11/002/KOR"
        assert numbering.next_number(db, m, "wz", dt.date(2026, 10, 30))[1] == "2026/10/004/WZ"
        assert numbering.next_number(db, m, "offer", dt.date(2026, 12, 1))[1] == "2026/12/001/O/S"
        db.rollback()

    # powtorka: bez dubli
    with SessionLocal() as db:
        s = firmao_archive.import_all(db, bundle(), _margot(db), log=lambda *_: None)
        db.commit()
        assert s["faktury"] == {"added": 0, "updated": 3, "skipped": 0, "renamed": 0, "skipped_types": {}}
        assert s["zakupy"]["added"] == 0 and s["wz"]["added"] == 0 and s["oferty"]["added"] == 0
        assert db.query(Invoice).count() == 3
        kor = db.query(Invoice).filter_by(source_ref="firmao:3").one()
        assert len(kor.items) == 2
        kor_id = kor.id

    # imported invoices show in the app and are not offered for KSeF sending
    r = client.get(f"/faktury/{inv_id}")
    assert r.status_code == 200 and "2026/10/007" in r.text
    r = client.get(f"/faktury/{kor_id}")
    assert r.status_code == 200 and "2026/11/001/KOR" in r.text and "Wyślij do KSeF" not in r.text
    assert client.get(f"/faktury/{kor_id}/pdf").status_code == 200


def test_cli_dry_run(client, tmp_path, capsys):
    from app import firmao_cli
    from app.db import SessionLocal
    from app.models import Invoice

    f = tmp_path / "b.json.gz"
    f.write_bytes(gzip.compress(json.dumps(bundle()).encode()))
    assert firmao_cli.main([str(f), "--firma", "555-185-99-56", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "PROBA" in out and "'faktury': 3" in out
    with SessionLocal() as db:
        assert db.query(Invoice).count() == 0
    assert firmao_cli.main([str(f), "--firma", "5551859956"]) == 0
    with SessionLocal() as db:
        assert db.query(Invoice).count() == 3
    assert firmao_cli.main([str(f), "--firma", "1111111111"]) == 2


def test_bundle_tool(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    src = tmp_path / "eks"
    src.mkdir()
    (src / "transactions.json").write_text(json.dumps([{"id": 1, "images": [1], "x": None, "y": "", "z": 5}]))
    dst = tmp_path / "b.json.gz"
    tool = Path(__file__).parent.parent / "tools" / "firmao_bundle.py"
    subprocess.run([sys.executable, str(tool), "--zrodlo", str(src), "--cel", str(dst)], check=True,
                   capture_output=True)
    data = json.loads(gzip.decompress(dst.read_bytes()))
    assert data["transactions"] == [{"id": 1, "z": 5}] and data["offers"] == []
