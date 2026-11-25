# Faktury: Margot i ARI ERDMANN WORLDS

Własny program do fakturowania dla autokomisu, zamiast Firmao. Dwie firmy w jednym
miejscu, faktury VAT i VAT marża na auta używane, PDF. Kolejne etapy: KSeF 2.0,
biała lista VAT, wysyłka mailem, przeniesienie danych z Firmao.

## Etap 1 (ten kod)

- **Dwie firmy**, przełączane u góry ekranu. Każda ma własne dane, konto bankowe
  i numerację. Kontrahenci są wspólni dla obu firm, auta i faktury są osobne.
- **Faktura VAT**: stawki 23, 8, 5, 0, zw, np; ceny wpisywane brutto albo netto;
  zestawienie wg stawek, kwota słownie.
- **Faktura VAT marża** (art. 120 ustawy o VAT): marża = cena sprzedaży brutto
  minus cena nabycia, VAT = marża × 23/123, przy ujemnej marży VAT 0. Na fakturze
  dla klienta jest tylko kwota brutto i adnotacja „procedura marży – towary
  używane”; marża i VAT od marży są widoczne tylko w programie.
- **Auta na placu**: marka, model, VIN, rocznik, przebieg, nr rej., cena nabycia,
  cena sprzedaży. „Sprzedaj” wypełnia fakturę marżową danymi auta, a po wystawieniu
  auto dostaje status „Sprzedany”.
- **Numeracja** bez dziur, osobno dla firmy i rodzaju faktury, od 1 co miesiąc
  albo co rok; wzór do ustawienia (domyślnie `FV/{nr}/{MM}/{RRRR}` i `FM/{nr}/{MM}/{RRRR}`).
- **Szkic → wystawienie**: numer nadaje się przy wystawieniu. Wystawionej faktury
  nie można zmienić ani usunąć (zmiany przez korektę w kolejnym etapie, potem KSeF).
- PDF (A4, polskie znaki), logowanie hasłem, wygodne na iPhonie.

Do uzupełnienia po pierwszym uruchomieniu (Ustawienia, osobno dla każdej firmy):
pełna nazwa, NIP, adres, konto bankowe. Na start są tam wartości zastępcze.

## Uruchomienie u siebie

```bash
pip install -r requirements-dev.txt     # WeasyPrint potrzebuje bibliotek Pango
ADMIN_PASSWORD=haslo uvicorn app.main:app --reload
# http://localhost:8000, login admin
pytest -q
```

Bez `DATABASE_URL` dane trafiają do `data/crm.db` (SQLite). Na serwerze jest PostgreSQL.

## Serwer

Program stoi na tym samym serwerze co ChirpStack i Sygnavo (repozytorium `lorawan`),
w katalogu `/opt/crm`, jako osobny projekt Docker Compose `crm`:

- kontenery `crm-app-1` (ok. 100–200 MB RAM, limit 384 MB) i `crm-db-1` (Postgres 16, limit 256 MB),
- żadnych nowych portów: aplikacja dołącza do sieci `chirpstack_default`,
  a Caddy z ChirpStacka wystawia ją pod `https://faktury.<adres-serwera>`,
- hasła w `/opt/crm/.env` i `/root/crm-dostep.txt` (tworzone przy pierwszej instalacji).

Instalacja i aktualizacja (root):

```bash
git clone <repo> /opt/crm   # albo skopiowanie katalogu
bash /opt/crm/deploy/install.sh
```

Skrypt robi najpierw kopię serwera (`/opt/sygnavo/kopia.sh`, jeśli jest), buduje
obraz, uruchamia kontenery i dopisuje blok `faktury.{$DOMAIN}` do Caddyfile ChirpStacka.

**Kopie zapasowe**: nocny `kopia.sh` z Sygnavo zrzuca wszystkie kontenery Postgres,
więc baza faktur trafia do kopii sama. Do listy plików w `kopia.sh` trzeba jeszcze
dopisać `/opt/crm` (plik `.env`), w repozytorium `lorawan`.

## Technologia

Python 3.13, FastAPI, SQLAlchemy 2, szablony Jinja2 (strony renderowane na serwerze,
bez osobnej aplikacji frontowej), WeasyPrint do PDF, PostgreSQL. Python wybrany pod
KSeF: podpisy XAdES, certyfikaty i XML (FA(3)) mają tu dojrzałe biblioteki.

## Dalsze etapy

1. Korekty, proformy, zaliczki; migracje bazy (Alembic) przed pierwszymi prawdziwymi danymi.
2. KSeF 2.0: logowanie certyfikatem KSeF (bez tokenu), wysyłka FA(3), numer KSeF
   i UPO przy fakturze, kod QR na PDF, tryb offline, pobieranie faktur zakupowych.
   Najpierw środowisko testowe MF.
3. Biała lista VAT: dane kontrahenta po NIP.
4. Poczta: wysyłka faktur z istniejącej skrzynki (SMTP), odbiór (IMAP).
5. Import z Firmao przez API (faktury jako archiwum tylko do odczytu).
6. API dla Claude („wystaw marżę dla Kowalskiego na Transita”).
