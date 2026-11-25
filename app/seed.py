"""Pierwsze uruchomienie: tworzy obie firmy z danymi do uzupelnienia w Ustawieniach."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Company

COMPANIES = [
    dict(short_name="Margot", name="Margot (uzupełnij pełną nazwę w Ustawieniach)", nip="0000000000"),
    dict(short_name="ARI ERDMANN WORLDS", name="ARI ERDMANN WORLDS (uzupełnij formę spółki w Ustawieniach)", nip="0000000000"),
]


def seed(db: Session) -> None:
    if db.execute(select(Company.id).limit(1)).first():
        return
    for data in COMPANIES:
        db.add(Company(**data))
    db.commit()
