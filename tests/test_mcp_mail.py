"""Poczta przez konektor Claude: skrzynki firmy, wyszukiwanie, odczyt, wysylka (uprawnienie issue)."""
import datetime as dt

from tests.test_mcp import call, connect


def _setup(db):
    from app import secretbox
    from app.models import Company, MailAccount, MailMessage

    c = db.query(Company).order_by(Company.id).first()
    acc = MailAccount(company_id=c.id, email="biuro@firma.pl", display_name="Biuro", imap_host="imap.x", smtp_host="smtp.x",
                      username="biuro@firma.pl", password_enc=secretbox.seal("h"), sync_state="{}", last_error="")
    db.add(acc)
    db.flush()
    db.add(MailMessage(account_id=acc.id, direction="in", subject="Zapytanie o ofertę", from_addr="klient@abc.pl",
                       from_name="Klient", to_addrs="biuro@firma.pl", date=dt.datetime(2026, 11, 20, 10, 0),
                       body_text="Dzień dobry, proszę o ofertę na kamery."))
    db.commit()
    return c.short_name


def test_mail_tools_read_and_send(client, monkeypatch):
    from app.db import SessionLocal
    from app.mail import service as mail

    with SessionLocal() as db:
        firm = _setup(db)
    _, tok = connect(client, scopes=("read", "write"))
    t = tok["access_token"]
    boxes = call(client, t, "list_mailboxes", company=firm)["structuredContent"]["mailboxes"]
    assert boxes[0]["email"] == "biuro@firma.pl"
    found = call(client, t, "search_mails", company=firm, query="ofert")["structuredContent"]["mails"]
    assert found and found[0]["subject"] == "Zapytanie o ofertę" and "kamery" in found[0]["snippet"]
    one = call(client, t, "get_mail", mail_id=found[0]["id"])["structuredContent"]["mail"]
    assert "proszę o ofertę" in one["body"]
    # bez uprawnienia issue wysylka jest zablokowana
    r = call(client, t, "send_email", company=firm, to="klient@abc.pl", subject="Oferta", body="W załączeniu")
    assert r["isError"]
    sent = []

    class FakeSMTP:
        def send_message(self, msg):
            sent.append(msg)

        def quit(self):
            pass

    monkeypatch.setattr(mail, "connect_smtp", lambda acc: FakeSMTP())
    monkeypatch.setattr(mail, "connect_imap", lambda acc: (_ for _ in ()).throw(OSError("brak")))
    _, tok2 = connect(client, scopes=("read", "write", "issue"))
    r = call(client, tok2["access_token"], "send_email", company=firm, to="klient@abc.pl", subject="Oferta",
             body="W załączeniu oferta.")
    assert not r["isError"], r
    assert sent and sent[0]["To"] == "klient@abc.pl" and "biuro@firma.pl" in sent[0]["From"]
    names = [m["subject"] for m in call(client, t, "search_mails", company=firm)["structuredContent"]["mails"]]
    assert "Oferta" in names  # wyslany mail jest w historii
