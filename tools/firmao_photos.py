#!/usr/bin/env python3
"""Pobiera zdjecia produktow z eksportu Firmao (products.json) na dysk, zanim wygasna linki (podpisane linki S3).

    python3 tools/firmao_photos.py [--zrodlo ~/Documents/AI/firmao-eksport] [--cel .../zdjecia]

Zapisuje pliki <id produktu>-<n>.<rozszerzenie> i manifest.json {id produktu: [pliki po kolei]}.
Pliki, ktore juz sa, pomija (mozna przerwac i uruchomic ponownie). Tylko biblioteka standardowa, same GET.
Potem: tar -czf zdjecia.tgz -C <cel> . i import na serwerze: python -m app.firmao_photos_import.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def rows_of(raw):
    if isinstance(raw, dict):
        for key in ("date", "data", "result"):
            if isinstance(raw.get(key), list):
                return raw[key]
        return []
    return raw if isinstance(raw, list) else []


def links(product: dict) -> list[str]:
    """Linki do zdjec produktu, w kolejnosci z Firmao (glowne pierwsze)."""
    imgs = product.get("images") or []
    if isinstance(imgs, dict):
        imgs = [imgs]
    imgs = sorted((i for i in imgs if isinstance(i, dict)),
                  key=lambda i: (not i.get("main", i.get("mainImage", False)), i.get("order") or i.get("position") or 0))
    out = []
    for i in imgs:
        acc = i.get("documentAccess") or i.get("document", {}).get("documentAccess") or {}
        href = acc.get("href") if isinstance(acc, dict) else None
        href = href or i.get("href") or i.get("url")
        if href and href.startswith("http") and href not in out:
            out.append(href)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zrodlo", default="~/Documents/AI/firmao-eksport")
    ap.add_argument("--cel", default="~/Documents/AI/firmao-eksport/zdjecia")
    a = ap.parse_args()
    src, dst = Path(a.zrodlo).expanduser(), Path(a.cel).expanduser()
    dst.mkdir(parents=True, exist_ok=True)
    products = rows_of(json.loads((src / "products.json").read_text(encoding="utf-8")))
    manifest, ok, skipped, failed = {}, 0, 0, []
    for p in products:
        files = []
        for n, url in enumerate(links(p), start=1):
            have = sorted(dst.glob(f"{p['id']}-{n}.*"))
            if have:
                files.append(have[0].name)
                skipped += 1
                continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "sygnavo-photos/1.0"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
                    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
            except (urllib.error.URLError, TimeoutError) as e:
                failed.append(f"{p['id']}-{n}: {e}")
                continue
            name = f"{p['id']}-{n}{EXT.get(ctype, '.jpg')}"
            (dst / name).write_bytes(data)
            files.append(name)
            ok += 1
            time.sleep(0.2)
        if files:
            manifest[str(p["id"])] = files
    (dst / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    os.chmod(dst, 0o700)
    print(f"produkty ze zdjeciami: {len(manifest)}, pobrane: {ok}, juz byly: {skipped}, bledy: {len(failed)}")
    for f in failed[:20]:
        print("  ", f)
    size = sum(f.stat().st_size for f in dst.iterdir()) // (1024 * 1024)
    print(f"razem {size} MB w {dst}")
    sys.exit(1 if failed and not ok and not skipped else 0)


if __name__ == "__main__":
    main()
