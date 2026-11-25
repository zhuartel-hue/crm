"""Dostep z zewnatrz: OAuth 2.1 dla konektora Claude (MCP) i stale klucze API.

Przeplyw dla Claude: Claude rejestruje sie (/oauth/register), wysyla Cie na /oauth/authorize,
logujesz sie do programu i zatwierdzasz uprawnienia, Claude wymienia kod na token (/oauth/token, PKCE).
W bazie trzymamy tylko skroty SHA-256 tokenow.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import secrets
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import SCOPES, ApiToken, AuthCode, OAuthClient

ACCESS_TTL = dt.timedelta(hours=1)
REFRESH_TTL = dt.timedelta(days=60)
CODE_TTL = dt.timedelta(minutes=5)

router = APIRouter()


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def base_url(request: Request) -> str:
    if config.PUBLIC_URL:
        return config.PUBLIC_URL.rstrip("/")
    return str(request.base_url).rstrip("/")


def normalize_scopes(requested: str | None, allowed=None) -> list[str]:
    allowed = allowed or list(SCOPES)
    scopes = [s for s in (requested or "").split() if s in SCOPES and s in allowed]
    return scopes or ["read"]


# --- tokeny -------------------------------------------------------------------


def issue_tokens(db: Session, client: OAuthClient, scopes: list[str], grant_id: str | None = None) -> dict:
    grant_id = grant_id or secrets.token_hex(8)
    access = "crm_at_" + secrets.token_urlsafe(32)
    refresh = "crm_rt_" + secrets.token_urlsafe(32)
    scope_str = " ".join(scopes)
    db.add(ApiToken(kind="access", token_hash=sha(access), name=client.name, client_id=client.client_id,
                    grant_id=grant_id, scopes=scope_str, expires_at=now() + ACCESS_TTL))
    db.add(ApiToken(kind="refresh", token_hash=sha(refresh), name=client.name, client_id=client.client_id,
                    grant_id=grant_id, scopes=scope_str, expires_at=now() + REFRESH_TTL))
    db.commit()
    return {"access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()),
            "refresh_token": refresh, "scope": scope_str}


def create_api_key(db: Session, name: str, scopes: list[str]) -> str:
    key = "crm_key_" + secrets.token_urlsafe(32)
    db.add(ApiToken(kind="key", token_hash=sha(key), name=name or "Klucz API", grant_id=secrets.token_hex(8),
                    scopes=" ".join(scopes)))
    db.commit()
    return key


def revoke_grant(db: Session, grant_id: str) -> None:
    for t in db.scalars(select(ApiToken).where(ApiToken.grant_id == grant_id)):
        t.revoked = True
    db.commit()


class Principal:
    def __init__(self, token: ApiToken):
        self.token = token
        self.scopes = set(token.scope_list)
        who = token.name or "API"
        self.actor = f"Claude ({who})" if token.kind != "key" else f"API ({who})"

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise PermissionError(
                f"Brak uprawnienia „{scope}” ({SCOPES[scope]}). Właściciel może je nadać, łącząc konektor ponownie."
            )


def authenticate(db: Session, request: Request) -> Principal | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == sha(token)))
    if row is None or row.revoked or row.kind == "refresh":
        return None
    if row.expires_at is not None and row.expires_at < now():
        return None
    if row.last_used_at is None or now() - row.last_used_at > dt.timedelta(minutes=5):
        row.last_used_at = now()
        db.commit()
    return Principal(row)


def unauthorized(request: Request) -> JSONResponse:
    meta = f"{base_url(request)}/.well-known/oauth-protected-resource"
    return JSONResponse(
        {"error": "invalid_token", "error_description": "Zaloguj konektor ponownie."},
        status_code=401,
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{meta}"'},
    )


# --- metadane OAuth --------------------------------------------------------------


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource(request: Request):
    b = base_url(request)
    return {"resource": f"{b}/mcp", "authorization_servers": [b], "scopes_supported": list(SCOPES),
            "bearer_methods_supported": ["header"], "resource_name": "Faktury"}


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/openid-configuration")
def authorization_server(request: Request):
    b = base_url(request)
    return {
        "issuer": b,
        "authorization_endpoint": f"{b}/oauth/authorize",
        "token_endpoint": f"{b}/oauth/token",
        "registration_endpoint": f"{b}/oauth/register",
        "revocation_endpoint": f"{b}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": list(SCOPES),
    }


def redirect_allowed(uri: str) -> bool:
    try:
        p = urlparse(uri)
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    if p.scheme == "http" and host in ("localhost", "127.0.0.1"):
        return True
    if p.scheme != "https":
        return False
    return any(host == h or host.endswith("." + h) for h in config.OAUTH_REDIRECT_HOSTS)


@router.post("/oauth/register")
async def register(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
    uris = body.get("redirect_uris") or []
    if not uris or not all(isinstance(u, str) and redirect_allowed(u) for u in uris):
        return JSONResponse(
            {"error": "invalid_redirect_uri",
             "error_description": "Dozwolone adresy powrotu: " + ", ".join(config.OAUTH_REDIRECT_HOSTS) + ", localhost."},
            status_code=400,
        )
    client = OAuthClient(client_id="crm_c_" + secrets.token_urlsafe(16),
                         name=str(body.get("client_name") or "Aplikacja")[:200], redirect_uris="\n".join(uris))
    db.add(client)
    db.commit()
    return JSONResponse(
        {"client_id": client.client_id, "client_name": client.name, "redirect_uris": uris,
         "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
         "response_types": ["code"], "client_id_issued_at": int(now().timestamp())},
        status_code=201,
    )


def _client(db: Session, client_id: str) -> OAuthClient | None:
    return db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))


def _oauth_error_redirect(redirect_uri: str, state: str | None, error: str) -> RedirectResponse:
    q = {"error": error}
    if state:
        q["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(q)}", status_code=303)


@router.get("/oauth/authorize", response_class=HTMLResponse)
def authorize_form(request: Request, db: Session = Depends(get_db)):
    from .main import render  # unikamy importu cyklicznego

    p = request.query_params
    client = _client(db, p.get("client_id", ""))
    redirect_uri = p.get("redirect_uri", "")
    if client is None or redirect_uri not in client.redirect_uris.splitlines():
        raise HTTPException(400, "Nieznana aplikacja albo niezgodny adres powrotu.")
    if p.get("response_type") != "code" or p.get("code_challenge_method") != "S256" or not p.get("code_challenge"):
        return _oauth_error_redirect(redirect_uri, p.get("state"), "invalid_request")
    if not request.session.get("user"):
        request.session["next"] = str(request.url.path) + "?" + str(request.url.query)
        return RedirectResponse("/login", status_code=303)
    requested = normalize_scopes(p.get("scope") or "read write")
    return render(request, db, "oauth_consent.html", client=client, params=dict(p), scopes=SCOPES, requested=requested)


@router.post("/oauth/authorize")
async def authorize_decide(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user"):
        raise HTTPException(401)
    form = await request.form()
    client = _client(db, form.get("client_id", ""))
    redirect_uri = form.get("redirect_uri", "")
    if client is None or redirect_uri not in client.redirect_uris.splitlines():
        raise HTTPException(400, "Nieznana aplikacja albo niezgodny adres powrotu.")
    state = form.get("state") or None
    if form.get("decision") != "allow":
        return _oauth_error_redirect(redirect_uri, state, "access_denied")
    scopes = [s for s in form.getlist("scope") if s in SCOPES] or ["read"]
    if "issue" in scopes and "write" not in scopes:
        scopes.append("write")
    code = secrets.token_urlsafe(32)
    db.add(AuthCode(code_hash=sha(code), client_id=client.client_id, redirect_uri=redirect_uri,
                    code_challenge=form.get("code_challenge", ""), scopes=" ".join(scopes),
                    resource=form.get("resource", ""), expires_at=now() + CODE_TTL))
    db.commit()
    q = {"code": code}
    if state:
        q["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(q)}", status_code=303)


def _token_error(error: str, description: str = "") -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=400,
                        headers={"Cache-Control": "no-store"})


def pkce_ok(verifier: str, challenge: str) -> bool:
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return secrets.compare_digest(digest, challenge)


@router.post("/oauth/token")
async def token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str = Form(""),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    code_verifier: str = Form(""),
    refresh_token: str = Form(""),
    db: Session = Depends(get_db),
):
    client = _client(db, client_id)
    if client is None:
        return JSONResponse({"error": "invalid_client"}, status_code=401)

    if grant_type == "authorization_code":
        row = db.scalar(select(AuthCode).where(AuthCode.code_hash == sha(code)))
        if row is None or row.used or row.expires_at < now() or row.client_id != client.client_id:
            return _token_error("invalid_grant", "Kod wygasł albo był już użyty.")
        row.used = True
        db.commit()
        if row.redirect_uri != redirect_uri:
            return _token_error("invalid_grant", "Niezgodny adres powrotu.")
        if not code_verifier or not pkce_ok(code_verifier, row.code_challenge):
            return _token_error("invalid_grant", "Błędny code_verifier (PKCE).")
        return JSONResponse(issue_tokens(db, client, row.scopes.split()), headers={"Cache-Control": "no-store"})

    if grant_type == "refresh_token":
        row = db.scalar(select(ApiToken).where(ApiToken.token_hash == sha(refresh_token), ApiToken.kind == "refresh"))
        if row is None or row.client_id != client.client_id:
            return _token_error("invalid_grant", "Nieznany token odświeżania.")
        if row.revoked:
            # Ponowne uzycie zuzytego tokenu: ktos go mogl przechwycic, odcinamy cala zgode.
            revoke_grant(db, row.grant_id)
            return _token_error("invalid_grant", "Token odświeżania był już użyty. Połącz konektor ponownie.")
        if row.expires_at < now():
            return _token_error("invalid_grant", "Token odświeżania wygasł.")
        row.revoked = True
        db.commit()
        return JSONResponse(issue_tokens(db, client, row.scope_list, grant_id=row.grant_id),
                            headers={"Cache-Control": "no-store"})

    return _token_error("unsupported_grant_type")


@router.post("/oauth/revoke")
async def revoke(token: str = Form(""), db: Session = Depends(get_db)):
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == sha(token)))
    if row is not None and row.kind != "key":
        revoke_grant(db, row.grant_id)
    return JSONResponse({}, status_code=200)
