"""Dane firmy po NIP z wykazu podatnikow VAT Ministerstwa Finansow (biala lista, wl-api.mf.gov.pl).

Darmowe, bez klucza. Limit MF: 100 zapytan "search" dziennie z jednego adresu IP, wiec wynik trzymamy w pamieci.
"""
from __future__ import annotations

import datetime as dt
import re
import time

import httpx

API = "https://wl-api.mf.gov.pl"
_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_SECONDS = 6 * 3600

# Do testow mozna podmienic: http_get(url) -> httpx.Response
def http_get(url: str) -> httpx.Response:
    return httpx.get(url, timeout=10.0, headers={"Accept": "application/json"})


class NipLookupError(ValueError):
    pass


def clean_nip(nip: str) -> str:
    return re.sub(r"[^0-9]", "", nip or "")


def valid_nip(nip: str) -> bool:
    if not re.fullmatch(r"\d{10}", nip):
        return False
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    return sum(int(nip[i]) * w for i, w in enumerate(weights)) % 11 == int(nip[9])


def split_address(text: str) -> tuple[str, str, str]:
    """"UL. PROCHOWA 5, 89-600 CHOJNICE" -> ("UL. PROCHOWA 5", "89-600", "CHOJNICE")."""
    text = (text or "").strip()
    street, _, rest = text.rpartition(",")
    if not street:
        street, rest = "", text
    m = re.match(r"\s*(\d{2}-\d{3})\s+(.*)", rest)
    if m:
        return street.strip(), m.group(1), m.group(2).strip()
    return text, "", ""


def lookup(nip: str) -> dict:
    nip = clean_nip(nip)
    if not valid_nip(nip):
        raise NipLookupError("Niepoprawny NIP (10 cyfr z poprawną sumą kontrolną).")
    hit = _CACHE.get(nip)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        r = http_get(f"{API}/api/search/nip/{nip}?date={dt.date.today().isoformat()}")
    except httpx.HTTPError as e:
        raise NipLookupError(f"Brak połączenia z wykazem VAT: {e}") from None
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        raise NipLookupError(f"Wykaz VAT: {data.get('message') or r.status_code}")
    subject = (data.get("result") or {}).get("subject")
    if not subject:
        raise NipLookupError("Nie ma tego NIP-u w wykazie podatników VAT. Wpisz dane ręcznie.")
    address = subject.get("workingAddress") or subject.get("residenceAddress") or ""
    street, postal, city = split_address(address)
    out = dict(
        nip=nip, name=subject.get("name") or "", status_vat=subject.get("statusVat") or "",
        regon=subject.get("regon") or "", krs=subject.get("krs") or "",
        street=street, postal_code=postal, city=city, address=address,
        accounts=list(subject.get("accountNumbers") or []),
    )
    _CACHE[nip] = (time.monotonic(), out)
    return out
