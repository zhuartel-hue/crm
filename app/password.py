"""Haslo administratora: z .env (ADMIN_PASSWORD), a po zmianie w aplikacji skrot scrypt w tabeli app_settings."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from sqlalchemy.orm import Session

from . import config
from .models import AppSetting

KEY = "admin_password_hash"
MIN_LEN = 10
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def _verify_hash(password: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=_N, r=_R, p=_P, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def check_login(db: Session, login: str, password: str) -> bool:
    if not hmac.compare_digest(login, config.ADMIN_LOGIN):
        # Taki sam czas odpowiedzi niezaleznie od loginu.
        _verify_hash(password, hash_password("x"))
        return False
    row = db.get(AppSetting, KEY)
    if row and row.value:
        return _verify_hash(password, row.value)
    return hmac.compare_digest(password, config.ADMIN_PASSWORD)


def change(db: Session, login: str, old: str, new: str, repeat: str) -> None:
    if not check_login(db, login, old):
        raise ValueError("Obecne hasło jest niepoprawne.")
    if new != repeat:
        raise ValueError("Nowe hasła nie są takie same.")
    if len(new) < MIN_LEN:
        raise ValueError(f"Nowe hasło musi mieć co najmniej {MIN_LEN} znaków.")
    if new == old:
        raise ValueError("Nowe hasło musi być inne niż obecne.")
    row = db.get(AppSetting, KEY) or AppSetting(key=KEY)
    row.value = hash_password(new)
    db.add(row)
