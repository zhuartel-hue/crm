#!/usr/bin/env python3
"""Eksport danych z Firmao przez API do plikow JSON (TYLKO ODCZYT: same zapytania GET).

Uruchamiany na komputerze, na ktorym leży plik z danymi dostepu (nigdy w czacie):
    python3 tools/firmao_export.py [--plik ~/Documents/AI/firmao-api.txt] [--cel ~/Documents/AI/firmao-eksport]
                                   [--tylko customers,products] [--limit 100]

Plik z danymi dostepu (3 linie):
    FIRMAO_ORG=nazwa-organizacji-z-adresu
    FIRMAO_LOGIN=login-api
    FIRMAO_PASSWORD=haslo-api

Zasady Firmao API: do 60 zapytan na minute i 5 naraz (tu jedno naraz, ok. 1,1 s odstepu), 429 = czekamy.
Odpowiedz: {"date": [...], "totalSize": N} (totalSize = -1, gdy jest wiecej stron). Tylko biblioteka standardowa.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OBJECTS = [
    # (endpoint, sortowanie)
    ("customers", "id"),
    ("customergroups", "id"),
    ("contacts", "id"),
    ("products", "id"),
    ("transactions", "id"),        # faktury sprzedazy i zakupu (pole mode: SALE/PURCHASE)
    ("transactionentries", "id"),  # pozycje faktur (pole transaction.id)
    ("storagedocs", "id"),         # WZ/PZ (type: OUTSIDE_RELEASE / OUTSIDE_INCOME ...)
    ("offers", "id"),              # oferty, zamowienia, umowy (type: OFFER / ORDER / AGREEMENT)
    ("salesnotes", "id"),
    ("tasks", "id"),
    ("projects", "id"),
    ("salesopportunities", "id"),
    ("documents", "id"),           # lista zalacznikow (bez tresci)
    ("invoicenumbers", "id"),
    ("mails", "id"),               # naglowki maili (API nie podaje tresci)
    ("agreements", "id"),          # umowy / faktury cykliczne (okres, dzien, nastepna faktura)
    # sprzet: nazwa obiektu w API nie jest pewna, probujemy kolejno (blad 404 = 1 zapytanie)
    ("equipments", "id"),
    ("equipment", "id"),
    ("devices", "id"),
]
# obiekty spoza domyslnego przebiegu (pobierane tylko przez --tylko)
EXTRA = {"agreements", "equipments", "equipment", "devices"}


def read_credentials(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    missing = [k for k in ("FIRMAO_ORG", "FIRMAO_LOGIN", "FIRMAO_PASSWORD") if not out.get(k)]
    if missing:
        sys.exit(f"W pliku {path} brakuje: {', '.join(missing)}")
    org = out["FIRMAO_ORG"].strip().strip("/")
    if org.startswith("http"):
        org = urllib.parse.urlparse(org).path.strip("/").split("/")[0]
    out["FIRMAO_ORG"] = org
    return out


class Firmao:
    def __init__(self, org: str, login: str, password: str):
        self.base = f"https://system.firmao.pl/{urllib.parse.quote(org)}/svc/v1/"
        token = base64.b64encode(f"{login}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Content-type": "application/json; charset=UTF-8",
                        "Accept": "application/json", "User-Agent": "sygnavo-export/1.0"}
        self.last = 0.0
        self.calls = 0

    def get(self, endpoint: str, params: dict) -> dict:
        url = self.base + endpoint + "?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            wait = 1.1 - (time.monotonic() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            self.calls += 1
            req = urllib.request.Request(url, headers=self.headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.loads(r.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                if e.code == 429 or e.code >= 500:
                    time.sleep(min(60, 5 * (attempt + 1)))
                    continue
                raise RuntimeError(f"{endpoint}: HTTP {e.code} {body}") from None
            except urllib.error.URLError as e:
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"{endpoint}: brak odpowiedzi po kilku probach")

    def all(self, endpoint: str, sort: str, limit: int, progress) -> list:
        rows, start = [], 0
        while True:
            data = self.get(endpoint, {"start": start, "limit": limit, "sort": sort, "dir": "ASC",
                                       "dataFormat": "MEDIUM"})
            page = data.get("date")
            if page is None:
                page = data.get("data") or []
            rows.extend(page)
            total = data.get("totalSize", -1)
            progress(endpoint, len(rows), total)
            if len(page) < limit or (isinstance(total, int) and total >= 0 and len(rows) >= total):
                return rows
            start += len(page)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plik", default="~/Documents/AI/firmao-api.txt")
    ap.add_argument("--cel", default="~/Documents/AI/firmao-eksport")
    ap.add_argument("--tylko", default="", help="lista obiektow po przecinku")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--test", action="store_true", help="tylko sprawdz polaczenie (1 zapytanie)")
    a = ap.parse_args()

    cred = read_credentials(Path(os.path.expanduser(a.plik)))
    api = Firmao(cred["FIRMAO_ORG"], cred["FIRMAO_LOGIN"], cred["FIRMAO_PASSWORD"])
    if a.test:
        d = api.get("customers", {"start": 0, "limit": 1, "dataFormat": "MEDIUM"})
        print(f"Połączenie OK. Klienci w Firmao: {d.get('totalSize')}")
        return
    out = Path(os.path.expanduser(a.cel))
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    only = {x.strip() for x in a.tylko.split(",") if x.strip()}

    def progress(ep, n, total):
        print(f"\r{ep}: {n}" + (f" / {total}" if isinstance(total, int) and total >= 0 else ""), end="", flush=True)

    old = out / "podsumowanie.json"
    summary = json.loads(old.read_text(encoding="utf-8")) if only and old.is_file() else {}
    for ep, sort in OBJECTS:
        if (only and ep not in only) or (not only and ep in EXTRA):
            continue
        try:
            rows = api.all(ep, sort, a.limit, progress)
        except RuntimeError as e:
            print(f"\n{ep}: BŁĄD {e}")
            summary[ep] = f"błąd: {e}"
            continue
        (out / f"{ep}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        summary[ep] = len(rows)
        print()
    summary["_zapytan"] = api.calls
    summary["_data"] = time.strftime("%Y-%m-%d %H:%M")
    (out / "podsumowanie.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
