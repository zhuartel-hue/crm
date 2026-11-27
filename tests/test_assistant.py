"""Asystent AI (okienko Pomoc): baza wiedzy, zrzuty, kontekst bez danych osobowych, limity, oddzielenie organizacji."""
import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    from app import assistant

    (tmp_path / "moduly").mkdir()
    (tmp_path / "faq").mkdir()
    (tmp_path / "zrzuty").mkdir()
    (tmp_path / "moduly" / "faktury.md").write_text("# Faktury\nKliknij **Nowa faktura**.\n", encoding="utf-8")
    (tmp_path / "faq" / "serwer.md").write_text("# Serwer\nDla klientów: nie\nTajna wiedza o serwerze.\n", encoding="utf-8")
    (tmp_path / "zrzuty" / "faktura-nowa.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "zrzuty" / "zrzuty.json").write_text(json.dumps({"screens": [
        {"id": "faktura-nowa", "title": "Lista faktur", "page": "/faktury", "image": "faktura-nowa.png",
         "steps": [{"css": "a.btn", "text": "Nowa faktura", "label": "Nowa faktura"}]},
        {"id": "bez-pliku", "title": "Brak", "page": "/", "image": "brak.png", "steps": []},
    ]}), encoding="utf-8")
    monkeypatch.setattr(assistant, "KNOWLEDGE_DIR", tmp_path)
    monkeypatch.setattr(assistant, "SCREENS_DIR", tmp_path / "zrzuty")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    return tmp_path


def _enable(org_name=None):
    from app import features
    from app.db import SessionLocal
    from app.models import Organisation

    with SessionLocal() as db:
        for org in db.scalars(sa.select(Organisation)).all():
            if org_name is None or org.name == org_name:
                features.set_enabled(db, org, "asystent_ai", True)
        db.commit()


def _fake(calls, text="Kliknij **Nowa faktura**.\n[[zrzut:faktura-nowa]]\n[[zrzut:nie-ma]]"):
    def call(cfg, system, messages):
        calls.append({"cfg": cfg, "system": system, "messages": messages})
        return SimpleNamespace(
            stop_reason="end_turn", model=cfg["model"],
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=100, output_tokens=200, cache_read_input_tokens=10000,
                                  cache_creation_input_tokens=0))
    return call


def test_off_by_default(client, kb):
    assert client.post("/pomoc/pytanie", json={"question": "jak?"}).status_code == 404
    assert 'id="pz"' not in client.get("/").text


def test_answer_with_screenshot_and_log(client, kb, monkeypatch):
    from app import assistant
    from app.db import SessionLocal
    from app.models import AssistantMessage

    _enable()
    assert 'id="pz"' in client.get("/").text
    calls = []
    monkeypatch.setattr(assistant, "call_model", _fake(calls))
    r = client.post("/pomoc/pytanie", json={"question": "Jak wystawić fakturę?", "page": "/faktury",
                                            "history": [{"role": "user", "text": "hej"}, {"role": "assistant", "text": "Cześć"}]})
    j = r.json()
    assert j["ok"] and j["id"]
    assert '<img src="/pomoc/zrzut/faktura-nowa.png"' in j["html"] and "nie-ma" not in j["html"]
    assert j["screens"]["faktura-nowa"]["page"] == "/faktury"
    sent = calls[0]
    # baza wiedzy w stalym prefiksie, bez plikow wewnetrznych
    assert "Kliknij **Nowa faktura**" in sent["system"] and "Tajna wiedza" not in sent["system"]
    assert "faktura-nowa: Lista faktur" in sent["system"] and "bez-pliku" not in sent["system"]
    # historia + kontekst firmy bez nazwy i NIP
    assert [m["role"] for m in sent["messages"]] == ["user", "assistant", "user"]
    last = sent["messages"][-1]["content"]
    assert "<kontekst>" in last and "strona: /faktury" in last
    with SessionLocal() as db:
        from app.models import Company
        for c in db.scalars(sa.select(Company)).all():
            assert c.nip not in last and c.name not in last
        row = db.get(AssistantMessage, j["id"])
        assert row.question == "Jak wystawić fakturę?" and row.output_tokens == 200
        assert float(row.cost_usd) == pytest.approx((100 * 4 + 200 * 20 + 10000 * 0.2) / 1e6)
    assert client.post(f"/pomoc/ocena/{j['id']}", json={"rating": -1}).json()["ok"]
    page = client.get("/pomoc/historia").text
    assert "Jak wystawić fakturę?" in page and "nie pomogło" in page


def test_no_key_and_daily_limit(client, kb, monkeypatch):
    from app import assistant

    _enable()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert client.post("/pomoc/pytanie", json={"question": "x"}).json()["unavailable"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AI_DAILY_LIMIT", "1")
    monkeypatch.setattr(assistant, "call_model", _fake([]))
    assert client.post("/pomoc/pytanie", json={"question": "a"}).json().get("id")
    assert client.post("/pomoc/pytanie", json={"question": "b"}).json()["limited"]


def test_other_organisation_cannot_see_or_rate(client, kb, monkeypatch):
    from app import assistant
    from app.main import app
    from tests.test_users import _login, _second_org
    from app.db import SessionLocal

    _enable()
    monkeypatch.setattr(assistant, "call_model", _fake([]))
    mid = client.post("/pomoc/pytanie", json={"question": "Pytanie pierwszej firmy"}).json()["id"]
    with SessionLocal() as db:
        _second_org(db)
    c2 = TestClient(app)
    assert _login(c2, "obcy", "obce-haslo-123").status_code == 303
    # druga organizacja nie ma wlaczonej funkcji
    assert c2.post("/pomoc/pytanie", json={"question": "x"}).status_code == 404
    assert c2.post(f"/pomoc/ocena/{mid}", json={"rating": 1}).status_code == 404
    assert "Pytanie pierwszej firmy" not in c2.get("/pomoc/historia").text


def test_render_is_safe():
    from app.assistant import render_answer

    html, used = render_answer(
        "<script>x</script> **ok** [zła](https://evil.example) [dobra](/faktury)\n1. jeden\n2. dwa\n[[zrzut:abc]]",
        {"abc": {"image": "abc.png", "title": "T"}})
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<a href="/faktury">dobra</a>' in html and "evil.example\"" not in html and 'href="https' not in html
    assert "<ol><li>jeden</li><li>dwa" in html and used == ["abc"]
    assert html.index("pz-fig") < html.index("</ol>")  # zrzut pod ostatnim krokiem


def test_screenshot_route(client, kb):
    _enable()
    assert client.get("/pomoc/zrzut/faktura-nowa.png").status_code == 200
    assert client.get("/pomoc/zrzut/..%2Fsekret.png").status_code == 404
    assert client.get("/pomoc/zrzut/brak.png").status_code == 404


def test_real_knowledge_base_loads():
    """Prawdziwa baza wiedzy z repozytorium: laduje sie, pomija strony wewnetrzne, zrzuty istnieja."""
    from app import assistant

    files = [p.name for p in assistant.knowledge_files()]
    assert files, "brak stron w docs/wiedza"
    text = assistant.system_text()
    assert "Baza wiedzy Sygnavo" in text
    for s in assistant.load_screens():
        assert (assistant.SCREENS_DIR / s["image"]).exists() and s["page"].startswith("/")
