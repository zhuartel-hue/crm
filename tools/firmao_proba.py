#!/usr/bin/env python3
"""Proba importu archiwum z Firmao na pustej bazie SQLite (na komputerze, bez serwera).

    pip install sqlalchemy cryptography
    python3 tools/firmao_proba.py ~/Documents/AI/firmao-eksport/firmao-bundle.json.gz

Wypisuje, ile czego wejdzie, i liste rzeczy do sprawdzenia (zmienione numery, faktury bez pozycji, sumy pozycji
inne niz naglowek, dokumenty bez klienta). Baza powstaje w katalogu tymczasowym i jest usuwana.
"""
from __future__ import annotations

import os
import sys
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="sygnavo-proba-")
os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/proba.db"
os.environ.setdefault("SECRET_KEY", "proba")
os.environ.setdefault("ADMIN_PASSWORD", "proba-" + os.urandom(6).hex())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app import firmao_archive, firmao_cli, numbering  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Company, Delivery, Invoice, Offer, PurchaseInvoice  # noqa: E402
from app.seed import seed  # noqa: E402


def main(path: str, nip: str = "5551859956") -> None:
    Base.metadata.create_all(engine)
    data = firmao_cli.read_bundle(path)
    with SessionLocal() as db:
        seed(db)
        db.commit()
        company = db.scalar(select(Company).where(Company.nip == nip))
        print("W pliku: " + ", ".join(f"{k} {len(v)}" for k, v in data.items()))
        s = firmao_archive.import_all(db, data, company)
        db.commit()
        print("\n== WYNIK ==")
        for k, v in s.items():
            print(f"{k}: {v}")
        print(f"liczby: {firmao_cli.counts(db, company.id)}")

        print("\n== DO SPRAWDZENIA ==")
        invs = list(db.scalars(select(Invoice).where(Invoice.company_id == company.id)))
        print("rodzaje faktur:", dict(Counter(i.kind for i in invs)))
        print("KSeF:", dict(Counter(i.ksef_status for i in invs)))
        print("platnosc:", dict(Counter(i.payment_method for i in invs)))
        unpaid = [i for i in invs if i.to_pay > 0]
        print(f"nieoplacone: {len(unpaid)} na {sum((i.to_pay for i in unpaid), Decimal(0))} zl")
        print("bez pozycji:", [i.number for i in invs if not i.items][:20])
        bad = []
        for i in invs:
            after = sum((x.gross for x in i.after_items), Decimal(0))
            before = sum((x.gross for x in i.before_items), Decimal(0))
            if abs((after - before) - i.total_gross) > Decimal("0.05"):
                bad.append(f"{i.number}: pozycje {after - before} / naglowek {i.total_gross}")
        print(f"suma pozycji != naglowek: {len(bad)}", bad[:15])
        print("korekty bez faktury korygowanej:",
              [i.number for i in invs if i.kind == "correction" and not i.related_id][:20])
        print("bez klienta z listy:", sum(1 for i in invs if not i.contractor_id))
        print("zmienione numery (dubel w Firmao):", [i.number for i in invs if " (" in (i.number or "")][:20])
        for kind in ("vat", "correction"):
            last = max((i.number for i in invs if i.kind == kind and i.issue_date.year == 2026), default=None)
            print(f"ostatni numer {kind} 2026: {last}")
        m = db.get(Company, company.id)
        for kind in ("vat", "correction", "wz", "pz", "offer"):
            d = max((x for x in [*(i.issue_date for i in invs)]), default=None)
            if d:
                seq_kind = kind
                print(f"nastepny {kind}: {numbering.next_number(db, m, seq_kind, d)[1]}")
        db.rollback()
        print("WZ/PZ bez pozycji:", db.scalar(select(func.count()).select_from(Delivery)
                                              .where(~Delivery.items.any())))
        print("oferty bez pozycji:", db.scalar(select(func.count()).select_from(Offer).where(~Offer.items.any())))
        print("koszty oplacone:", db.scalar(select(func.count()).select_from(PurchaseInvoice)
                                            .where(PurchaseInvoice.paid_at.is_not(None))))


if __name__ == "__main__":
    main(*sys.argv[1:])
