"""Kody jednorazowe TOTP (RFC 6238) do weryfikacji dwuetapowej: aplikacje Google Authenticator,
Microsoft Authenticator, 1Password itp. Sekret trzymamy zaszyfrowany (app/secretbox.py)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    num = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return f"{num:0{DIGITS}d}"


def now_code(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at or time.time()) // STEP))


def verify(secret: str, code: str, at: float | None = None) -> bool:
    """Kod z biezacego okna 30 s lub sasiedniego (rozjazd zegara telefonu)."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return False
    counter = int((at or time.time()) // STEP)
    return any(hmac.compare_digest(_code(secret, counter + d), code) for d in (-1, 0, 1))


def uri(secret: str, login: str, issuer: str = "Sygnavo") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(login)}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"


def qr_svg(text: str) -> str:
    import segno

    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="svg", scale=5, border=2, xmldecl=False, svgns=True)
    return out.getvalue().decode()
