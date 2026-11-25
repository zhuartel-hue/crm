"""Konektor Claude: OAuth (rejestracja, zgoda, PKCE, odswiezanie) i narzedzia MCP."""
import base64
import hashlib
from urllib.parse import parse_qs, urlparse
import pytest

REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def pkce():
    verifier = "v" * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def connect(client, scopes=("read", "write")):
    """Pelny przeplyw OAuth jak w Claude; zwraca odpowiedz z /oauth/token."""
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"]
    reg = client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT]})
    assert reg.status_code == 201
    cid = reg.json()["client_id"]
    verifier, challenge = pkce()
    q = dict(response_type="code", client_id=cid, redirect_uri=REDIRECT, code_challenge=challenge,
             code_challenge_method="S256", state="xyz", scope="read write issue")
    page = client.get("/oauth/authorize", params=q)
    assert page.status_code == 200 and "Połączyć „Claude”" in page.text
    r = client.post("/oauth/authorize", data={**{k: q[k] for k in ("client_id", "redirect_uri", "state", "code_challenge")},
                                              "scope": list(scopes), "decision": "allow"}, follow_redirects=False)
    loc = urlparse(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == REDIRECT
    params = parse_qs(loc.query)
    assert params["state"] == ["xyz"]
    tok = client.post("/oauth/token", data=dict(grant_type="authorization_code", client_id=cid, code=params["code"][0],
                                                redirect_uri=REDIRECT, code_verifier=verifier))
    assert tok.status_code == 200, tok.text
    return cid, tok.json()


def rpc(client, token, method, params=None, id_=1):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def call(client, token, name, **args):
    return rpc(client, token, "tools/call", {"name": name, "arguments": args})


def test_mcp_requires_token(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]
    meta = client.get("/.well-known/oauth-protected-resource").json()
    assert meta["resource"].endswith("/mcp")


def test_register_rejects_foreign_redirect(client):
    r = client.post("/oauth/register", json={"client_name": "x", "redirect_uris": ["https://evil.example/cb"]})
    assert r.status_code == 400


def test_bad_pkce_and_code_reuse(client):
    reg = client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT]}).json()
    verifier, challenge = pkce()
    data = dict(client_id=reg["client_id"], redirect_uri=REDIRECT, state="", code_challenge=challenge,
                scope=["read"], decision="allow")
    loc = client.post("/oauth/authorize", data=data, follow_redirects=False).headers["location"]
    code = parse_qs(urlparse(loc).query)["code"][0]
    base = dict(grant_type="authorization_code", client_id=reg["client_id"], code=code, redirect_uri=REDIRECT)
    assert client.post("/oauth/token", data={**base, "code_verifier": "zly"}).status_code == 400
    # Kod jest jednorazowy, nawet po nieudanej probie.
    assert client.post("/oauth/token", data={**base, "code_verifier": verifier}).status_code == 400


def test_refresh_rotation_and_reuse_detection(client):
    cid, tok = connect(client)
    r1 = client.post("/oauth/token", data=dict(grant_type="refresh_token", client_id=cid, refresh_token=tok["refresh_token"]))
    assert r1.status_code == 200
    new = r1.json()
    assert rpc(client, new["access_token"], "ping") == {}
    # Ponowne uzycie starego refresh tokenu odcina cala zgode.
    r2 = client.post("/oauth/token", data=dict(grant_type="refresh_token", client_id=cid, refresh_token=tok["refresh_token"]))
    assert r2.status_code == 400
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Authorization": f"Bearer {new['access_token']}"})
    assert r.status_code == 401


def test_sell_car_flow_via_mcp(client):
    _, tok = connect(client, scopes=("read", "write", "issue"))
    at = tok["access_token"]
    init = rpc(client, at, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "claude", "version": "1"}})
    assert init["protocolVersion"] == "2025-06-18" and "tools" in init["capabilities"]
    names = {t["name"] for t in rpc(client, at, "tools/list")["tools"]}
    assert {"create_invoice_draft", "issue_invoice", "search_cars"} <= names

    car = call(client, at, "create_car", company="margot", make="Ford", model="Transit", vin="WF0XXXTTGXKA12345",
               year=2019, mileage_km=184000, purchase_price="30000", asking_price="36900")
    assert not car["isError"], car
    car_id = car["structuredContent"]["car"]["id"]
    found = call(client, at, "search_cars", company="Margot", query="ford transit")["structuredContent"]["cars"]
    assert [c["id"] for c in found] == [car_id]

    draft = call(client, at, "create_invoice_draft", company="Margot", car_id=car_id,
                 buyer={"name": "Jan Kowalski", "address": "ul. Prosta 1\n00-001 Warszawa", "save_as_contractor": True})
    assert not draft["isError"], draft
    inv = draft["structuredContent"]["invoice"]
    assert inv["status"] == "draft" and inv["number"] is None
    assert inv["total_gross"] == "36900.00" and inv["total_net"] == "30000.00" and inv["total_vat"] == "6900.00"
    assert "VIN: WF0XXXTTGXKA12345" in inv["items"][0]["name"]
    assert inv["pdf_url"].endswith(f"/faktury/{inv['id']}/pdf")

    issued = call(client, at, "issue_invoice", invoice_id=inv["id"])["structuredContent"]["invoice"]
    assert issued["number"] == f"FV/1/{issued['issue_date'][5:7]}/{issued['issue_date'][:4]}"
    again = call(client, at, "create_invoice_draft", company="Margot", car_id=car_id, buyer={"name": "X"})
    assert again["isError"] and "sprzedane" in again["content"][0]["text"]

    paid = call(client, at, "mark_invoice_paid", invoice_id=inv["id"])["structuredContent"]["invoice"]
    assert paid["to_pay"] == "0.00"
    summary = call(client, at, "sales_summary", company="margot", month=issued["issue_date"][:7])["structuredContent"]
    assert summary["gross"] == "36900.00"
    assert summary["cars_sold"][0]["profit"] == "0.00"

    log = client.get("/dziennik").text
    assert "Claude (Claude)" in log and "Wystawiono FV/1/" in log


