"""Darmowa pomoc bez AI: wyszukiwarka instrukcji z bazy wiedzy (docs/wiedza).

Okienko „Pomoc” dziala w tym trybie, gdy nie ma klucza ANTHROPIC_API_KEY (albo AI_MODE=szukaj).
Pytanie dzielimy na slowa, porownujemy z tytulami i sekcjami stron (bez polskich znakow, po poczatku slowa,
z kilkoma synonimami) i pokazujemy najlepiej pasujaca sekcje z krokami i zrzutami ekranu oraz linki do
podobnych instrukcji. Nic nie wysylamy na zewnatrz i nic nie kosztuje.
"""
from __future__ import annotations

import functools
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

STEM = 5
STOP = {
    "jak", "czy", "gdzie", "co", "mam", "moge", "mozna", "sie", "jest", "sa", "dla", "przy", "oraz", "albo",
    "lub", "ale", "aby", "zeby", "ten", "ta", "to", "tego", "tej", "tym", "moj", "moja", "moje", "mojej",
    "w", "we", "z", "ze", "na", "do", "od", "po", "o", "i", "a", "u", "nie", "tak", "jaki", "jaka", "jakie",
    "kiedy", "dlaczego", "prosze", "chce", "chcialbym", "potrzebuje", "zrobic", "program", "programie",
    "sygnavo", "trzeba", "musze", "mozesz", "pomoz", "pomoc", "dodac", "ustawic",
}
# Slowa klienta -> slowa z instrukcji (po normalizacji, pierwsze 5 liter).
SYNONYMS = {
    "mail": "poczt", "email": "poczt", "maila": "poczt", "gmail": "poczt", "skrzy": "poczt",
    "praco": "uzytk", "zespo": "uzytk", "login": "logow", "haslo": "logow",
    "klien": "kontr", "nabyw": "kontr", "towar": "produ", "uslug": "produ", "cennik": "produ",
    "rachu": "faktu", "fv": "faktu", "logot": "logo", "znak": "logo", "druk": "raport",
    "ksieg": "raport", "zakup": "koszt", "wydat": "koszt", "wycia": "bank", "przel": "bank",
    "certy": "ksef", "token": "ksef", "numer": "numer", "magaz": "magaz",
}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", norm(text)):
        if len(w) < 2 or w in STOP:
            continue
        stem = w[:STEM]
        out.append(SYNONYMS.get(w, SYNONYMS.get(stem, stem)))
    return out


@dataclass
class Section:
    slug: str           # nazwa pliku bez .md
    kind: str           # moduly / faq
    title: str          # tytul strony
    heading: str        # naglowek sekcji ("" = wstep)
    text: str           # tresc sekcji (Markdown)
    head_tokens: set
    body_tokens: list


def _split(path: Path, kind: str) -> list[Section]:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    title = next((l[2:].strip() for l in lines if l.startswith("# ")), path.stem)
    body = [l for l in lines if not l.startswith("# ") and not re.match(r"^(Aktualizacja|Moduł|Dla klientów):", l)]
    parts: list[tuple[str, list[str]]] = [("", [])]
    for l in body:
        if l.startswith("## "):
            parts.append((l[3:].strip(), []))
        else:
            parts[-1][1].append(l)
    out = []
    for heading, chunk in parts:
        text = "\n".join(chunk).strip()
        if not text:
            continue
        out.append(Section(path.stem, kind, title, heading, text,
                           set(tokens(title + " " + heading)), tokens(text)))
    return out


@functools.lru_cache(maxsize=4)
def _index(stamp: tuple) -> tuple[list[Section], dict[str, float]]:
    from .assistant import knowledge_files

    sections: list[Section] = []
    for p in knowledge_files():
        sections += _split(p, p.parent.name)
    df: dict[str, int] = {}
    for s in sections:
        for t in s.head_tokens | set(s.body_tokens):
            df[t] = df.get(t, 0) + 1
    n = max(len(sections), 1)
    idf = {t: math.log(1 + n / c) for t, c in df.items()}
    return sections, idf


def index() -> tuple[list[Section], dict[str, float]]:
    from .assistant import knowledge_files

    stamp = tuple((str(p), p.stat().st_mtime_ns) for p in knowledge_files())
    return _index(stamp)


def search(question: str, limit: int = 4) -> list[tuple[float, Section]]:
    sections, idf = index()
    q = list(dict.fromkeys(tokens(question)))
    if not q:
        return []
    scored = []
    for s in sections:
        score = 0.0
        for t in q:
            w = idf.get(t, 0.0)
            if t in s.head_tokens:
                score += 3 * w
            c = s.body_tokens.count(t)
            if c:
                score += w * (1 + math.log(c))
        if score:
            if "krok po kroku" in s.heading:
                score *= 1.2  # instrukcja krok po kroku przed opisem
            if s.kind == "faq":
                score *= 1.15  # FAQ to gotowe odpowiedzi na pytania klientow
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return scored[:limit]


def find_article(slug: str) -> Path | None:
    from .assistant import knowledge_files

    return next((p for p in knowledge_files() if p.stem == slug), None)


def answer(question: str) -> str:
    """Odpowiedz w prostym Markdownie (renderuje ja assistant.render_answer)."""
    hits = search(question)
    if not hits or hits[0][0] < 1.0:
        return ("Nie znalazłem instrukcji do tego pytania. Spróbuj innymi słowami (np. „logo”, „KSeF”, "
                "„nowy użytkownik”) albo zajrzyj do [wszystkich instrukcji](/pomoc/artykuly).\n\n"
                "- [Pierwsze kroki](/pomoc/artykul/pierwsze-kroki)\n- [Mapa programu: gdzie co jest](/pomoc/artykul/mapa-programu)")
    best = hits[0][1]
    head = f"**{best.title}**" + (f" · {best.heading}" if best.heading else "")
    parts = [head, "", best.text, "", f"[Cała instrukcja: {best.title}](/pomoc/artykul/{best.slug})"]
    others, seen = [], {best.slug}
    for _, s in hits[1:]:
        if s.slug not in seen:
            seen.add(s.slug)
            others.append(f"- [{s.title}](/pomoc/artykul/{s.slug})")
    if others:
        parts += ["", "Zobacz też:"] + others[:3]
    return "\n".join(parts)
