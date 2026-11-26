"""Import zdjec produktow pobranych z Firmao (tools/firmao_photos.py) do bazy.

    python -m app.firmao_photos_import /tmp/zdjecia [--dry-run]

Katalog z manifest.json {id produktu w Firmao: [pliki]}. Produkt szuka sie po source_ref "firmao:<id>".
Produkt, ktory ma juz zdjecia w Sygnavo, jest pomijany (nie dubluje przy powtorce). Zapis co 20 produktow,
zeby zmiescic sie w pamieci malego serwera.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from . import products
from .db import SessionLocal
from .models import Product


def run(folder: Path, dry_run: bool = False, log=print) -> dict:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    out = {"products": 0, "photos": 0, "skipped_has_photos": 0, "missing_product": 0, "errors": []}
    with SessionLocal() as db:
        refs = {p.source_ref: p.id for p in db.scalars(select(Product).where(Product.source_ref.like("firmao:%")))}
        done = 0
        for fid, files in manifest.items():
            pid = refs.get(f"firmao:{fid}")
            if pid is None:
                out["missing_product"] += 1
                continue
            p = db.get(Product, pid)
            if p.photos:
                out["skipped_has_photos"] += 1
                continue
            raw = [(name, (folder / name).read_bytes()) for name in files if (folder / name).is_file()]
            errors = products.add_photos(p, raw)
            out["errors"] += [f"{p.name}: {e}" for e in errors]
            out["photos"] += len(p.photos)
            out["products"] += 1
            done += 1
            if done % 20 == 0:
                if dry_run:
                    db.flush()
                else:
                    db.commit()
                db.expunge_all()
                log(f"... {done} produktów")
        if dry_run:
            db.rollback()
        else:
            db.commit()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.firmao_photos_import")
    ap.add_argument("katalog")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    res = run(Path(a.katalog), a.dry_run)
    errors = res.pop("errors")
    print(res)
    for e in errors[:20]:
        print("  ", e)
    print("PROBA: nic nie zapisano." if a.dry_run else "Zapisano.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
