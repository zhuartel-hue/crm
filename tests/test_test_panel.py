"""Panel testowy: zadnego kontaktu ze swiatem (maile, KSeF, bank, zadania w tle)."""
import pytest

from app import config


@pytest.fixture
def test_panel(monkeypatch):
    monkeypatch.setattr(config, "TEST_PANEL", True)


def test_mail_blocked(test_panel):
    from app.mail import service
    from app.models import MailAccount

    acc = MailAccount(email="a@b.pl", smtp_host="smtp.example.com", imap_host="imap.example.com")
    for fn in (service.connect_smtp, service.connect_imap):
        with pytest.raises(service.MailError, match="Panel testowy"):
            fn(acc)


def test_bank_blocked(test_panel):
    from app.bank.enablebanking import BankApiError, Client

    with pytest.raises(BankApiError, match="Panel testowy"):
        Client("app", "pem")


def test_ksef_blocked(test_panel):
    from app.ksef import service
    from app.models import Company
    from app.services import ServiceError

    with pytest.raises(ServiceError, match="Panel testowy"):
        service._client(Company(short_name="X", nip="5260250995"))


def test_health_and_banner(client, monkeypatch):
    from app import main

    monkeypatch.setattr(config, "APP_VERSION", "abc1234")
    assert client.get("/health").json() == {"ok": True, "version": "abc1234", "test": False}
    assert "PANEL TESTOWY" not in client.get("/").text
    monkeypatch.setitem(main.templates.env.globals, "TEST_PANEL", True)
    assert "PANEL TESTOWY" in client.get("/").text


def test_system_status_tool(client, tmp_path, monkeypatch):
    from app import system_status
    from tests.test_mcp import call, connect

    (tmp_path / "produkcja.json").write_text('{"stan":"dziala","wersja":"abc1234"}')
    (tmp_path / "wydania.log").write_text("2026-11-27 12:00 produkcja: abc1234 dziala\n")
    monkeypatch.setattr(system_status, "STAN_DIR", tmp_path)
    _, tok = connect(client, scopes=("read",))
    res = call(client, tok["access_token"], "system_status")
    assert not res["isError"], res
    st = res["structuredContent"]
    assert st["production"]["wersja"] == "abc1234" and st["test_panel"] is None
    assert st["deploy_log"][-1].endswith("abc1234 dziala")
