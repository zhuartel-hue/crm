"""Import wyciagu MT940 (format iPKO) i dopasowanie wplat do faktur."""
from decimal import Decimal

from test_app import _new_invoice

ACC = "12102055610000310200012345"
E = "\xa0"  # pusty podpis: bajt 255 w CP852


def _sub(code, text=""):
    return f"~{code}{text}" if text else f"~{code}{E}"


def _op(vd, amt, mark, code, title, name, ref="NONREF//", opno="6460500500000513"):
    lines = [f":61:{vd}{vd[2:]}{mark}{amt}N{code}{ref}{opno}", f"{code} 0", f":86:020~00{code}",
             _sub("20", title[:27]), _sub("21", title[27:54]), _sub("22"), _sub("23"), _sub("24"), _sub("25"),
             _sub("3010205561"[2:] if False else "30", "10205561"), _sub("31", "0000310200099999"),
             _sub("32", name[:27]), _sub("33", name[27:54]), _sub("38", "PL61109010140000071219812874"),
             _sub("60"), _sub("63")]
    return lines


def _file(ops, number="1"):
    lines = [":20:MT940", f":25:/PL{ACC}", f":28C:{number}", ":60F:C270115PLN1000,00"]
    for op in ops:
        lines += op
    lines += [":62F:C270115PLN9999,00", ":64:C270115PLN9999,00", "-"]
    return "\r\n".join(lines).encode("cp852")


def _set_account():
    from app.db import SessionLocal
    from app.models import Company

    with SessionLocal() as db:
        c = db.get(Company, 1)
        c.bank_account = "12 1020 5561 0000 3102 0001 2345"
        db.commit()


def _inv(i):
    from app.db import SessionLocal
    from app.models import Invoice

    db = SessionLocal()
    return db, db.get(Invoice, i)


def test_mt940_parser():
    from app.bank import mt940

    data = _file([_op("270115", "150,00", "C", "150", "Zapłata za FV/1/01/2027", "JAN KOWALSKI UL. PROSTA 1 00-001 WARSZAWA"),
                  _op("270115", "25,00", "D", "152", "Opłata", "PKO BP", opno="6460500500000514")])
    [st] = mt940.parse(data)
    assert st.account == ACC and st.opening == Decimal("1000.00") and st.closing == Decimal("9999.00")
    t1, t2 = st.transactions
    assert t1.amount == Decimal("150.00") and t1.title == "Zapłata za FV/1/01/2027"
    assert t1.counterparty.startswith("JAN KOWALSKI") and t1.counterparty_account == "PL61109010140000071219812874"
    assert t2.amount == Decimal("-25.00") and t1.fingerprint != t2.fingerprint
    assert str(t1.value_date) == "2027-01-15"


def test_import_matches_payments(client):
    _set_account()
    a = _new_invoice(client, "vat", item_price=["150,00"])  # FV/1/01/2027, Jan Kowalski
    b = _new_invoice(client, "vat", item_price=["999,99"], buyer_name="Auto-Handel Nowak Sp. z o.o.",
                     buyer_nip="5552144076")  # FV/2/01/2027
    c = _new_invoice(client, "vat", item_price=["500,00"], buyer_name="Zenon Wiśniewski")  # FV/3/01/2027
    d = _new_invoice(client, "vat", item_price=["700,00"], buyer_name="Anna Mała")  # FV/4/01/2027
    data = _file([
        _op("270115", "150,00", "C", "150", "Zapłata za FV/1/01/2027", "JAN KOWALSKI WARSZAWA", opno="0000000000000001"),
        _op("270116", "999,99", "C", "150", "zaplata faktura", "AUTO-HANDEL NOWAK SP. Z O.O.", opno="0000000000000002"),
        _op("270116", "500,00", "C", "150", "przelew", "KTOS INNY", opno="0000000000000003"),
        _op("270117", "50,00", "C", "150", "fv 4/01/2027 czesc", "ANNA MALA", opno="0000000000000004"),
        _op("270117", "1,00", "C", "150", "zwrot", "NIKT", opno="0000000000000005"),
        _op("270117", "25,00", "D", "152", "Opłata", "PKO BP", opno="0000000000000006"),
    ])
    r = client.post("/bank/import", files=[("files", ("wyciag.txt", data, "text/plain"))], follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/bank?pokaz=wszystkie").text
    assert "6 nowych operacji" in page and "3 wpłat zaksięgowanych automatycznie" in page
    assert "1 propozycji" in page

    db, inv = _inv(a); assert inv.paid == Decimal("150.00"); db.close()
    db, inv = _inv(b); assert inv.paid == Decimal("999.99"); db.close()
    db, inv = _inv(c); assert inv.paid == Decimal("0"); db.close()  # tylko propozycja (ta sama kwota, inny nadawca)
    db, inv = _inv(d); assert inv.paid == Decimal("50.00"); db.close()  # numer w tytule, czesciowa wplata

    # Ten sam plik drugi raz: nic nie dochodzi.
    again = client.post("/bank/import", files=[("files", ("wyciag.txt", data, "text/plain"))])
    assert "0 nowych operacji, 6 już było" in again.text

    from app.db import SessionLocal
    from app.models import BankTransaction
    from sqlalchemy import select

    with SessionLocal() as s:
        txs = {t.bank_ref: t for t in s.scalars(select(BankTransaction))}
        assert len(txs) == 6
        sug, none, out = txs["0000000000000003"], txs["0000000000000005"], txs["0000000000000006"]
        assert sug.state == "suggested" and sug.invoice_id == c
        assert none.state == "none" and out.state == "out" and out.amount == Decimal("-25.00")
        first = txs["0000000000000001"].id
        sug_id, none_id = sug.id, none.id

    # Potwierdzenie propozycji
    assert client.post(f"/bank/{sug_id}/potwierdz", data={}, follow_redirects=False).status_code == 303
    db, inv = _inv(c); assert inv.paid == Decimal("500.00"); db.close()
    # Reczne przypisanie po numerze faktury i cofniecie
    client.post(f"/bank/{none_id}/potwierdz", data={"invoice": "FV/4/01/2027"})
    db, inv = _inv(d); assert inv.paid == Decimal("51.00"); db.close()
    client.post(f"/bank/{none_id}/cofnij")
    db, inv = _inv(d); assert inv.paid == Decimal("50.00"); db.close()
    client.post(f"/bank/{first}/cofnij")
    db, inv = _inv(a); assert inv.paid == Decimal("0"); db.close()
    # Nie mozna zaksiegowac na fakture zaplacona
    r = client.post(f"/bank/{none_id}/potwierdz", data={"invoice": "FV/2/01/2027"})
    assert "już zapłacona" in r.text
    # Wplata widoczna na fakturze
    assert "Wpłaty z banku" in client.get(f"/faktury/{b}").text


def test_bad_file(client):
    r = client.post("/bank/import", files=[("files", ("x.txt", b"to nie jest wyciag", "text/plain"))])
    assert "To nie jest plik MT940" in r.text
