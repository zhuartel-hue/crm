"""SMS-y przy kontrahencie.

Bramka SMS (np. SMSAPI, SerwerSMS) zostanie podlaczona pozniej. Do tego czasu SMS zapisuje sie w historii
kontrahenta ze statusem „czeka na bramke SMS” i NIE jest wysylany. Po podlaczeniu bramki wystarczy uzupelnic
funkcje `_provider_send` i wysylac oczekujace z kolejki.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from .models import Contractor, SmsMessage

MAX_LEN = 640  # 4 czesci SMS


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"[^0-9+]", "", phone or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if re.fullmatch(r"[0-9]{9}", digits):
        digits = "+48" + digits
    if not re.fullmatch(r"\+[0-9]{8,15}", digits):
        raise ValueError("Podaj poprawny numer telefonu (np. 600 100 200 albo +48600100200).")
    return digits


def provider_configured(company) -> bool:
    return False


def _provider_send(company, phone: str, body: str) -> str:  # pragma: no cover - bramka jeszcze niepodlaczona
    raise NotImplementedError


def create(db: Session, actor: str, contractor: Contractor, phone: str, body: str) -> SmsMessage:
    body = (body or "").strip()
    if not body:
        raise ValueError("Wpisz treść SMS-a.")
    if len(body) > MAX_LEN:
        raise ValueError(f"SMS jest za długi (maksymalnie {MAX_LEN} znaków).")
    sms = SmsMessage(company_id=contractor.company_id, contractor_id=contractor.id, phone=normalize_phone(phone),
                     body=body, status="waiting", created_by=actor[:100], direction="out", provider_ref="", error="")
    db.add(sms)
    return sms
