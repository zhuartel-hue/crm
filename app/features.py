"""Przelaczniki funkcji per organizacja (feature flags).

Wszyscy klienci korzystaja z tego samego programu, ale nowa funkcja moze najpierw dzialac tylko w
wybranych organizacjach (np. Margot i ARI), a dopiero po sprawdzeniu u wszystkich. Ustawienia firmy
(logo, numeracja, szablony, statusy) zawsze byly i sa osobne dla kazdej firmy.

Etapy funkcji:
- "beta": wlaczona tylko organizacjom, ktore maja jej klucz w Organisation.features,
- "all": wlaczona wszystkim (klucz w Organisation.features nie ma juz znaczenia),
- "off": wylaczona wszystkim (np. awaryjnie).
Nowa funkcja: dopisz ja do FEATURES ze stage="beta", w kodzie/szablonie sprawdzaj enabled()/feature(),
po sprawdzeniu zmien stage na "all", a gdy jest u wszystkich - usun przelacznik z kodu.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import Organisation


@dataclass(frozen=True)
class Feature:
    label: str
    stage: str  # beta / all / off


FEATURES: dict[str, Feature] = {
    # Przyklad i test mechanizmu; prawdziwe funkcje dopisujemy tu przy ich budowie.
    "dwuetapowe_wymuszone": Feature("Wymuszona weryfikacja dwuetapowa dla właściciela i księgowości", "beta"),
}


def org_keys(org: Organisation | None) -> set[str]:
    return {k.strip() for k in (org.features or "").split(",") if k.strip()} if org else set()


def enabled(org: Organisation | None, key: str) -> bool:
    f = FEATURES.get(key)
    if f is None or f.stage == "off":
        return False
    return f.stage == "all" or key in org_keys(org)


def set_enabled(db: Session, org: Organisation, key: str, on: bool) -> None:
    if key not in FEATURES:
        raise ValueError(f"Nieznana funkcja: {key}")
    keys = org_keys(org)
    keys.add(key) if on else keys.discard(key)
    org.features = ",".join(sorted(keys))
