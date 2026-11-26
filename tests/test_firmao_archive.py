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


NOTES = [
    {"id": 300, "customer": {"id": 10, "label": "Klient"}, "contact": {"id": 20}, "createdBy": {"id": 1, "label": "Arek"},
     "date": "2025-06-01T08:30:00Z", "description": "<p>Klient prosi o <b>telefon</b></p><p>po 15</p>", "important": True,
     "task": {"id": 5, "name": "Oddzwonić"}},
    {"id": 301, "customer": {}, "description": "bez klienta"},
]


def bundle():
    return {"customers": CUSTOMERS, "contacts": CONTACTS, "products": PRODUCTS, "transactions": TRANSACTIONS,
            "transactionentries": ENTRIES, "storagedocs": STORAGEDOCS, "offers": OFFERS, "salesnotes": NOTES}


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
        assert s["notatki"] == {"added": 1, "updated": 0, "skipped": 1}
        from app.models import ContractorNote
        note = db.query(ContractorNote).one()
        assert note.text == "❗ Klient prosi o telefon\npo 15\nZadanie: Oddzwonić" and note.author == "Arek"
        assert note.at.hour == 10 and note.contact is not None and note.contractor.source_ref == "firmao:10"

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
    # tylko wybrane czesci (np. same notatki, bez nadpisywania faktur)
    with SessionLocal() as db:
        inv = db.query(Invoice).filter_by(source_ref="firmao:2").one()
        inv.paid = inv.total_gross
        db.commit()
    assert firmao_cli.main([str(f), "--firma", "5551859956", "--tylko", "notatki"]) == 0
    with SessionLocal() as db:
        inv = db.query(Invoice).filter_by(source_ref="firmao:2").one()
        assert inv.to_pay == 0
    assert firmao_cli.main([str(f), "--firma", "5551859956", "--tylko", "faktury"]) == 2
    assert firmao_cli.main([str(f), "--firma", "5551859956", "--tylko", "cos"]) == 2


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


AGREEMENTS = [
    {"id": 400, "number": "2025/01/001/U", "name": "", "customer": {"id": 10, "emails": ["biuro@klient.pl"]},
     "customerName": "Klient Sp. z o.o.", "nipNumber": "8672239415", "issuePeriod": "MONTH", "issueDay": 1,
     "issueType": "AUTO_ISSUE_AND_SEND", "lastInvoiceIssueDate": "2026-11-01", "alwaysSendToKsef": True,
     "agreementStatus": "ACTIVE", "validFrom": "2025-01-01", "agreementDate": "2024-12-20", "daysToDueDate": 7,
     "paymentType": "TRANSFER", "mailTemplate": {"id": 4, "name": "Faktura automatyczna"}, "nettoPrice": 100,
     "annotations": "Abonament serwisowy", "copyAnnotations": True},
    {"id": 401, "number": "2024/03/002/U", "customer": {"id": 10}, "issuePeriod": "QUARTER", "issueDay": 20,
     "agreementStatus": "EXPIRED", "validFrom": "2024-03-01", "validTo": "2025-02-28", "nettoPrice": 50},
    # status "Nowa", ale Firmao z niej fakturuje (jak AUTO-ROJAN): ma fakturowac
    {"id": 402, "number": "2024/11/001/U", "customer": {"id": 10}, "issuePeriod": "MONTH", "issueDay": 1,
     "agreementStatus": "NEW", "validFrom": "2024-11-01", "lastInvoiceIssueDate": "2026-11-01", "nettoPrice": 115},
    # "wygasla" w Firmao, ale faktura byla w tym miesiacu: trwa dalej
    {"id": 403, "number": "2023/01/001/U", "customer": {"id": 10}, "issuePeriod": "MONTH", "issueDay": 1,
     "agreementStatus": "EXPIRED", "validFrom": "2023-01-01", "validTo": "2025-12-31",
     "lastInvoiceIssueDate": "2026-11-01", "nettoPrice": 80},
]
AGREEMENT_ENTRIES = [
    {"id": 1100, "agreement": {"id": 400}, "entryOrder": 1, "name": "Abonament monitoringu", "quantity": 1,
     "unit": "usługa", "unitNettoPrice": 100, "vatPercent": 23, "nettoPrice": 100, "vatPrice": 23, "bruttoPrice": 123},
]
RESOURCES = [
    {"id": 500, "name": "Wiertarka Hilti", "resourceClass": {"id": 1, "name": "Narzędzia"}, "serialNumber": "H-77",
     "localization": {"id": 1, "name": "AUTO Citroen Jumper"}, "supplier": {"id": 9, "label": "Hurtownia"},
     "acquisitionDate": "2023-05-10", "purchasePrice": 1299.99, "tags": [{"id": 1, "label": "akumulatorowa"}]},
]
TASKS = [
    {"id": 600, "name": "Przegląd kamer", "status": {"key": "OPEN", "group": "OPEN"}, "priority": 300,
     "customer": {"id": 10}, "address": "Długa 1, Chojnice", "plannedStartDate": "2026-12-03",
     "responsibleUsers": [{"id": 1, "label": "Arek"}], "category": {"key": "5", "label": "Serwis"}},
    {"id": 601, "name": "Montaż alarmu", "status": {"key": "CLOSED", "group": "CLOSED"}, "priority": 100,
     "actualEndDate": "2025-04-02T13:00:00Z", "actualWorkHours": 2.5, "description": "<p>Gotowe</p>"},
]


