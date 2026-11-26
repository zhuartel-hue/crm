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
from .models import Company, Contact, Contractor, ContractorNote, Delivery, Invoice, Offer, Product, PurchaseInvoice


def read_bundle(path: str) -> dict:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def counts(db, company_id: int) -> dict:
    def n(model, *cond):
        return db.scalar(select(func.count()).select_from(model).where(*cond)) or 0
    return {"klienci": n(Contractor), "osoby": n(Contact), "notatki": n(ContractorNote), "produkty": n(Product),
            "faktury": n(Invoice, Invoice.company_id == company_id),
            "koszty": n(PurchaseInvoice, PurchaseInvoice.company_id == company_id),
            "wz_pz": n(Delivery, Delivery.company_id == company_id),
            "oferty": n(Offer, Offer.company_id == company_id)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.firmao_cli")
    ap.add_argument("plik")
    ap.add_argument("--firma", required=True, help="NIP firmy w Sygnavo, do ktorej ida dokumenty")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tylko", default="", help="wybrane czesci, np. notatki albo klienci,osoby "
                                                 f"({', '.join(firmao_archive.SECTIONS)})")
    ap.add_argument("--dodaj", action="append", default=[], metavar="KLUCZ=PLIK",
                    help="dodatkowy plik z eksportu, np. phonecalls=/root/firmao-eksport/phonecalls.json")
    a = ap.parse_args(argv)
    data = read_bundle(a.plik)
    for extra in a.dodaj:
        key, _, path = extra.partition("=")
        if not key or not path:
            print(f"--dodaj: podaj KLUCZ=PLIK, a jest „{extra}”", file=sys.stderr)
            return 2
        data[key.strip()] = read_bundle(path.strip())
    nip = re.sub(r"\D", "", a.firma)
    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.nip == nip))
        if company is None:
            print(f"Nie ma firmy z NIP {nip}.", file=sys.stderr)
            return 2
        print(f"Firma: {company.short_name} ({company.nip})")
        print("W pliku: " + ", ".join(f"{k} {len(v)}" for k, v in data.items()))
        print(f"Przed: {counts(db, company.id)}")
        only = {x.strip() for x in a.tylko.split(",") if x.strip()} or None
        if only and only - set(firmao_archive.SECTIONS):
            print(f"Nieznane części: {', '.join(sorted(only - set(firmao_archive.SECTIONS)))}", file=sys.stderr)
            return 2
        try:
            summary = firmao_archive.import_all(db, data, company, only=only)
        except ValueError as e:
            db.rollback()
            print(str(e), file=sys.stderr)
            return 2
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
