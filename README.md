# Sygnavo: faktury i CRM dla Margot i ARI ERDMANN WORLDS

Sygnavo to własny program do fakturowania dla autokomisu, zamiast Firmao. Dwie firmy w jednym
miejscu, faktury VAT (także na sprzedaż aut), PDF. Kolejne etapy: KSeF 2.0,
biała lista VAT, wysyłka mailem, przeniesienie danych z Firmao.

## Etap 1 (ten kod)

- **Dwie firmy**, przełączane u góry ekranu. Każda ma własne dane, konto bankowe
  i numerację. Kontrahenci są wspólni dla obu firm, auta i faktury są osobne.
- **Faktura VAT**: stawki 23, 8, 5, 0, zw, np; ceny wpisywane brutto albo netto;
  zestawienie wg stawek, kwota słownie.
- Faktury VAT marża nie ma (usunięta 2026-11-25, firmy z niej nie korzystają);
  jej kolumny zostały w bazie, ale program ich nie używa.
- **Auta na placu**: marka, model, VIN, rocznik, przebieg, nr rej., cena nabycia,
  cena sprzedaży. „Sprzedaj” wypełnia fakturę VAT danymi auta, a po wystawieniu
  auto dostaje status „Sprzedany”.
- **Numeracja** bez dziur, osobno dla każdej firmy, od 1 co miesiąc
  albo co rok; wzór do ustawienia (domyślnie `FV/{nr}/{MM}/{RRRR}`).
- **Szkic → wystawienie**: numer nadaje się przy wystawieniu. Wystawionej faktury
  nie można zmienić ani usunąć (zmiany przez korektę w kolejnym etapie, potem KSeF).
- PDF (A4, polskie znaki), logowanie hasłem, wygodne na iPhonie.

Dane obu firm (nazwa, NIP, adres, konto) są wpisane z białej listy VAT; zmienia się je w Ustawieniach.

## Etap 2: konektor dla Claude

Program ma serwer MCP pod `https://faktury.<adres-serwera>/mcp`. Podłączenie (raz):
w Claude **Ustawienia → Konektory → Dodaj własny konektor**, adres jak wyżej, **Połącz**,
zalogowanie do programu i zaznaczenie uprawnień. Instrukcja jest też w programie, zakładka **Claude**.

- **Narzędzia**: firmy, szukanie kontrahentów i aut, nowy kontrahent i auto, lista i szczegóły
  faktur, szkic faktury VAT (z auta jednym parametrem `car_id`), wystawienie,
  płatność, podsumowanie miesiąca.
- **Uprawnienia** wybierane przy łączeniu: `read` (odczyt), `write` (szkice, auta, kontrahenci,
  płatności), `issue` (wystawianie). Domyślnie wystawianie jest wyłączone: Claude robi szkic,
  numer nadajesz w programie. Z `issue` Claude i tak pyta o potwierdzenie przed wystawieniem.
- **Logowanie**: OAuth 2.1 z PKCE i dynamiczną rejestracją klienta; adresy powrotu tylko
  `claude.ai`/`claude.com` (zmienna `OAUTH_REDIRECT_HOSTS`) i localhost. Token dostępu ważny
  1 godzinę, odświeżania 60 dni, z rotacją: ponowne użycie starego tokenu odcina całe połączenie.
  W bazie są tylko skróty SHA-256 tokenów.
- **Cofnięcie dostępu**: zakładka **Claude → Aktywne połączenia → Cofnij**.
- **Dziennik**: każda zmiana z programu, API i Claude (kto, co, kiedy), zakładka **Dziennik**.
- **REST API** `/api/v1/...` (te same operacje, nagłówek `Authorization: Bearer <klucz>`);
  klucz tworzy się w zakładce **Claude**.

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

1. (Etap 2, gotowe) Konektor dla Claude: MCP + OAuth + REST API + dziennik.
1. Korekty, proformy, zaliczki; migracje bazy (Alembic) przed pierwszymi prawdziwymi danymi.
2. KSeF 2.0: logowanie certyfikatem KSeF (bez tokenu), wysyłka FA(3), numer KSeF
   i UPO przy fakturze, kod QR na PDF, tryb offline, pobieranie faktur zakupowych.
   Najpierw środowisko testowe MF.
3. Biała lista VAT: dane kontrahenta po NIP.
4. Poczta: wysyłka faktur z istniejącej skrzynki (SMTP), odbiór (IMAP).
5. Import z Firmao przez API (faktury jako archiwum tylko do odczytu).
6. Moduły CRM: kontakty, notatki, zadania, szanse sprzedaży, oferty.
