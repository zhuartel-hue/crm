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
- **Rodzaje faktur**: VAT, proforma (bez skutków podatkowych, nie liczy się do sprzedaży),
  zaliczkowa (całe zamówienie + otrzymana zaliczka, VAT zaliczki proporcjonalnie do stawek),
  końcowa (zamówienie minus wszystkie nierozliczone zaliczki nabywcy), korygująca
  (pozycje przed i po, różnica wg stawek, przyczyna korekty). Z proformy jednym kliknięciem
  powstaje faktura VAT, z zaliczkowej końcowa, z VAT/końcowej korekta. Osobne serie:
  `FV` (VAT i końcowe), `PRO`, `FZ`, `FK`; wzory w Ustawieniach.
- **Produkty**: katalog wspólny dla obu firm (kod, nazwa, jm., cena netto/brutto, VAT, EAN,
  opis, zdjęcia). Zdjęcia są w bazie (JPEG max 1600 px + miniatura), więc trafiają do nocnej
  kopii. Na fakturze wpisz kod albo nazwę i wybierz produkt z listy. Claude: `search_products`,
  a w `create_invoice_draft` pozycja może mieć `product_id`; REST: `GET /api/v1/products`.
- **Konto**: zmiana hasła w aplikacji (skrót scrypt w bazie; od tej chwili hasło z `.env`
  przestaje działać).
- **Numeracja** bez dziur, osobno dla każdej firmy, od 1 co miesiąc
  albo co rok; wzór do ustawienia (domyślnie `FV/{nr}/{MM}/{RRRR}`).
- **Szkic → wystawienie**: numer nadaje się przy wystawieniu. Wystawionej faktury
  nie można zmienić ani usunąć (zmiany przez korektę w kolejnym etapie, potem KSeF).
- Usuwanie auta i kontrahenta, jeśli nie ma wystawionych faktur (szkice tracą tylko powiązanie).
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
  klucz tworzy się w zakładce **Claude**. Korekta / VAT z proformy / końcowa:
  `POST /api/v1/invoices/{id}/correction` (`reason`, opcjonalnie `items` po korekcie),
  `/from-proforma`, `/final`. Te same operacje w MCP: `create_correction_draft`,
  `create_invoice_from_proforma`, `create_final_invoice_draft`; `create_invoice_draft`
  przyjmuje `kind` (`vat`, `proforma`, `advance` + `advance_gross`).

## Bank: wpłaty z PKO BP

Zakładka **Bank** wczytuje wyciągi MT940 z iPKO (Moje sprawy → Raporty → MT940 → „Włącz” przy
rachunku firmowym; raporty są bezpłatne). Firma jest rozpoznawana po numerze rachunku z Ustawień.
Wpłata z numerem faktury w tytule albo z kwotą i nadawcą pasującymi do jednej faktury księguje się
sama (pole „Zapłacono”); pozostałe czekają w „Do sprawdzenia” z propozycją albo do ręcznego
przypisania. Ten sam plik można wczytać wiele razy, powtórzone operacje są pomijane.
Format: specyfikacja PKO „Struktura pliku wyjściowego - Raport MT940” (CP852, podpola `~NN`).

## Poczta

Zakładka **Poczta**: skrzynki IMAP/SMTP przypisane do firm (Ustawienia → Skrzynki pocztowe).
Sygnavo pobiera INBOX i folder wysłanych co `MAIL_SYNC_MINUTES` minut (domyślnie 5; 0 wyłącza),
tylko do odczytu, nic nie usuwa na serwerze poczty. Wiadomość łączy się z kontrahentem po adresie
e-mail i z fakturą po numerze w temacie. Fakturę można wysłać z PDF-em przyciskiem „Wyślij e-mailem”.
Hasła skrzynek są zaszyfrowane kluczem z `SECRET_KEY`. HTML wiadomości jest wyświetlany w ramce
sandbox z CSP (bez skryptów i bez obrazków z internetu).

## WZ i aplikacja na telefon

Zakładka **WZ**: wydanie towaru (z faktury przyciskiem „Wystaw WZ” albo od zera, z listą produktów),
numeracja `WZ/{nr}/{MM}/{RRRR}` (Ustawienia), podpis odbiorcy palcem na telefonie, PDF z podpisem.
Sygnavo można dodać do ekranu początkowego telefonu (Safari → Udostępnij → „Do ekranu początkowego”):
otwiera się wtedy jak aplikacja, z ikoną. Service worker niczego nie zapisuje offline.

Zakładka **Magazyn** (jak w Firmao): WZ i PZ. Pozycje mogą mieć cenę i VAT, są przyciski „+ Dojazd (km)”
i „+ Roboczogodziny”; z wystawionej WZ przycisk „Wystaw fakturę z WZ” tworzy szkic faktury VAT.
Wystawienie WZ zmniejsza, a PZ zwiększa stan produktu z katalogu (usługi nie mają stanu). Stan i historię ruchów
widać na karcie produktu; tam też ręczna korekta stanu (np. inwentaryzacja). W numeracji `{NNN}` daje numer
z zerami (`{RRRR}/{MM}/{NNN}/WZ` → `2027/02/001/WZ`, jak w Firmao).

## Należności i koszty

Zakładka **Należności**: nieopłacone faktury (wszystkie, po terminie, z terminem w ciągu 7 dni), suma i liczba dni po terminie.
Wpłatę, także częściową, zapisujesz z listy albo z faktury (kwota, data, forma). Wpłaty z banku (MT940) liczą się same.
„Przypomnij” otwiera gotowy e-mail z kwotą do zapłaty, terminem i numerem konta. Po wysłaniu licznik przypomnień rośnie.

