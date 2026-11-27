# Baza wiedzy Sygnavo

Jedno źródło wiedzy o programie. Z tych plików powstaną później:
- **centrum pomocy i forum** dla klientów,
- **artykuły na blog i social media** (z grafikami i zrzutami ekranu),
- **asystent AI w Sygnavo**, który odpowiada klientom na pytania na podstawie tej wiedzy.

Dlatego piszemy ją od pierwszego dnia, na naszych prawdziwych doświadczeniach (Margot i ARI jako pierwszy klient).

## Zasady (obowiązkowe przy każdej zmianie, patrz CLAUDE.md)

1. Każdy nowy lub zmieniony moduł ma stronę w `moduly/<modul>.md`: do czego służy, jak go użyć krok po kroku, typowe błędy.
2. Każde pytanie lub problem, który właściciel zgłosił w trakcie pracy, trafia do `faq/` jako pytanie i odpowiedź (bez danych osobowych i danych klientów).
3. Zrzuty ekranu robi sam skrypt `python tools/zrzuty_pomocy.py` na danych testowych (czerwona ramka i numer przy tym,
   co kliknąć). Pliki i opis są w `zrzuty/`; w tekście wstawiamy je jako `![opis](../zrzuty/<id>.png)`. Nowy zrzut:
   dopisz go do listy SCREENS w skrypcie. Po zmianie wyglądu uruchom skrypt ponownie.
4. Język prosty, dla właściciela małej firmy, nie dla programisty. Krótkie zdania, kroki numerowane.
5. Na górze każdej strony: data ostatniej aktualizacji i wersja programu (commit).
6. Strona albo FAQ tylko do użytku wewnętrznego (serwer, wdrożenia) ma w trzecim wierszu `Dla klientów: nie`.
   Takich stron asystent AI (okienko Pomoc) nie dostaje.

## Szablon strony modułu

```markdown
# <Nazwa modułu>
Aktualizacja: RRRR-MM-DD · wersja: <commit>

## Do czego służy
## Jak zacząć (krok po kroku)
## Najczęstsze pytania
## Co zrobić, gdy coś nie działa
```

## Szablon pytania FAQ (`faq/<krotka-nazwa>.md`)

```markdown
# <Pytanie tak, jak zadałby je klient>
Moduł: <moduł> · Aktualizacja: RRRR-MM-DD

<Odpowiedź w 2–5 zdaniach lub krokach.>
```
