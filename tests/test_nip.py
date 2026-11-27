"""Dane firmy po NIP z bialej listy VAT (MF), z podmienionym HTTP."""
import httpx


def _fake(status=200, payload=None):
    calls = []

    def get(url):
        calls.append(url)
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))
    get.calls = calls
    return get


def test_lookup_fills_contractor(client, monkeypatch):
    from app import nip_lookup
    nip_lookup._CACHE.clear()
    fake = _fake(payload={"result": {"subject": {
        "name": "ARI ERDMANN WORLDS SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ", "nip": "5552144076",
        "statusVat": "Czynny", "regon": "123456785", "krs": "0001179031",
        "workingAddress": "UL. PROCHOWA 5, 89-600 CHOJNICE", "accountNumbers": ["12102011560000000000000001"]}}})
    monkeypatch.setattr(nip_lookup, "http_get", fake)
    r = client.get("/nip/555-214-40-76")
    assert r.status_code == 200
    d = r.json()
    assert d["street"] == "UL. PROCHOWA 5" and d["postal_code"] == "89-600" and d["city"] == "CHOJNICE"
    assert d["status_vat"] == "Czynny" and d["accounts"] == ["12102011560000000000000001"]
    assert "/api/search/nip/5552144076?date=" in fake.calls[0]
    client.get("/nip/5552144076")
    assert len(fake.calls) == 1  # z pamieci
    assert "Pobierz dane po NIP" in client.get("/kontrahenci/nowy").text
    assert "Pobierz po NIP" in client.get("/faktury/nowa").text


def test_lookup_errors(client, monkeypatch):
    from app import nip_lookup
    nip_lookup._CACHE.clear()
    assert client.get("/nip/1234567890").status_code == 422  # zla suma kontrolna
    monkeypatch.setattr(nip_lookup, "http_get", _fake(payload={"result": {"subject": None}}))
    r = client.get("/nip/5551859956")
    assert r.status_code == 422 and "Nie ma tego NIP-u" in r.json()["error"]
    monkeypatch.setattr(nip_lookup, "http_get", _fake(400, {"code": "WL-113", "message": "Limit zapytań"}))
    assert "Limit zapytań" in client.get("/nip/5551859956").json()["error"]


def test_nip_requires_login():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as anon:
        assert anon.get("/nip/5551859956", follow_redirects=False).status_code == 303