def test_agreements_equipment_tasks(client):
    import datetime as dt
    from app import firmao_archive
    from app.db import SessionLocal
    from app.models import Contractor, Equipment, Invoice, RecurringInvoice, Task
    data = dict(bundle(), agreements=AGREEMENTS, resources=RESOURCES, tasks=TASKS,
                transactionentries=ENTRIES + AGREEMENT_ENTRIES)
    data["transactions"] = [dict(TRANSACTIONS[0], agreements=[{"id": 400, "number": "2025/01/001/U"}]), *TRANSACTIONS[1:]]
    with SessionLocal() as db:
        firmao_archive.import_all(db, dict(data, agreements=[], resources=[], tasks=[]), _margot(db), log=lambda *_: None)
        db.commit()
        s = firmao_archive.import_all(db, data, _margot(db), log=lambda *_: None,
                                      only={"umowy", "sprzet", "zadania"})
        db.commit()
        assert s["umowy"]["added"] == 4 and s["umowy"]["linked_invoices"] == 1 and s["umowy"]["ksef_auto"] == 1
        r = db.query(RecurringInvoice).filter_by(source_ref="firmao:400").one()
        assert r.number == "2025/01/001/U" and r.name == "Umowa 2025/01/001/U" and r.mode == "draft"
        assert r.active and r.invoicing and r.end_date is None and r.email_to == "biuro@klient.pl"
        assert r.next_date == max(dt.date(2026, 12, 1), r.next_date) and r.next_date.day == 1
        assert r.notes == "Abonament serwisowy" and "wyślij automatycznie" in r.contract_notes
        assert '"Abonament monitoringu"' in r.items_json and r.signed_date == dt.date(2024, 12, 20)
        assert db.query(Contractor).filter_by(source_ref="firmao:10").one().ksef_auto
        assert db.query(Invoice).filter_by(source_ref="firmao:1").one().recurring_id == r.id
        old = db.query(RecurringInvoice).filter_by(source_ref="firmao:401").one()
        assert not old.active and not old.invoicing and old.interval_months == 3 and '"unit_price": "50.00"' in old.items_json
        new = db.query(RecurringInvoice).filter_by(source_ref="firmao:402").one()
        assert new.active and new.invoicing and new.mode == "draft" and new.status_label == "aktywna"
        still = db.query(RecurringInvoice).filter_by(source_ref="firmao:403").one()
        assert still.active and still.invoicing and still.end_date is None and "31.12.2025" in still.contract_notes
        e = db.query(Equipment).one()
        assert e.category == "Narzędzia" and e.location == "AUTO Citroen Jumper" and e.serial_no == "H-77"
        assert str(e.price) == "1299.99" and "akumulatorowa" in e.notes and e.contractor_id is None
        open_t = db.query(Task).filter_by(source_ref="firmao:600").one()
        assert open_t.status == "todo" and open_t.priority == "urgent" and open_t.contractor_id and open_t.all_day
        assert open_t.start_at == dt.datetime(2026, 12, 3) and open_t.assignee == "Arek" and open_t.category == "Serwis"
        done = db.query(Task).filter_by(source_ref="firmao:601").one()
        assert done.status == "done" and done.minutes_spent == 150 and done.description == "Gotowe" and done.done_at
        # powtorka bez dubli
        s = firmao_archive.import_all(db, data, _margot(db), log=lambda *_: None, only={"umowy", "sprzet", "zadania"})
        assert s["umowy"]["added"] == 0 and s["sprzet"]["added"] == 0 and s["zadania"]["added"] == 0
        db.commit()
    assert "2025/01/001/U" in client.get("/umowy").text
    assert "Wiertarka Hilti" in client.get("/sprzet").text
    assert "Przegląd kamer" in client.get("/zadania?widok=otwarte").text


def test_offer_statuses_only(client):
    from app.db import SessionLocal
    from app.firmao_archive import import_all
    from app.models import Company, Offer

    with SessionLocal() as db:
        company = db.get(Company, 1)
        db.add(Offer(company_id=1, number="O/1", source_ref="firmao:5", status="new", issue_date=__import__("datetime").date(2026, 1, 2),
                     title="", recipient_name="X", recipient_address="", contact_person="", email="", notes="",
                     internal_notes=""))
        db.commit()
        res = import_all(db, {"offers": [{"id": 5, "offerStatus": "DURING_NEGOTIATIONS"}, {"id": 6, "offerStatus": "SENT"}]},
                         company, log=lambda *_: None, only={"statusy_ofert"})
        db.commit()
        assert res["statusy_ofert"]["changed"] == 1 and res["statusy_ofert"]["missing"] == 1
        assert db.scalar(__import__("sqlalchemy").select(Offer.status).where(Offer.number == "O/1")) == "negotiating"
    assert "w trakcie negocjacji" in client.get("/oferty").text
