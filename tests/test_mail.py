"""Poczta: skrzynki, pobieranie (IMAP), wysylka (SMTP), powiazanie z kontrahentem i faktura."""
from email.message import EmailMessage

import pytest

from test_app import _new_invoice


def _raw(uid, frm, to, subject, body, html=None, attach=None, msgid=None):
    m = EmailMessage()
    m["From"] = frm
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = "Mon, 18 Jan 2027 10:0%d:00 +0100" % (uid % 10)
    m["Message-ID"] = msgid or f"<m{uid}@test.pl>"
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    if attach:
        m.add_attachment(attach[1], maintype="application", subtype="pdf", filename=attach[0])
    return m.as_bytes()


class FakeImap:
    folders = {}
    appended = []

    def __init__(self, acc):
        self.cur = None

    def list(self):
        return "OK", [b'(\\HasNoChildren) "." "INBOX"', b'(\\HasNoChildren \\Sent) "." "INBOX.Sent"']

    def select(self, name, readonly=False):
        self.cur = name.strip('"')
        return "OK", [str(len(self.folders.get(self.cur, {}))).encode()]

    def response(self, code):
        return code, [b"777"]

    def uid(self, cmd, *args):
        msgs = self.folders.get(self.cur, {})
        if cmd == "SEARCH":
            crit = args[-1]
            if crit.startswith("UID "):
                lo = int(crit[4:].split(":")[0])
                ids = [u for u in msgs if u >= lo] or ([max(msgs)] if msgs else [])
            else:
                ids = list(msgs)
            return "OK", [" ".join(map(str, sorted(ids))).encode()]
        if cmd == "FETCH":
            u = int(args[0])
            return "OK", [(f"{u} (UID {u} FLAGS (\\Seen) BODY[] {{1}}".encode(), msgs[u]), b")"]
        raise AssertionError(cmd)

    def append(self, folder, flags, date, raw):
        self.appended.append((folder, raw))
        return "OK", []

    def logout(self):
        pass


class FakeSmtp:
    sent = []

    def __init__(self, acc):
        pass

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        pass


@pytest.fixture()
def fake_mail(monkeypatch):
    from app.mail import service

    FakeImap.folders = {"INBOX": {}, "INBOX.Sent": {}}
    FakeImap.appended = []
    FakeSmtp.sent = []
    monkeypatch.setattr(service, "imap_factory", FakeImap)
    monkeypatch.setattr(service, "smtp_factory", FakeSmtp)
    return FakeImap