Zakładka **Koszty**: faktury, na których firma jest nabywcą, pobierane z KSeF co `KSEF_PURCHASE_SYNC_HOURS` godzin
(domyślnie 6) albo przyciskiem „Pobierz z KSeF”. Każda czeka na weryfikację (zatwierdź / odrzuć); można ją oznaczyć
jako zapłaconą i pobrać oryginalny XML. Działa dla firm z włączonym KSeF (certyfikat albo token).

## Faktury cykliczne i szablony maili

Zakładka **Faktury cykliczne**: abonamenty i umowy (co miesiąc, kwartał, pół roku, rok; wybrany dzień miesiąca, koniec umowy).
W dniu terminu program tworzy szkic, wystawia fakturę albo wystawia i wysyła ją mailem (sprawdza co `RECURRING_CHECK_MINUTES`,
domyślnie 60). W nazwach pozycji działają `{okres}`, `{miesiac}`, `{rok}`. Cykliczną robi się też z faktury: „Powtarzaj cyklicznie”.

**Szablony maili** (menu System): treści do wysyłki faktury, przypomnienia, WZ i faktury cyklicznej, ze znacznikami
(`{numer}`, `{kwota}`, `{do_zaplaty}`, `{termin}`...) i podglądem na żywo. Przy wysyłce można wybrać inny szablon.

## Bank automatycznie (Enable Banking)

Bank → „Podłącz bank (automatycznie)”. Sygnavo łączy się z bankiem przez licencjonowanego pośrednika PSD2
(Enable Banking, AISP nadzorowany przez FIN-FSA): tylko odczyt sald i operacji, bez przelewów.

- Jedna aplikacja Enable Banking na cały program: identyfikator i klucz `.pem` wgrywane na stronie `/bank/polaczenie`,
  klucz zapisany zaszyfrowany (`app_settings`). Każda firma podłącza swoje rachunki osobno (`bank_connections`).
- Zgoda w banku ważna tyle, ile pozwala bank (zwykle ~180 dni). Na 14 dni przed końcem przypomnienie na Start i w Banku;
  odnowienie to ponowne „Podłącz” tego samego banku (stare połączenie zamyka się samo).
- Operacje zaksięgowane (BOOK) pobierane w tle co `BANK_SYNC_HOURS` (domyślnie 6; banki pozwalają na ok. 4 pobrania dziennie)
  i od razu dopasowywane do faktur tymi samymi zasadami co MT940. Powtórki odrzuca odcisk operacji.
- Bez umowy z Enable Banking aplikacja działa w trybie „restricted”: tylko rachunki podłączone w ich panelu
  („Activate by linking accounts”). Do sprzedaży Sygnavo innym firmom potrzebna umowa.
- Publiczne strony `/polityka-prywatnosci` i `/regulamin` (wymagane przy rejestracji aplikacji).
- Wczytywanie MT940 zostaje jako zapas.

## Oferty

Menu **Oferty** (`/oferty`): oferta z pozycjami jak na WZ (produkty z katalogu, dojazd w km, roboczogodziny), ceny
netto albo brutto, data ważności, szansa w %, osoba kontaktowa i notatka wewnętrzna. Numer nadaje się przy pierwszym
zapisie wg wzoru z Ustawień (domyślnie `OF/{nr}/{MM}/{RRRR}`).

- Statusy jak w Firmao: nowa, wysłana, przyjęta, w realizacji, zrealizowana, odrzucona, anulowana.
- **Wyślij mailem**: szablon „Wysyłka oferty”, PDF w załączniku; po wysłaniu nowa oferta dostaje status „wysłana”.
- **Faktura z oferty** i **WZ z oferty** tworzą szkice do sprawdzenia; oferta przechodzi w „przyjęta”.
- **Kopiuj jako nową**: ta sama treść, dzisiejsza data.
- Na Start przypomnienie o otwartych ofertach, którym za 3 dni lub mniej kończy się ważność (albo już minęła).
- Na karcie kontrahenta lista jego ofert i przycisk „+ Oferta”.

## Import z Firmao

Menu System → **Import z Firmao** (`/import/firmao`): wgraj `customers.json` i `products.json` z eksportu
(`tools/firmao_export.py`, tylko odczyt z API Firmao). Rekordy pamiętają swoje id z Firmao (`source_ref`), więc
ponowny import aktualizuje zamiast dublować; istniejącego kontrahenta dopasowuje po NIP, produkt po kodzie.

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

Własny adres (np. `crm.margotsystem.com`): rekord DNS A na IP serwera, w `/opt/crm/.env` linia
`APP_DOMAIN=crm.margotsystem.com` i ponownie `install.sh`. Skrypt ustawia `PUBLIC_URL`, blok w Caddyfile
(oznaczony `# >>> crm` … `# <<< crm`) i przekierowanie ze starego `faktury.<adres-serwera>`. Konektor Claude
trzeba potem podłączyć raz pod nowym adresem `/mcp`.

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
1. (Gotowe) Korekty, proformy, zaliczki; migracje bazy (Alembic, `migrations/`, uruchamiane
   automatycznie przy starcie; stara baza bez `alembic_version` dostaje stempel `0001`).
2. KSeF 2.0: logowanie certyfikatem KSeF (bez tokenu), wysyłka FA(3), numer KSeF
   i UPO przy fakturze, kod QR na PDF, tryb offline, pobieranie faktur zakupowych.
   Najpierw środowisko testowe MF.
3. Biała lista VAT: dane kontrahenta po NIP.
4. Poczta: wysyłka faktur z istniejącej skrzynki (SMTP), odbiór (IMAP).
5. Import z Firmao przez API (faktury jako archiwum tylko do odczytu).
6. Moduły CRM: kontakty, notatki, zadania, szanse sprzedaży, oferty.
