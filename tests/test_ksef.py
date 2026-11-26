"""KSeF: XML FA(3) zgodny ze schematem MF, szyfrowanie, kod QR, wysylka (klient KSeF podmieniony)."""
import base64
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

XSD = Path(__file__).parent / "ksef_xsd" / "schemat_FA(3)_v1-0E.xsd"
NS = {"f": "http://crd.gov.pl/wzor/2025/06/25/13775/"}
_schema = None


def validate(xml: bytes):
    global _schema
    if _schema is None:
        _schema = etree.XMLSchema(etree.parse(str(XSD)))
    doc = etree.fromstring(xml)
    if not _schema.validate(doc):
        raise AssertionError("\n".join(str(e) for e in _schema.error_log))
    return doc


def t(doc, path):
    return [e.text for e in doc.iterfind(path, NS)]


@pytest.fixture()
def db(client):
    from app.db import SessionLocal

    with SessionLocal() as s:
        yield s


BUYER = {"name": "Firma Testowa Sp. z o.o.", "nip": "5260250995", "address": "ul. Prosta 1\n00-001 Warszawa"}


def _issue(db, **data):
    from app import services

    data.setdefault("company", "Margot")
    data.setdefault("buyer", BUYER)
    inv = services.create_invoice_draft(db, "test", data)
    return services.issue_invoice(db, "test", inv.id)


def _xml(inv):
    from app.ksef import service

    return service.preview_xml(inv)


def test_vat_invoice_xml(db):
    inv = _issue(db, items=[{"name": "Opony", "quantity": 4, "unit_price": "400.00"},
                            {"name": "Usługa", "unit_price": "100.00", "vat_rate": "8"},
                            {"name": "Książka", "unit_price": "50.00", "vat_rate": "5"},
                            {"name": "Eksport usług", "unit_price": "10.00", "vat_rate": "np"}],
                 payment_method="transfer", due_days=7)
    doc = validate(_xml(inv))
    assert t(doc, ".//f:Fa/f:RodzajFaktury") == ["VAT"]
    assert t(doc, ".//f:Fa/f:P_2") == [inv.number]
    assert t(doc, ".//f:Podmiot1/f:DaneIdentyfikacyjne/f:NIP") == ["5551859956"]
    assert t(doc, ".//f:Podmiot2/f:DaneIdentyfikacyjne/f:NIP") == ["5260250995"]
    assert t(doc, ".//f:Fa/f:P_13_1") == ["1300.81"] and t(doc, ".//f:Fa/f:P_14_1") == ["299.19"]
    assert t(doc, ".//f:Fa/f:P_13_2") == ["92.59"] and t(doc, ".//f:Fa/f:P_14_2") == ["7.41"]
    assert t(doc, ".//f:Fa/f:P_13_8") == ["10.00"]
    assert t(doc, ".//f:Fa/f:P_15") == ["1760.00"]
    # ceny brutto: P_9B i P_11A
    assert t(doc, ".//f:FaWiersz/f:P_9B")[0] == "400" and t(doc, ".//f:FaWiersz/f:P_11A")[0] == "1600.00"
    assert t(doc, ".//f:FaWiersz/f:P_12") == ["23", "8", "5", "np I"]
    assert t(doc, ".//f:Platnosc/f:FormaPlatnosci") == ["6"]
    assert t(doc, ".//f:RachunekBankowy/f:NrRB") == ["89102014910000480201320720"]


def test_private_buyer_net_prices_cash(db):
    inv = _issue(db, buyer={"name": "Jan Kowalski", "address": "ul. Lipowa 2\n89-600 Chojnice"},
                 prices_gross=False, payment_method="cash",
                 items=[{"name": "Auto", "unit_price": "10000.00", "quantity": "1"}], notes="Dziękujemy!")
    doc = validate(_xml(inv))
    assert t(doc, ".//f:Podmiot2/f:DaneIdentyfikacyjne/f:BrakID") == ["1"]
    assert t(doc, ".//f:FaWiersz/f:P_9A") == ["10000"] and t(doc, ".//f:FaWiersz/f:P_11") == ["10000.00"]
    assert t(doc, ".//f:Platnosc/f:FormaPlatnosci") == ["1"]
    assert t(doc, ".//f:Stopka/f:Informacje/f:StopkaFaktury") == ["Dziękujemy!"]


def test_zw_requires_basis(db):
    from app.services import ServiceError

    inv = _issue(db, items=[{"name": "Auto używane", "unit_price": "20000", "vat_rate": "zw"}])
    with pytest.raises(ServiceError, match="podstawę zwolnienia"):
        _xml(inv)
    inv.exemption_basis = "art. 43 ust. 1 pkt 2 ustawy o VAT"
    doc = validate(_xml(inv))
    assert t(doc, ".//f:Zwolnienie/f:P_19A") == ["art. 43 ust. 1 pkt 2 ustawy o VAT"]
    assert t(doc, ".//f:Fa/f:P_13_7") == ["20000.00"]


