"""KOD I (kody-qr.md): link {qr}/invoice/{NIP sprzedawcy}/{P_1 DD-MM-RRRR}/{SHA-256 pliku faktury, Base64URL}."""
from __future__ import annotations

import datetime as dt
import io

import segno

from . import ENVIRONMENTS
from .crypto import sha256_b64url


def invoice_url(env: str, seller_nip: str, issue_date: dt.date, xml: bytes) -> str:
    return f"{ENVIRONMENTS[env]['qr']}/invoice/{seller_nip}/{issue_date.strftime('%d-%m-%Y')}/{sha256_b64url(xml)}"


def svg(url: str, scale: int = 3) -> str:
    """Kod QR jako SVG do PDF (ISO/IEC 18004, korekcja bledow M)."""
    out = io.BytesIO()
    segno.make(url, error="m", micro=False).save(out, kind="svg", scale=scale, border=2, xmldecl=False,
                                                  svgns=True, nl=False)
    return out.getvalue().decode()
