"""Szyfrowanie sekretow w bazie (np. token KSeF) kluczem wyprowadzonym z SECRET_KEY (Fernet: AES-128-CBC + HMAC)."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from . import config


def _fernet() -> Fernet:
    key = hashlib.sha256(b"sygnavo-secretbox|" + config.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def seal(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode() if text else ""


def open_(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise ValueError("Nie można odczytać zapisanego sekretu (zmieniono SECRET_KEY?). Wpisz go ponownie.")