def test_correction_xml(db):
    from app import services

    fv = _issue(db, items=[{"name": "Opony", "quantity": 4, "unit_price": "400.00"}])
    kor = services.start_correction(db, "test", fv.id, "Rabat", [{"name": "Opony", "quantity": 4, "unit_price": "300.00"}])
    kor = services.issue_invoice(db, "test", kor.id)
    doc = validate(_xml(kor))
    assert t(doc, ".//f:Fa/f:RodzajFaktury") == ["KOR"]
    assert t(doc, ".//f:Fa/f:P_13_1") == ["-325.20"] and t(doc, ".//f:Fa/f:P_14_1") == ["-74.80"]
    assert t(doc, ".//f:Fa/f:P_15") == ["-400.00"]
    assert t(doc, ".//f:DaneFaKorygowanej/f:NrFaKorygowanej") == [fv.number]
    assert t(doc, ".//f:DaneFaKorygowanej/f:NrKSeFN") == ["1"]
    assert t(doc, ".//f:FaWiersz/f:StanPrzed") == ["1"]
    # jak w przykladach MF: wiersz "przed" i "po" maja ten sam NrWierszaFa
    assert t(doc, ".//f:FaWiersz/f:NrWierszaFa") == ["1", "1"]
    assert t(doc, ".//f:Fa/f:PrzyczynaKorekty") == ["Rabat"]
    # gdy faktura korygowana jest w KSeF, korekta podaje jej numer KSeF
    fv.ksef_status, fv.ksef_number = "accepted", "5551859956-20270120-0100001AF629-AF"
    doc = validate(_xml(kor))
    assert t(doc, ".//f:DaneFaKorygowanej/f:NrKSeFFaKorygowanej") == [fv.ksef_number]


def test_advance_and_final_xml(db):
    from app import services

    adv = _issue(db, kind="advance", advance_gross="12300",
                 items=[{"name": "Auto", "unit_price": "61500"}, {"name": "Usługa", "unit_price": "1080", "vat_rate": "8"}])
    doc = validate(_xml(adv))
    assert t(doc, ".//f:Fa/f:RodzajFaktury") == ["ZAL"]
    assert t(doc, ".//f:Fa/f:P_15") == ["12300.00"]
    assert t(doc, ".//f:Zamowienie/f:WartoscZamowienia") == ["62580.00"]
    assert t(doc, ".//f:Fa/f:P_6") == [adv.sale_date.isoformat()]
    assert t(doc, ".//f:Platnosc/f:Zaplacono") == ["1"]
    assert len(doc.findall(".//f:FaWiersz", NS)) == 0 and len(doc.findall(".//f:ZamowienieWiersz", NS)) == 2
    fin = services.start_final(db, "test", adv.id)
    fin = services.issue_invoice(db, "test", fin.id)
    doc = validate(_xml(fin))
    assert t(doc, ".//f:Fa/f:RodzajFaktury") == ["ROZ"]
    assert t(doc, ".//f:Fa/f:P_15") == ["50280.00"]
    assert t(doc, ".//f:FakturaZaliczkowa/f:NrFaZaliczkowej") == [adv.number]
    assert len(doc.findall(".//f:FaWiersz", NS)) == 2


def test_proforma_not_sent(db):
    from app.services import ServiceError

    pro = _issue(db, kind="proforma", items=[{"name": "X", "unit_price": "10"}])
    with pytest.raises(ServiceError, match="nie jest wysyłany"):
        _xml(pro)


def test_crypto_roundtrip():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import NameOID

    from app.ksef import crypto

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(dt.datetime(2025, 1, 1)).not_valid_after(dt.datetime(2030, 1, 1))
            .sign(key, hashes.SHA256()))
    der = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()
    enc = crypto.encrypt_token(der, "TOKEN", 1730000000000)
    oaep = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    assert key.decrypt(base64.b64decode(enc), oaep) == b"TOKEN|1730000000000"
    sk = crypto.SessionKey.new()
    info = sk.encryption_info(der)
    assert key.decrypt(base64.b64decode(info["encryptedSymmetricKey"]), oaep) == sk.key
    assert sk.decrypt(sk.encrypt(b"<Faktura/>")) == b"<Faktura/>"
    assert len(sk.encrypt(b"x" * 16)) == 32  # PKCS#7 dokleja pelny blok


