"""Test calego przeplywu KSeF na SRODOWISKU TESTOWYM MF (api-test.ksef.mf.gov.pl), z fikcyjnym NIP.

Nie dotyka bazy programu ani prawdziwych firm: uzywa tymczasowej bazy SQLite i losowego NIP.
Na srodowisku testowym MF wolno logowac sie certyfikatem samopodpisanym (srodowiska.md), wiec skrypt:
1. tworzy certyfikat pieczeci z organizationIdentifier VATPL-<NIP> i loguje sie podpisem XAdES,
2. generuje token KSeF (POST /tokens/rev2) i czeka na status Active,
3. tym tokenem (jak program na co dzien) wysyla: fakture VAT, korekte, zaliczkowa i koncowa,
4. wypisuje numery KSeF, statusy i linki do kodow QR.

Uruchomienie (z katalogu repozytorium, potrzebne: pip install -r requirements.txt signxml):
    python scripts/ksef_test_e2e.py            # losowy NIP
    python scripts/ksef_test_e2e.py --nip 1234567890
    python scripts/ksef_test_e2e.py --token <token KSeF>  --nip <NIP>   # bez XAdES
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="ksef-e2e-")
os.environ["DATABASE_URL"] = f"sqlite:///{TMP}/e2e.db"
os.environ.setdefault("SECRET_KEY", "e2e-test")
os.environ.setdefault("ADMIN_PASSWORD", "e2e-test")

import httpx  # noqa: E402

API = "https://api-test.ksef.mf.gov.pl/v2"


def random_nip() -> str:
    w = [6, 5, 7, 2, 3, 4, 5, 6, 7]
    while True:
        d = [random.randint(1, 9)] + [random.randint(0, 9) for _ in range(8)]
        c = sum(a * b for a, b in zip(d, w)) % 11
        if c != 10:
            return "".join(map(str, d)) + str(c)


def poll(fn, done, timeout=90, every=2):
    end = time.monotonic() + timeout
    while True:
        r = fn()
        if done(r) or time.monotonic() > end:
            return r
        time.sleep(every)


def check(r: httpx.Response):
    if r.status_code >= 400:
        raise SystemExit(f"HTTP {r.status_code} {r.request.method} {r.request.url}: {r.text[:1500]}")
    return r.json() if r.content else {}


def token_via_xades(http: httpx.Client, nip: str) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ObjectIdentifier
    from lxml import etree
    from signxml import SignatureMethod, DigestAlgorithm
    from signxml.xades import XAdESSigner

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, f"Test Sygnavo {nip}"),
        x509.NameAttribute(ObjectIdentifier("2.5.4.97"), f"VATPL-{nip}"),
        x509.NameAttribute(NameOID.COMMON_NAME, f"Test Sygnavo {nip}"),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30)).sign(key, hashes.SHA256()))

    ch = check(http.post("/auth/challenge"))
    ns = "http://ksef.mf.gov.pl/auth/token/2.0"
    root = etree.Element("{%s}AuthTokenRequest" % ns, nsmap={None: ns})
    etree.SubElement(root, "{%s}Challenge" % ns).text = ch["challenge"]
    ci = etree.SubElement(root, "{%s}ContextIdentifier" % ns)
    etree.SubElement(ci, "{%s}Nip" % ns).text = nip
    etree.SubElement(root, "{%s}SubjectIdentifierType" % ns).text = "certificateSubject"
    signer = XAdESSigner(signature_algorithm=SignatureMethod.RSA_SHA256, digest_algorithm=DigestAlgorithm.SHA256)
    signed = signer.sign(root, key=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption()),
                         cert=[cert.public_bytes(serialization.Encoding.PEM).decode()])
    body = etree.tostring(signed, xml_declaration=True, encoding="UTF-8")
    init = check(http.post("/auth/xades-signature", content=body, headers={"Content-Type": "application/xml"}))
    auth = init["authenticationToken"]["token"]
    st = poll(lambda: check(http.get(f"/auth/rev2/{init['referenceNumber']}", headers={"Authorization": f"Bearer {auth}"})),
              lambda d: d["status"]["code"] != 100)
    if st["status"]["code"] != 200:
        raise SystemExit(f"XAdES: {st['status']}")
    at = check(http.post("/auth/token/redeem", headers={"Authorization": f"Bearer {auth}"}))["accessToken"]["token"]
    print("✓ logowanie certyfikatem testowym (XAdES)")
    gen = check(http.post("/tokens/rev2", headers={"Authorization": f"Bearer {at}"},
                          json={"permissions": ["InvoiceWrite", "InvoiceRead"], "description": "Sygnavo test e2e"}))
    st = poll(lambda: check(http.get(f"/tokens/rev2/{gen['referenceNumber']}", headers={"Authorization": f"Bearer {at}"})),
              lambda d: d["status"] not in ("Pending",))
    if st["status"] != "Active":
        raise SystemExit(f"Token KSeF ma status {st['status']}")
    print("✓ token KSeF wygenerowany i aktywny")
    return gen["token"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nip")
    ap.add_argument("--token")
    args = ap.parse_args()
    nip = args.nip or random_nip()
    print(f"Środowisko TESTOWE MF, NIP (fikcyjny): {nip}")
    http = httpx.Client(base_url=API, timeout=30)
    token = args.token or token_via_xades(http, nip)

    from app import migrate, services
    from app.db import SessionLocal
    from app.ksef import service as ksef
    from app.ksef.client import KsefClient
    from app.models import Company

    migrate.upgrade()
    db = SessionLocal()
    c = Company(short_name="Test", name=f"Firma Testowa {nip}", nip=nip, street="ul. Testowa 1", postal_code="00-001",
                city="Warszawa", bank_account="11 1111 1111 1111 1111 1111 1111", bank_name="Bank Testowy",
                ksef_env="test", issue_place="Warszawa")
    db.add(c)
    db.commit()
    ksef.set_token(db, "e2e", c, "test", token)
    ksef.client_factory = lambda env, n, t: KsefClient(env, n, t, poll_timeout=90, poll_interval=2)
    buyer = {"name": "Nabywca Testowy", "nip": random_nip(), "address": "ul. Kupiecka 2\n00-002 Warszawa"}
    suffix = random.randint(1000, 9999)
    c.pattern_vat = f"E2E/{suffix}/{{nr}}/{{MM}}/{{RRRR}}"
    c.pattern_correction = f"E2E-K/{suffix}/{{nr}}/{{MM}}/{{RRRR}}"
    c.pattern_advance = f"E2E-Z/{suffix}/{{nr}}/{{MM}}/{{RRRR}}"
    db.commit()

    def issue(**data):
        data.setdefault("company", c.id)
        data.setdefault("buyer", buyer)
        inv = services.create_invoice_draft(db, "e2e", data)
        return services.issue_invoice(db, "e2e", inv.id)

    def send(inv, label):
        inv = ksef.send(db, "e2e", inv.id)
        mark = "✓" if inv.ksef_status == "accepted" else "✗"
        print(f"{mark} {label} {inv.number}: {ksef.status_label(inv)} {inv.ksef_number} {inv.ksef_message}")
        if inv.ksef_status == "accepted":
            print(f"    QR: {ksef.qr_url(inv)}   UPO: {'tak' if inv.ksef_upo else 'nie'}")
        return inv

    ok = True
    fv = send(issue(items=[{"name": "Opony zimowe", "quantity": 4, "unit_price": "400.00"},
                           {"name": "Wymiana", "unit_price": "108.00", "vat_rate": "8"}]), "Faktura VAT")
    ok &= fv.ksef_status == "accepted"
    kor = services.start_correction(db, "e2e", fv.id, "Rabat", [{"name": "Opony zimowe", "quantity": 4, "unit_price": "350.00"},
                                                                {"name": "Wymiana", "unit_price": "108.00", "vat_rate": "8"}])
    kor = send(services.issue_invoice(db, "e2e", kor.id), "Korekta")
    ok &= kor.ksef_status == "accepted"
    adv = send(issue(kind="advance", advance_gross="10000", items=[{"name": "Samochód testowy", "unit_price": "61500"}]),
               "Zaliczkowa")
    ok &= adv.ksef_status == "accepted"
    fin = services.start_final(db, "e2e", adv.id)
    fin = send(services.issue_invoice(db, "e2e", fin.id), "Końcowa")
    ok &= fin.ksef_status == "accepted"
    priv = send(issue(buyer={"name": "Jan Testowy", "address": "ul. Prywatna 3\n00-003 Warszawa"}, prices_gross=False,
                      payment_method="cash", items=[{"name": "Usługa", "unit_price": "100"}]), "Osoba prywatna, netto")
    ok &= priv.ksef_status == "accepted"
    print("WYNIK:", "wszystko przyjęte" if ok else "są błędy (szczegóły wyżej)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