def test_scopes_limit_tools(client):
    _, tok = connect(client, scopes=("read", "write"))
    at = tok["access_token"]
    names = {t["name"] for t in rpc(client, at, "tools/list")["tools"]}
    assert "issue_invoice" not in names
    res = call(client, at, "issue_invoice", invoice_id=1)
    assert res["isError"] and "issue" in res["content"][0]["text"]


def test_company_must_be_known(client):
    _, tok = connect(client)
    res = call(client, tok["access_token"], "search_cars", company="nieznana")
    assert res["isError"] and "Margot" in res["content"][0]["text"]


def test_api_key_and_revoke(client):
    r = client.post("/dostep/klucz", data={"name": "test", "scope": ["read"]}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/dostep").text
    key = page.split("<code style=\"word-break:break-all\">")[1].split("</code>")[0]
    h = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/v1/companies", headers=h).json()["companies"][0]["short_name"] == "Margot"
    assert client.post("/api/v1/invoices", json={}, headers=h).status_code == 403
    assert client.get("/api/v1/companies").status_code == 401
    grant = page.split('action="/dostep/')[1].split("/cofnij")[0]
    client.post(f"/dostep/{grant}/cofnij")
    assert client.get("/api/v1/companies", headers=h).status_code == 401


def test_login_returns_to_authorize():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anon:
        reg = anon.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT]}).json()
        _, challenge = pkce()
        q = dict(response_type="code", client_id=reg["client_id"], redirect_uri=REDIRECT, code_challenge=challenge,
                 code_challenge_method="S256", state="s")
        r = anon.get("/oauth/authorize", params=q, follow_redirects=False)
        assert r.headers["location"] == "/login"
        r = anon.post("/login", data={"login": "admin", "password": "test-haslo"}, follow_redirects=False)
        assert r.headers["location"].startswith("/oauth/authorize?")


def test_kinds_via_mcp(client):
    _, tok = connect(client, scopes=("read", "write", "issue"))
    at = tok["access_token"]
    sc = lambda r: (r.get("isError") and pytest.fail(r["content"][0]["text"])) or r["structuredContent"]
    buyer = {"name": "Firma Test", "nip": "5260250995", "address": "ul. A 1\n00-001 Warszawa"}
    item = {"name": "Usługa", "unit_price": "1230.00"}

    pro = sc(call(client, at, "create_invoice_draft", company="Margot", kind="proforma", buyer=buyer, items=[item]))
    pro = sc(call(client, at, "issue_invoice", invoice_id=pro["invoice"]["id"]))["invoice"]
    assert pro["number"].startswith("PRO/")
    vat = sc(call(client, at, "create_invoice_from_proforma", proforma_id=pro["id"]))["invoice"]
    assert vat["kind"] == "vat" and vat["total_gross"] == "1230.00"
    vat = sc(call(client, at, "issue_invoice", invoice_id=vat["id"]))["invoice"]

    no_reason = call(client, at, "create_correction_draft", invoice_id=vat["id"], reason="")
    assert no_reason["isError"] is False  # szkic bez powodu wolno zapisać, ale nie wystawić
    bad = call(client, at, "issue_invoice", invoice_id=no_reason["structuredContent"]["invoice"]["id"])
    assert bad["isError"]
    kor = sc(call(client, at, "create_correction_draft", invoice_id=vat["id"], reason="Rabat",
                  items=[{"name": "Usługa", "unit_price": "615.00"}]))["invoice"]
    assert kor["kind"] == "correction" and kor["total_gross"] == "-615.00"

    adv = sc(call(client, at, "create_invoice_draft", company="Margot", kind="advance", advance_gross="1000",
                  buyer=buyer, items=[{"name": "Auto", "unit_price": "12300.00"}]))["invoice"]
    adv = sc(call(client, at, "issue_invoice", invoice_id=adv["id"]))["invoice"]
    fin = sc(call(client, at, "create_final_invoice_draft", advance_id=adv["id"]))["invoice"]
    assert fin["kind"] == "final" and fin["total_gross"] == "11300.00"
    listed = sc(call(client, at, "list_invoices", company="Margot", kind="advance"))["invoices"]
    assert [i["id"] for i in listed] == [adv["id"]]