def test_qr_link():
    from app.ksef import qr

    url = qr.invoice_url("test", "1111111111", dt.date(2026, 2, 1), b"abc")
    assert url == "https://qr-test.ksef.mf.gov.pl/invoice/1111111111/01-02-2026/ungWv48Bz-pBQUDeXa4iI7ADYaOWF3qctBD_YfIAFa0"
    assert qr.svg(url).startswith("<svg")


class FakeClient:
    sent = []

    def __init__(self, env, nip, token):
        assert token == "tajny-token"
        self.env, self.nip = env, nip

    def send_invoice(self, xml):
        from app.ksef.client import SendResult

        FakeClient.sent.append(xml)
        return SendResult("20270120-SO-1", "20270120-EE-1", 200, "Sukces",
                          ksef_number=f"{self.nip}-20270120-0100001AF629-AF", upo_xml="<UPO/>")

    def invoice_status(self, s, i):
        raise AssertionError

    def upo(self, s, i):
        return "<UPO/>"


def test_send_flow_with_fake_client(client, db, monkeypatch):
    from app.ksef import service
    from app.models import Company

    monkeypatch.setattr(service, "client_factory", FakeClient)
    # ustawienia KSeF w aplikacji: token zapisany zaszyfrowany
    r = client.post("/ustawienia/ksef", data={"ksef_env": "test", "ksef_token": "tajny-token"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    comp = db.query(Company).filter_by(short_name="Margot").one()
    assert comp.ksef_env == "test" and comp.ksef_token_enc and "tajny" not in comp.ksef_token_enc
    assert "tajny-token" not in client.get("/ustawienia").text

    inv = _issue(db, items=[{"name": "Opony", "unit_price": "400.00"}])
    r = client.post(f"/faktury/{inv.id}/ksef", follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert inv.ksef_status == "accepted" and inv.ksef_number.startswith("5551859956-")
    assert inv.ksef_xml.encode() == FakeClient.sent[-1] and inv.ksef_upo == "<UPO/>"
    page = client.get(f"/faktury/{inv.id}").text
    assert inv.ksef_number in page and "qr-test.ksef.mf.gov.pl/invoice/5551859956/" in page
    pdf = client.get(f"/faktury/{inv.id}/pdf")
    assert pdf.status_code == 200
    # drugi raz nie wysyla
    r = client.post(f"/faktury/{inv.id}/ksef", follow_redirects=False)
    db.expire_all()
    assert len(FakeClient.sent) == 1
    assert client.get(f"/faktury/{inv.id}/ksef/upo").text == "<UPO/>"
    assert client.get(f"/faktury/{inv.id}/ksef/xml").content == FakeClient.sent[-1]


def _self_signed(nip="5551859956", ec_key=False, password=b"haslo-do-klucza"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.x509.oid import NameOID, ObjectIdentifier

    key = ec.generate_private_key(ec.SECP256R1()) if ec_key else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jan Testowy"),
                      x509.NameAttribute(ObjectIdentifier("2.5.4.97"), f"VATPL-{nip}")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365)).sign(key, hashes.SHA256()))
    enc = serialization.BestAvailableEncryption(password) if password else serialization.NoEncryption()
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc))


