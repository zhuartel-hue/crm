"""Import archiwum z Firmao z wiersza polecen (na serwerze, w kontenerze aplikacji).

    python -m app.firmao_cli /tmp/firmao-bundle.json.gz --firma 5551859956 --dry-run
    python -m app.firmao_cli /tmp/firmao-bundle.json.gz --firma 5551859956

--dry-run robi caly import i na koncu go wycofuje (nic nie zostaje w bazie), zeby zobaczyc liczby.
Plik powstaje przez tools/firmao_bundle.py. Ponowny import aktualizuje zamiast dublowac.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys

from sqlalchemy import func, select

from . import firmao_archive
from .db import SessionLocal
from .models import Company, Contact, Contractor, Delivery, Invoice, Offer, Product, PurchaseInvoice


def read_bundle(path: str) -> dict:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def counts(db, company_id: int) -> dict:
    def n(model, *cond):
        return db.scalar(select(func.count()).select_from(model).where(*cond)) or 0
    return {"klienci": n(Contractor), "osoby": n(Contact), "produkty": n(Product),
            "faktury": n(Invoice, Invoice.company_id == company_id),
            "koszty": n(PurchaseInvoice, PurchaseInvoice.company_id == company_id),
            "wz_pz": n(Delivery, Delivery.company_id == company_id),
            "oferty": n(Offer, Offer.company_id == company_id)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.firmao_cli")
    ap.add_argument("plik")
    ap.add_argument("--firma", required=True, help="NIP firmy w Sygnavo, do ktorej ida dokumenty")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    data = read_bundle(a.plik)
    nip = re.sub(r"\D", "", a.firma)
    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.nip == nip))
        if company is None:
            print(f"Nie ma firmy z NIP {nip}.", file=sys.stderr)
            return 2
        print(f"Firma: {company.short_name} ({company.nip})")
        print("W pliku: " + ", ".join(f"{k} {len(v)}" for k, v in data.items()))
        print(f"Przed: {counts(db, company.id)}")
        summary = firmao_archive.import_all(db, data, company)
        after = counts(db, company.id)
        print(f"Po: {after}")
        print(f"Wzory numeracji: {summary.get('wzory')}")
        print(f"Powiazania: {summary.get('powiazania')}")
        if a.dry_run:
            db.rollback()
            print("PROBA: nic nie zapisano.")
        else:
            db.commit()
            print("Zapisano.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
