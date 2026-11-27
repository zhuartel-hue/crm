"""Klient Enable Banking (posrednik PSD2/AIS, https://enablebanking.com/docs/api/reference/).

Sygnavo ma jedna aplikacje Enable Banking (identyfikator + klucz prywatny RSA z Control Panel), zapisana
w app_settings: klucz zaszyfrowany (secretbox). Kazda firma podpina swoje konto osobno: logowanie w banku,
zgoda ważna do ~180 dni, potem odnowienie.

Kazde zapytanie niesie JWT RS256: naglowek kid = identyfikator aplikacji, iss "enablebanking.com",
aud "api.enablebanking.com", czas zycia do 24 h (uzywamy 1 h).
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy.orm import Session

from .. import config, secretbox
from ..models import AppSetting

API = "https://api.enablebanking.com"
KEY_APP_ID = "enablebanking_app_id"
KEY_PEM = "enablebanking_key"


class BankApiError(ValueError):
    pass


# Do testow mozna podmienic: http(method, url, headers, params, json) -> httpx.Response
def http(method: str, url: str, headers: dict, params: dict | None = None, body: dict | None = None) -> httpx.Response:
    return httpx.request(method, url, headers=headers, params=params, json=body, timeout=30.0)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def load_key(pem: str) -> rsa.RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError):
        raise BankApiError("To nie jest klucz prywatny w formacie PEM (plik .pem z Enable Banking).") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise BankApiError("Klucz musi być kluczem RSA.")
    return key


def make_jwt(app_id: str, pem: str, now: int | None = None, ttl: int = 3600) -> str:
    iat = int(now if now is not None else time.time())
    header = {"typ": "JWT", "alg": "RS256", "kid": app_id}
    body = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": iat, "exp": iat + ttl}
    signing = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}." \
              f"{_b64(json.dumps(body, separators=(',', ':')).encode())}"
    sig = load_key(pem).sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing}.{_b64(sig)}"


# ---------------------------------------------------------------- konfiguracja aplikacji

def get_config(db: Session) -> tuple[str, str]:
    app = db.get(AppSetting, KEY_APP_ID)
    key = db.get(AppSetting, KEY_PEM)
    return (app.value if app else ""), (secretbox.open_(key.value) if key and key.value else "")


def is_configured(db: Session) -> bool:
    app = db.get(AppSetting, KEY_APP_ID)
    key = db.get(AppSetting, KEY_PEM)
    return bool(app and app.value and key and key.value)


def save_config(db: Session, app_id: str, pem: str | None) -> None:
    app_id = (app_id or "").strip()
    if not app_id:
        raise BankApiError("Podaj identyfikator aplikacji (Application ID) z Enable Banking.")
    row = db.get(AppSetting, KEY_APP_ID) or AppSetting(key=KEY_APP_ID)
    row.value = app_id[:100]
    db.add(row)
    if pem:
        pem = pem.strip()
        load_key(pem)
        krow = db.get(AppSetting, KEY_PEM) or AppSetting(key=KEY_PEM)
        krow.value = secretbox.seal(pem)
        db.add(krow)
    elif not ((k := db.get(AppSetting, KEY_PEM)) and k.value):
        raise BankApiError("Dodaj plik klucza .pem pobrany przy rejestracji aplikacji.")


# ---------------------------------------------------------------- zapytania

class Client:
    def __init__(self, app_id: str, pem: str):
        if config.TEST_PANEL:
            raise BankApiError(config.TEST_PANEL_MSG)
        if not app_id or not pem:
            raise BankApiError("Połączenie z bankiem nie jest skonfigurowane (Bank → Połączenie z bankiem).")
        self.app_id, self.pem = app_id, pem

    @classmethod
    def from_db(cls, db: Session) -> "Client":
        return cls(*get_config(db))

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
              psu_ip: str = "") -> dict:
        headers = {"Authorization": f"Bearer {make_jwt(self.app_id, self.pem)}", "Accept": "application/json"}
        if psu_ip:
            headers["Psu-Ip-Address"] = psu_ip
        try:
            r = http(method, API + path, headers, params, body)
        except httpx.HTTPError as e:
            raise BankApiError(f"Brak połączenia z Enable Banking: {e}") from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            msg = (data.get("message") or data.get("error") or "") if isinstance(data, dict) else ""
            detail = data.get("detail") if isinstance(data, dict) else ""
            raise BankApiError(f"Enable Banking {r.status_code}: {msg or r.text[:200]}"
                               + (f" ({detail})" if detail and isinstance(detail, str) else ""))
        return data if isinstance(data, dict) else {}

    def aspsps(self, country: str = "PL", psu_type: str = "") -> list[dict]:
        params = {"country": country, "service": "AIS"}
        if psu_type:
            params["psu_type"] = psu_type
        return list(self._call("GET", "/aspsps", params=params).get("aspsps") or [])

    def start_auth(self, aspsp_name: str, country: str, psu_type: str, valid_until: dt.datetime, state: str,
                   redirect_url: str, psu_ip: str = "") -> dict:
        body = {
            "access": {"valid_until": valid_until.replace(microsecond=0).isoformat() + "Z"},
            "aspsp": {"name": aspsp_name, "country": country},
            "state": state, "redirect_url": redirect_url, "psu_type": psu_type, "language": "pl",
        }
        return self._call("POST", "/auth", body=body, psu_ip=psu_ip)

    def create_session(self, code: str, psu_ip: str = "") -> dict:
        return self._call("POST", "/sessions", body={"code": code}, psu_ip=psu_ip)

    def get_session(self, session_id: str) -> dict:
        return self._call("GET", f"/sessions/{session_id}")

    def delete_session(self, session_id: str) -> None:
        self._call("DELETE", f"/sessions/{session_id}")

    def transactions(self, account_uid: str, date_from: dt.date, date_to: dt.date | None = None) -> list[dict]:
        out: list[dict] = []
        params: dict = {"date_from": date_from.isoformat()}
        if date_to:
            params["date_to"] = date_to.isoformat()
        for _ in range(100):  # zabezpieczenie przed nieskonczonym stronicowaniem
            data = self._call("GET", f"/accounts/{account_uid}/transactions", params=params)
            out.extend(data.get("transactions") or [])
            key = data.get("continuation_key")
            if not key:
                break
            params = {**params, "continuation_key": key}
        return out