def _account(client, **kw):
    data = {"email": "biuro@margot.test", "display_name": "Margot", "username": "", "password": "tajne-haslo-123",
            "imap_host": "imap.test", "imap_port": "993", "smtp_host": "smtp.test", "smtp_port": "465",
            "smtp_security": "ssl", "active": "1", "signature": "Arkadiusz Erdmann"}
    data.update(kw)
    r = client.post("/ustawienia/poczta", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    page = client.get("/ustawienia/poczta").text
    assert "biuro@margot.test" in page and "tajne-haslo-123" not in page
    from app.db import SessionLocal
    from app.models import MailAccount

    with SessionLocal() as db:
        acc = db.query(MailAccount).one()
        assert acc.password_enc and "tajne" not in acc.password_enc
        return acc.id


def test_sync_links_contractor_and_invoice(client, fake_mail):
    inv_id = _new_invoice(client, "vat", buyer_name="Jan Kowalski")  # FV/1/01/2027, kontrahent zapisany
    from app.db import SessionLocal
    from app.models import Contractor, Invoice

    with SessionLocal() as db:
        inv = db.get(Invoice, inv_id)
        c = db.get(Contractor, inv.contractor_id)
        c.email = "jan@kowalski.test, biuro@kowalski.test"
        db.commit()
        cid = c.id
    acc_id = _account(client)
    fake_mail.folders["INBOX"] = {
        5: _raw(5, "Jan Kowalski <Biuro@Kowalski.test>", "biuro@margot.test", "Pytanie o FV/1/01/2027", "Dzień dobry,\nkiedy auto?"),
        6: _raw(6, "Sklep <news@sklep.test>", "biuro@margot.test", "Promocja", "", html="<p>Super <b>oferta</b></p><script>x()</script>",
                attach=("cennik.pdf", b"%PDF-1.4 test")),
    }
    fake_mail.folders["INBOX.Sent"] = {3: _raw(3, "biuro@margot.test", "jan@kowalski.test", "Oferta", "W załączniku")}
    r = client.post("/poczta/odswiez")
    assert "Nowych wiadomości: 3" in r.text
    page = client.get("/poczta").text
    assert "Pytanie o FV/1/01/2027" in page and "Promocja" in page and "Jan Kowalski" in page

    from app.models import MailMessage
    with SessionLocal() as db:
        msgs = {m.subject: m for m in db.query(MailMessage)}
        q = msgs["Pytanie o FV/1/01/2027"]
        assert q.contractor_id == cid and q.invoice_id == inv_id and q.direction == "in"
        promo = msgs["Promocja"]
        assert promo.contractor_id is None and "Super oferta" in promo.body_text and "x()" not in promo.body_text
        assert promo.attachments[0].filename == "cennik.pdf"
        assert msgs["Oferta"].direction == "out" and msgs["Oferta"].contractor_id == cid
        qid, pid, att = q.id, promo.id, promo.attachments[0].id

    # Ponowne pobranie nie dubluje, nowy UID dochodzi
    fake_mail.folders["INBOX"][7] = _raw(7, "jan@kowalski.test", "biuro@margot.test", "Re: oferta", "ok")
    assert "Nowych wiadomości: 1" in client.post("/poczta/odswiez").text

    view = client.get(f"/poczta/{qid}").text
    assert "kiedy auto?" in view and "Odpowiedz" in view
    h = client.get(f"/poczta/{pid}/html")
    assert "default-src 'none'" in h.headers["content-security-policy"] and h.headers["x-frame-options"] == "SAMEORIGIN"
    a = client.get(f"/poczta/zalacznik/{att}")
    assert a.content == b"%PDF-1.4 test" and "attachment" in a.headers["content-disposition"]
    assert "Pytanie o FV/1/01/2027" in client.get(f"/kontrahenci/{cid}").text
    assert "Korespondencja" in client.get(f"/faktury/{inv_id}").text

    # Odpowiedz
    form = client.get(f"/poczta/nowa?odpowiedz={qid}").text
    assert "Re: Pytanie o FV/1/01/2027" in form and "biuro@kowalski.test" in form
    r = client.post("/poczta/nowa", data={"account_id": str(acc_id), "to": "biuro@kowalski.test", "cc": "",
                                          "subject": "Re: Pytanie", "body": "Jutro.", "in_reply_to": "<m5@test.pl>",
                                          "invoice_id": ""}, follow_redirects=False)
    assert r.status_code == 303
    sent = FakeSmtp.sent[-1]
    assert sent["In-Reply-To"] == "<m5@test.pl>" and "Arkadiusz Erdmann" in sent.get_content()
    assert FakeImap.appended and FakeImap.appended[-1][0] == '"INBOX.Sent"'


def test_send_invoice_by_email(client, fake_mail):
    inv_id = _new_invoice(client, "vat")
    acc_id = _account(client)
    form = client.get(f"/poczta/nowa?faktura={inv_id}").text
    assert "FV/1/01/2027" in form and "Załącz PDF" in form
    r = client.post("/poczta/nowa", data={"account_id": str(acc_id), "to": "klient@test.pl", "cc": "",
                                          "subject": "Faktura FV/1/01/2027", "body": "W załączniku faktura.",
                                          "in_reply_to": "", "invoice_id": str(inv_id), "attach_invoice": "1"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/faktury/{inv_id}"
    msg = FakeSmtp.sent[-1]
    att = [p for p in msg.iter_attachments()]
    assert att[0].get_filename() == "FV-1-01-2027.pdf" and att[0].get_content()[:4] == b"%PDF"
    page = client.get(f"/faktury/{inv_id}").text
    assert "Korespondencja" in page and "Faktura FV/1/01/2027" in page
    # zly adres
    r = client.post("/poczta/nowa", data={"account_id": str(acc_id), "to": "to-nie-adres", "subject": "x", "body": "",
                                          "invoice_id": "", "in_reply_to": "", "cc": ""})
    assert "Niepoprawny adres" in r.text


def test_mail_requires_own_company(client, fake_mail):
    _account(client)
    client.post("/firma/2")
    assert "Nie ma jeszcze skrzynki" in client.get("/poczta").text


def test_mail_has_company_logo(client, fake_mail):
    import io

    from PIL import Image

    from app.db import SessionLocal
    from app.models import Company
    buf = io.BytesIO()
    Image.new("RGB", (120, 40), (10, 143, 220)).save(buf, "PNG")
    with SessionLocal() as db:
        c = db.get(Company, 1)
        c.logo, c.logo_type = buf.getvalue(), "image/png"
        db.commit()
    acc_id = _account(client)
    client.post("/poczta/nowa", data={"account_id": str(acc_id), "to": "klient@test.pl", "cc": "", "subject": "Oferta",
                                      "body": "Dzień dobry,\nw załączniku <oferta>.", "in_reply_to": "", "invoice_id": ""})
    msg = FakeSmtp.sent[-1]
    html_part = msg.get_body(("html",))
    assert "&lt;oferta&gt;" in html_part.get_content() and "cid:" in html_part.get_content()
    assert any(p.get_content_type() == "image/png" for p in msg.walk())
    assert "w załączniku <oferta>" in msg.get_body(("plain",)).get_content()
