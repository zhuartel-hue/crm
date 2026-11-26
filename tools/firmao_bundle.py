#!/usr/bin/env python3
"""Sklada eksport z Firmao (tools/firmao_export.py) w jeden maly plik do importu w Sygnavo (app/firmao_cli.py).

    python3 tools/firmao_bundle.py [--zrodlo ~/Documents/AI/firmao-eksport] [--cel firmao-bundle.json.gz]

Bierze tylko potrzebne zbiory i wyrzuca puste pola, zeby import zmiescil sie w pamieci malego serwera.
Tylko biblioteka standardowa, nic nie wysyla.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path

COLLECTIONS = ["customers", "contacts", "products", "transactions", "transactionentries", "storagedocs", "offers"]
# Ciezkie pola, z ktorych import nie korzysta.
DROP = {"images", "documentAccess", "attachments", "changes", "history", "permissions", "customFields",
        "htmlContent", "content", "thumbnail"}


def slim(v):
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k in DROP:
                continue
            x = slim(x)
            if x is None or x == "" or x == [] or x == {}:
                continue
            out[k] = x
        return out
    if isinstance(v, list):
        return [slim(x) for x in v]
    return v


def rows_of(raw):
    if isinstance(raw, dict):
        for key in ("date", "data", "result"):
            if isinstance(raw.get(key), list):
                return raw[key]
        return []
    return raw if isinstance(raw, list) else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zrodlo", default="~/Documents/AI/firmao-eksport")
    ap.add_argument("--cel", default="~/Documents/AI/firmao-eksport/firmao-bundle.json.gz")
    a = ap.parse_args()
    src, dst = Path(a.zrodlo).expanduser(), Path(a.cel).expanduser()
    bundle = {}
    for name in COLLECTIONS:
        f = src / f"{name}.json"
        rows = rows_of(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else []
        bundle[name] = [slim(r) for r in rows]
        print(f"{name}: {len(rows)}")
    data = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(dst, "wb") as fh:
        fh.write(data)
    os.chmod(dst, 0o600)
    print(f"zapisano {dst} ({dst.stat().st_size // 1024} KB, po rozpakowaniu {len(data) // 1024} KB)")


if __name__ == "__main__":
    main()