def test_certificate_settings_and_xades_login(client, db):
    import httpx
    from lxml import etree

    from app.ksef import service
    from app.ksef.client import KsefClient
    from app.ksef.xades import KsefCertificate
    from app.models import Company

    crt, key = _self_signed()
    files = {"ksef_cert": ("ksef.crt", crt, "application/x-x509-ca-cert"), "ksef_key": ("ksef.key", key, "application/octet-stream")}
    r = client.post("/ustawienia/ksef", data={"ksef_env": "test", "ksef_auth": "cert", "ksef_key_password": "zle"}, files=files)
    assert "Nie mogę odczytać klucza" in r.text
    _, other_key = _self_signed(password=None)
    r = client.post("/ustawienia/ksef", data={"ksef_env": "test", "ksef_auth": "cert", "ksef_key_password": ""},
                    files={"ksef_cert": ("a.crt", crt, "x"), "ksef_key": ("b.key", other_key, "x")})
    assert "nie pasuje do certyfikatu" in r.text
    r = client.post("/ustawienia/ksef", data={"ksef_env": "test", "ksef_auth": "cert", "ksef_key_password": "haslo-do-klucza"},
                    files=files, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    comp = db.query(Company).filter_by(short_name="Margot").one()
    assert comp.ksef_auth == "cert" and "BEGIN CERTIFICATE" in comp.ksef_cert_pem
    assert comp.ksef_key_enc and "PRIVATE KEY" not in comp.ksef_key_enc
    page = client.get("/ustawienia").text
    assert "ważny do" in page and "PRIVATE" not in page
    assert service.enabled(comp)
    assert isinstance(service._client(comp).token, KsefCertificate)

    # Logowanie XAdES: sprawdz, co trafia do KSeF (podpisany AuthTokenRequest z challenge i NIP).
    seen = {}

    def handler(req: httpx.Request):
        if req.url.path.endswith("/auth/challenge"):
            return httpx.Response(200, json={"challenge": "20270120-CR-ABC", "timestamp": "x", "timestampMs": 1})
        if req.url.path.endswith("/auth/xades-signature"):
            seen["xml"] = req.content
            assert req.headers["content-type"] == "application/xml"
            return httpx.Response(202, json={"referenceNumber": "R1", "authenticationToken": {"token": "AT"}})
        if "/auth/rev2/R1" in req.url.path:
            return httpx.Response(200, json={"status": {"code": 200, "description": "OK"}})
        if req.url.path.endswith("/auth/token/redeem"):
            return httpx.Response(200, json={"accessToken": {"token": "ACCESS"}, "refreshToken": {"token": "R"}})
        raise AssertionError(req.url)

    for ec_key in (False, True):
        c2, k2 = _self_signed(ec_key=ec_key, password=None)
        from app.ksef import xades
        kc, info = xades.load(c2, k2)
        cl = KsefClient("test", "5551859956", kc, http=httpx.Client(base_url="https://x/v2", transport=httpx.MockTransport(handler)),
                        poll_interval=0)
        assert cl.authenticate() == "ACCESS"
        doc = etree.fromstring(seen["xml"])
        ns = {"a": "http://ksef.mf.gov.pl/auth/token/2.0", "ds": "http://www.w3.org/2000/09/xmldsig#"}
        assert doc.findtext("a:Challenge", namespaces=ns) == "20270120-CR-ABC"
        assert doc.findtext("a:ContextIdentifier/a:Nip", namespaces=ns) == "5551859956"
        assert doc.find("ds:Signature", namespaces=ns) is not None
        assert b"SignedProperties" in seen["xml"]
        from signxml import XMLVerifier
        XMLVerifier().verify(seen["xml"], x509_cert=kc.cert_pem, expect_references=3)


def test_bulk_send_and_auto_send(client, db, monkeypatch):
    from app.ksef import service
    from app.models import Contractor

    monkeypatch.setattr(service, "client_factory", FakeClient)
    FakeClient.sent = []
    # bez KSeF: brak ptaszkow na liscie
    plain = _issue(db, items=[{"name": "Opony", "unit_price": "400.00"}])
    assert 'name="ids"' not in client.get("/faktury").text
    client.post("/ustawienia/ksef", data={"ksef_env": "test", "ksef_token": "tajny-token"})
    db.expire_all()

    # kontrahent bez automatu: faktura czeka na ptaszek
    other = _issue(db, items=[{"name": "Felgi", "unit_price": "100.00"}])
    assert other.ksef_status == "" and not FakeClient.sent
    page = client.get("/faktury?ksef=do-wyslania").text
    assert f'name="ids" value="{plain.id}"' in page and f'name="ids" value="{other.id}"' in page
    assert "Wyślij zaznaczone do KSeF" in page
    r = client.post("/faktury/ksef", data={"ids": [str(plain.id), str(other.id)], "back": "/faktury?ksef=do-wyslania"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/faktury?ksef=do-wyslania"
    db.expire_all()
    assert plain.ksef_status == other.ksef_status == "accepted" and len(FakeClient.sent) == 2
    assert f'name="ids" value="{plain.id}"' not in client.get("/faktury").text
    # obca strona powrotu jest ignorowana
    r = client.post("/faktury/ksef", data={"back": "https://zly.pl"}, follow_redirects=False)
    assert r.headers["location"] == "/faktury"

    # kontrahent z automatem: idzie od razu po wystawieniu
    c = Contractor(name="Auto Klient", nip="8672239415", ksef_auto=True)
    db.add(c)
    db.commit()
    auto = _issue(db, buyer={"contractor_id": c.id}, items=[{"name": "Serwis", "unit_price": "50.00"}])
    assert auto.ksef_status == "accepted" and len(FakeClient.sent) == 3
    # i przez formularz kontrahenta da sie to wylaczyc
    client.post(f"/kontrahenci/{c.id}", data={"name": "Auto Klient", "nip": "8672239415", "is_company": "1"})
    db.expire_all()
    assert c.ksef_auto is False


def test_invoice_list_is_limited(client, db, monkeypatch):
    from app import main

    for n in range(3):
        _issue(db, items=[{"name": f"P{n}", "unit_price": "10.00"}])
    monkeypatch.setattr(main, "LIST_LIMIT", 2)
    page = client.get("/faktury").text
    assert "Pokazano 2 najnowszych z 3" in page and "30,00 zł" in page
    assert "Pokazano" not in client.get("/faktury?wszystkie=1").text
