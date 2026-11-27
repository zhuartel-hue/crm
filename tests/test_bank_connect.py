"""Polaczenie z bankiem przez Enable Banking: JWT, zgoda, pobieranie operacji, dopasowanie wplat, wygasanie."""
import base64
import datetime as dt
import json
from decimal import Decimal

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from test_app import _new_invoice

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()
APP_ID = "11111111-2222-3333-4444-555555555555"


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class FakeEB:
    def __init__(self):
        self.calls = []
        self.txs = []
        self.valid_until = (dt.datetime.now(dt.UTC) + dt.timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    def __call__(self, method, url, headers, params=None, body=None):
        token = headers["Authorization"].split(" ", 1)[1]
        h, b, sig = token.split(".")
        KEY.public_key().verify(_unb64(sig), f"{h}.{b}".encode(), padding.PKCS1v15(), hashes.SHA256())
        assert json.loads(_unb64(h))["kid"] == APP_ID
        assert json.loads(_unb64(b))["aud"] == "api.enablebanking.com"
        path = url.replace("https://api.enablebanking.com", "")
        self.calls.append((method, path, params, body))
        req = httpx.Request(method, url)
        if path == "/aspsps":
            return httpx.Response(200, json={"aspsps": [
                {"name": "PKO Bank Polski", "country": "PL", "logo": "", "maximum_consent_validity": 15552000,
                 "psu_types": ["business", "personal"]}]}, request=req)
        if path == "/auth":
            self.state = body["state"]
            return httpx.Response(200, json={"url": "https://bank.example/login?x=1", "authorization_id": "a1"},
                                  request=req)
        if path == "/sessions" and method == "POST":
            assert body == {"code": "kod123"}
            return httpx.Response(200, json={
                "session_id": "sess-1", "access": {"valid_until": self.valid_until},
                "accounts": [{"uid": "acc-1", "account_id": {"iban": "PL12102055610000310200012345"},
                              "name": "Rachunek firmowy", "currency": "PLN"}]}, request=req)
        if path.startswith("/sessions/") and method == "DELETE":
            return httpx.Response(200, json={"message": "OK"}, request=req)
        if path == "/accounts/acc-1/transactions":
            if not params.get("continuation_key"):
                return httpx.Response(200, json={"transactions": self.txs[:1], "continuation_key": "k2"}, request=req)
            return httpx.Response(200, json={"transactions": self.txs[1:], "continuation_key": None}, request=req)
        return httpx.Response(404, json={"message": "Not found"}, request=req)


def _tx(ref, amount, title, name="FIRMA TESTOWA SP Z O O", ind="CRDT", status="BOOK"):
    return {"entry_reference": ref, "transaction_amount": {"amount": amount, "currency": "PLN"},
            "credit_debit_indicator": ind, "status": status, "booking_date": dt.date.today().isoformat(),
            "value_date": dt.date.today().isoformat(), "remittance_information": [title],
            "debtor": {"name": name}, "debtor_account": {"iban": "PL61109010140000071219812874"}}


@pytest.fixture()
def fake(monkeypatch):
    from app.bank import connect, enablebanking

    f = FakeEB()
    monkeypatch.setattr(enablebanking, "http", f)
    connect._ASPSP_CACHE.clear()
    return f


def test_jwt_is_rs256_with_kid():
    from app.bank.enablebanking import make_jwt

    tok = make_jwt(APP_ID, PEM, now=1_700_000_000)
    h, b, s = tok.split(".")
    assert json.loads(_unb64(h)) == {"typ": "JWT", "alg": "RS256", "kid": APP_ID}
    body = json.loads(_unb64(b))
    assert body["iss"] == "enablebanking.com" and body["exp"] - body["iat"] == 3600
    KEY.public_key().verify(_unb64(s), f"{h}.{b}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_to_row_maps_fields():
    from app.bank.connect import to_row

    acc = {"uid": "acc-1", "iban": "PL12", "currency": "PLN"}
    row = to_row(acc, _tx("r1", "123.45", "FV/1/2026"))
    assert row["amount"] == Decimal("123.45") and row["title"] == "FV/1/2026"
    assert row["counterparty"].startswith("FIRMA") and row["counterparty_account"].startswith("PL61")
    out = to_row(acc, {**_tx("r2", "10.00", "Opłata", ind="DBIT"), "creditor": {"name": "PKO"}})
    assert out["amount"] == Decimal("-10.00") and out["counterparty"] == "PKO"
    assert to_row(acc, _tx("r3", "5", "x", status="PDNG")) is None
    assert row["fingerprint"] != to_row(acc, _tx("r9", "123.45", "FV/1/2026"))["fingerprint"]


def test_full_flow_connect_sync_match(client, fake):
    from app.db import SessionLocal
    from app.models import BankConnection, BankTransaction, Invoice

    r = client.get("/bank/polaczenie")
    assert r.status_code == 200 and "Pierwsze uruchomienie" in r.text and "/bank/powrot" in r.text
    r = client.post("/bank/polaczenie/konfiguracja", data={"app_id": APP_ID},
                    files={"key_file": (f"{APP_ID}.pem", PEM.encode(), "application/x-pem-file")},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        from app.models import AppSetting
        stored = db.get(AppSetting, "enablebanking_key").value
        assert "PRIVATE KEY" not in stored  # zaszyfrowany

    r = client.get("/bank/polaczenie")
    assert "PKO Bank Polski" in r.text and "Podłącz wybrany bank" in r.text

    inv_id = _new_invoice(client, "vat")
    with SessionLocal() as db:
        inv = db.get(Invoice, inv_id)
        number, gross = inv.number, inv.total_gross
    fake.txs = [_tx("ref-1", f"{gross:.2f}", f"Zapłata {number}"), _tx("ref-2", "50.00", "Opłata", ind="DBIT")]

    r = client.post("/bank/polaczenie/nowe", data={"aspsp": "PKO Bank Polski", "psu_type": "business"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://bank.example/")
    auth_call = [c for c in fake.calls if c[1] == "/auth"][0]
    assert auth_call[3]["aspsp"] == {"name": "PKO Bank Polski", "country": "PL"}
    assert auth_call[3]["redirect_url"].endswith("/bank/powrot")

    r = client.get("/bank/powrot", params={"state": "zly", "code": "kod123"}, follow_redirects=False)
    assert r.headers["location"] == "/bank/polaczenie"
    r = client.get("/bank/powrot", params={"state": fake.state, "code": "kod123"}, follow_redirects=False)
    assert r.headers["location"] == "/bank"
    with SessionLocal() as db:
        conn = db.scalar(__import__("sqlalchemy").select(BankConnection))
        assert conn.state == "active" and conn.session_id == "sess-1" and conn.accounts[0]["uid"] == "acc-1"
        assert conn.days_left >= 178
        txs = db.scalars(__import__("sqlalchemy").select(BankTransaction)).all()
        assert len(txs) == 2
        paid = [t for t in txs if t.amount > 0][0]
        assert paid.state == "auto" and paid.invoice_id == inv_id
        assert db.get(Invoice, inv_id).to_pay == 0
        cid = conn.id

    # Drugie pobranie nie dubluje operacji
    r = client.post(f"/bank/polaczenie/{cid}/pobierz", follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert len(db.scalars(__import__("sqlalchemy").select(BankTransaction)).all()) == 2

    assert "pobiera automatycznie" in client.get("/bank").text

    # Zgoda wygasa: ostrzezenie na Start, potem stan expired
    with SessionLocal() as db:
        conn = db.get(BankConnection, cid)
        conn.valid_until = dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(days=5)
        db.commit()
    assert "wygasa za" in client.get("/").text
    with SessionLocal() as db:
        from app.bank import connect
        conn = db.get(BankConnection, cid)
        conn.valid_until = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(hours=1)
        db.commit()
        assert connect.sync_all(db) == 0
        assert db.get(BankConnection, cid).state == "expired"

    r = client.post(f"/bank/polaczenie/{cid}/odlacz", follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.get(BankConnection, cid).state == "closed"
    assert ("DELETE", "/sessions/sess-1", None, None) in fake.calls


def test_denied_consent_and_bad_key(client, fake):
    r = client.post("/bank/polaczenie/konfiguracja", data={"app_id": APP_ID, "key_text": "nie klucz"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "PEM" in client.get("/bank/polaczenie").text
    client.post("/bank/polaczenie/konfiguracja", data={"app_id": APP_ID, "key_text": PEM})
    client.post("/bank/polaczenie/nowe", data={"aspsp": "PKO Bank Polski"}, follow_redirects=False)
    r = client.get("/bank/powrot", params={"state": fake.state, "error": "access_denied",
                                           "error_description": "Cancelled by user"}, follow_redirects=False)
    assert r.headers["location"] == "/bank/polaczenie"
    assert "Cancelled by user" in client.get("/bank/polaczenie").text


def test_legal_pages_public():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        assert c.get("/polityka-prywatnosci").status_code == 200
        assert "Regulamin" in c.get("/regulamin").text
        assert c.get("/bank/powrot?state=x", follow_redirects=False).headers["location"] == "/login"


def test_payment_before_invoice_is_matched_later(client, fake):
    """Wplata przyszla, zanim faktura byla w programie: kolejne pobranie ja dopasowuje (po numerze w tytule)."""
    from sqlalchemy import select

    from app.bank import service
    from app.db import SessionLocal
    from app.models import BankTransaction, Company, Invoice

    with SessionLocal() as db:
        company = db.scalars(select(Company)).first()
        cid = company.id
        rows = [dict(account="PL1", booking_date=dt.date.today(), amount=Decimal("123.00"), title="Zapłata FV/1/01/2027",
                     counterparty="Jan Kowalski", fingerprint="fp-early"),
                dict(account="PL1", booking_date=dt.date.today() - dt.timedelta(days=200), amount=Decimal("123.00"),
                     title="Zapłata FV/1/01/2027", counterparty="Jan Kowalski", fingerprint="fp-old")]
        service.store(db, "test", company, rows, "eb")
        db.commit()
        assert db.scalar(select(BankTransaction).where(BankTransaction.fingerprint == "fp-early")).state == "none"
    client.post("/firma/%d" % cid)
    inv_id = _new_invoice(client, "vat")
    with SessionLocal() as db:
        assert db.get(Invoice, inv_id).number.startswith("FV/1/01/2027")
        service.store(db, "test", db.get(Company, cid), [], "eb")
        db.commit()
        early = db.scalar(select(BankTransaction).where(BankTransaction.fingerprint == "fp-early"))
        old = db.scalar(select(BankTransaction).where(BankTransaction.fingerprint == "fp-old"))
        assert early.state == "auto" and early.invoice_id == inv_id
        assert old.state == "none"  # starsze niz 120 dni zostaja bez zmian
        assert db.get(Invoice, inv_id).to_pay == 0
