"""Pierwsze uruchomienie: tworzy obie firmy z danymi z bialej listy VAT (stan na 2026-11-25).

Nazwy, adresy i konta mozna potem zmienic w Ustawieniach.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Company

COMPANIES = [
    dict(
        short_name="Margot", name="ARKADIUSZ ERDMANN", nip="5551859956",
        street="ul. Prochowa 5", postal_code="89-600", city="Chojnice",
        bank_account="89 1020 1491 0000 4802 0132 0720", bank_name="PKO BP",
    ),
    dict(
        short_name="ARI ERDMANN WORLDS", name="ARI ERDMANN WORLDS SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ",
        nip="5552144076", street="ul. Prochowa 5", postal_code="89-600", city="Chojnice",
        bank_account="71 1020 1491 0000 4602 0132 1918", bank_name="PKO BP",
    ),
]


def seed(db: Session) -> None:
    if db.execute(select(Company.id).limit(1)).first():
        return
    for data in COMPANIES:
        db.add(Company(**data))
    db.commit()
