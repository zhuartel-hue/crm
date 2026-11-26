# Sygnavo: architektura serwera i podstawy SaaS

Wersja 1, 26 listopada 2026. Zapisane na prośbę właściciela (Arkadiusz Erdmann).
Ten dokument jest **obowiązujący**: każda zmiana kodu, serwera i bazy musi być z nim zgodna.
Zmiana zasad wymaga zgody właściciela i wpisu w sekcji „Dziennik decyzji”.

## 1. Cel i koncepcja

Sygnavo budujemy najpierw dla własnych firm (Margot JDG i ARI ERDMANN WORLDS sp.), ale od pierwszego dnia
tak, żeby można je było sprzedawać w abonamencie innym firmom. Docelowo firma z zewnątrz trafia do nas
z social mediów, sama zakłada konto, rejestruje swoją firmę po NIP i pracuje tak jak my, nie widząc
niczego, co należy do innych.

Zasady nadrzędne, w tej kolejności:

1. **Bezpieczeństwo danych.** Faktury przechowujemy co najmniej 5 lat. Utrata lub wyciek danych klienta to
   najgorsze, co może się stać. Każda decyzja przegrywa z tym punktem.
2. **Prostota.** Jedna aplikacja, jedna baza, jeden serwer, dopóki to wystarcza. Mniej elementów to mniej awarii.
   Nie ma programisty na etacie, więc wszystko musi być opisane i powtarzalne skryptem.
3. **Bezpieczna rozbudowa.** Każde powiększenie serwera, pamięci, dysku czy liczby klientów musi być:
   - poprzedzone kopią,
   - odwracalne,
   - opisane krok po kroku w tym repozytorium,
   - przećwiczone, zanim zrobimy je na produkcji.
4. **Skromne koszty.** Płacimy za to, czego dziś potrzebujemy. Większe zasoby dokładamy, gdy pokażą to
   pomiary, nie na zapas.

## 2. Model danych wielu klientów (multi-tenant)

**Decyzja: jedna baza PostgreSQL, wspólne tabele, każdy rekord oznaczony organizacją.**

```
Organizacja (klient Sygnavo, płaci abonament)
 ├── Użytkownicy z rolami (właściciel, księgowość, handlowiec, magazyn/kierowca)
 ├── Abonament (plan, okres próbny, status płatności)
 └── Firmy (NIP; jedna organizacja może mieć kilka firm, np. Margot + ARI)
      └── faktury, kontrahenci, produkty, magazyn, oferty, umowy, bank, poczta, KSeF ...
```

Margot i ARI to **pierwsza organizacja**, nie wyjątek w kodzie.

Dlaczego tak, a nie osobna baza na klienta: jedna baza to jedna kopia, jedna migracja i jeden monitoring.
Osobne bazy lub schematy mnożą pracę przy każdej zmianie i przy setkach klientów stają się nie do
utrzymania bez zespołu. Model „wspólne tabele” wystarcza do tysięcy klientów.

Wymagania, bez wyjątków:

- Każda tabela z danymi klienta ma kolumnę `organisation_id` (NOT NULL, indeks).
- **Row Level Security w PostgreSQL**: baza sama odrzuca odczyt i zapis danych innej organizacji, nawet
  jeśli kod ma błąd. Aplikacja łączy się z bazą użytkownikiem bez prawa omijania RLS; migracje innym.
- Każde zapytanie w kodzie i tak filtruje po organizacji (podwójne zabezpieczenie).
- Testy automatyczne sprawdzają izolację: organizacja A nie widzi, nie zmienia i nie usuwa danych B,
  także przez API, MCP, eksporty PDF, wyszukiwarkę i zadania w tle.
- Żadnych danych konkretnej firmy w kodzie (nazwy, NIP, konta, logo). Nowa organizacja startuje pusta.
- Numeracja dokumentów, szablony, ustawienia, skrzynki, certyfikaty KSeF i połączenia bankowe są
  per firma lub per organizacja.
- Limity per organizacja (np. liczba faktur, użytkowników, miejsce na pliki), żeby jeden klient nie
  zablokował pozostałych.
- Eksport wszystkich danych organizacji i jej usunięcie (RODO), z zachowaniem dokumentów, które prawo
  każe przechowywać.

## 3. Sekrety klientów

Tokeny KSeF, certyfikaty, dostęp do banku, hasła do skrzynek, Allegro, sklepów:

- Szyfrowanie kopertowe: każda organizacja ma własny klucz danych, a ten jest zaszyfrowany kluczem
  głównym serwera. Wyciek jednego klucza nie odsłania innych klientów; klucz główny można wymienić bez
  odszyfrowywania wszystkiego ręcznie.
- Klucz główny nie trafia do repozytorium ani do kopii bazy. Jego kopia leży poza serwerem (iMac,
  menedżer haseł właściciela).
- Sekrety nigdy nie pojawiają się w logach, mailach ani odpowiedziach API.

Stan na 26.11.2026: jeden klucz wyprowadzony z `SECRET_KEY` (`app/secretbox.py`). Do zmiany w etapie
„Podstawa SaaS”.

## 4. Serwer

**Decyzja (rekomendacja, czeka na zgodę właściciela): osobny serwer dla Sygnavo, oddzielony od LoRaWAN.**

| Element | Wybór | Koszt netto / mies. (ceny Hetzner od 15.06.2026) |
| --- | --- | --- |
| Serwer | Hetzner Cloud CX33: 4 vCPU, 8 GB RAM, 80 GB NVMe, Norymberga (UE) | 8,49 € |
| Adres IPv4 | stały, publiczny | ok. 0,50 € |
| Dane bazy | Hetzner Volume (osobny dysk), np. 20–50 GB | do sprawdzenia przy zakupie, rzędu 1–3 € |
| Kopie obrazu serwera | Hetzner Backups, 7 dni | ok. 20% ceny serwera, ok. 1,70 € |
| Kopie bazy poza serwerem | Hetzner Storage Box BX11, 1 TB, Falkenstein (inne centrum) | 3,20 € |
| Monitoring | darmowe plany (sekcja 7) | 0 € |
| **Razem** | | **ok. 15–17 € netto** |

Na serwerze, wszystko w Dockerze (docker compose):

- **Caddy**: HTTPS, certyfikaty, przekierowania. Tylko porty 80 i 443 otwarte na świat.
- **app**: aplikacja Sygnavo (FastAPI), co najmniej 2 procesy.
- **worker**: osobny proces na zadania w tle (poczta, KSeF, bank, umowy cykliczne, PDF masowe).
  Zadania mają kolejkę w bazie, ponawianie i limit czasu. Awaria zadania jednej organizacji nie
  zatrzymuje innych.
- **db**: PostgreSQL 16, dane na osobnym dysku (Hetzner Volume).
- **backup**: pgBackRest (ciągły zapis zmian) i nocne zrzuty.

Dlaczego dane bazy na osobnym dysku (Volume): dysk można powiększyć bez wyłączania serwera,
a w razie awarii odpiąć i podpiąć do nowego serwera w kilka minut. Serwer staje się wymienny.
Uwaga: Hetzner Backups i migawki serwera **nie obejmują** Volume. Bazę chronią wyłącznie kopie
pgBackRest i zrzuty na Storage Box (sekcja 6), dlatego ich test odtwarzania jest obowiązkowy.

RODO: dane wyłącznie w UE (Niemcy, Finlandia). Z Hetznerem zawieramy umowę powierzenia (DPA) w panelu.
Dla klientów Sygnavo jesteśmy procesorem, Hetzner jest podprocesorem; wpisać to do regulaminu i umowy
powierzenia z klientami.

LoRaWAN zostaje na chirpstack-1 bez zmian. Kod LoRaWAN nie jest zmieniany z wątków CRM.

## 5. Bezpieczna rozbudowa: procedury

Każda procedura poniżej ma być zapisana jako skrypt w `deploy/` i przećwiczona na serwerze testowym
lub kopii, zanim zrobimy ją na produkcji. Zawsze najpierw kopia, potem zmiana, potem sprawdzenie.

### 5.1 Więcej mocy (CPU, RAM)

1. Kopia bazy na Storage Box + migawka (snapshot) serwera w Hetznerze.
2. Komunikat dla klientów o krótkiej przerwie (strona statusu), w nocy.
3. Wyłączenie serwera, w panelu „Rescale” z opcją **„CPU and RAM only”** (dysk bez zmian, więc można
   wrócić do mniejszego typu), włączenie. Przerwa zwykle kilka minut.
4. Automatyczny test: strona odpowiada, logowanie działa, liczba faktur zgadza się z kopią.
5. Jeśli coś nie działa: rescale z powrotem do poprzedniego typu albo odtworzenie z migawki.

### 5.2 Więcej miejsca na dane

Powiększenie Volume w panelu Hetznera i `resize2fs`, bez wyłączania serwera, po kopii.
Volume można tylko powiększać, więc rośniemy małymi krokami (np. +20 GB), gdy zajętość przekroczy 70%.

### 5.3 Nowy serwer (przeprowadzka, awaria, duża zmiana)

Serwer stawiamy zawsze skryptem `deploy/` z repozytorium, nigdy ręcznie. Kolejność:

1. Nowy serwer ze skryptu, równolegle ze starym.
2. Odtworzenie bazy z kopii i porównanie liczby rekordów.
3. Krótka przerwa: ostatnia synchronizacja, przełączenie DNS (niski TTL ustawiony dzień wcześniej).
4. Stary serwer zostaje włączony co najmniej 7 dni jako zapas, potem kasujemy.

### 5.4 Progi rozbudowy (decyzja po pomiarach, nie na zapas)

| Klienci (organizacje) | Układ | Szac. koszt netto / mies. |
| --- | --- | --- |
| 1–50 | 1 serwer CX33: app + worker + baza | ok. 15–17 € |
| 50–300 | ten sam serwer powiększony do CX43 (8 vCPU, 16 GB) | ok. 25 € |
| 300–1000 | osobny serwer bazy + 2 serwery aplikacji za load balancerem, pliki w Object Storage | ok. 60–80 € |
| 1000+ | replika bazy, druga lokalizacja, rozważyć zarządzaną bazę | od ok. 150 € |

Sygnał do rozbudowy: przez tydzień CPU powyżej 60% lub RAM powyżej 75% w godzinach pracy,
albo czas odpowiedzi strony powyżej 1 s. Mierzy to monitoring.

Kod piszemy już teraz tak, żeby kolejne progi nie wymagały przepisywania: aplikacja nie trzyma stanu
w pamięci procesu ani w plikach lokalnych poza wydzielonym katalogiem na pliki (później Object Storage),
sesje i kolejka są w bazie.

## 6. Kopie zapasowe i odtwarzanie

| Kopia | Częstotliwość | Miejsce | Przechowywanie |
| --- | --- | --- | --- |
| Zrzut bazy (pg_dump), zaszyfrowany, restic | co godzinę i przed każdym wdrożeniem; strata max 1 h | Storage Box (inne centrum danych) | 48 godzinowych, 30 dziennych, 12 miesięcznych, 6 rocznych |
| Ten sam zrzut | co godzinę | dysk systemowy serwera (nie Volume) | 48 ostatnich + 10 sprzed wdrożeń |
| Ten sam zrzut | co noc | iMac `~/kopie-faktur` | 60 dni |
| Obraz serwera (bez Volume) | co noc | Hetzner Backups | 7 dni |

Wszystkie dane programu (także zdjęcia produktów, logo, załączniki maili) są w bazie, więc zrzut bazy
jest pełną kopią. Skrypty: `deploy/serwer/kopia.sh`, `deploy/serwer/test-odtwarzania.sh`.

Dlaczego zrzut co godzinę, a nie ciągły zapis zmian (WAL): to jedno proste narzędzie zamiast dwóch,
łatwe do sprawdzenia i odtworzenia. Ciągły zapis (pgBackRest, strata max kilka minut) dokładamy,
gdy pojawią się klienci zewnętrzni (etap E).

- Kopie na Storage Box szyfruje restic. Hasło jest na serwerze (żeby kopie mogły się robić same)
  i **obowiązkowo także poza serwerem** (menedżer haseł właściciela, iMac). Bez niego kopii nie da się odczytać.
- **Test odtwarzania co tydzień automatycznie**: odtworzenie ostatniej kopii w osobnym kontenerze,
  porównanie liczby faktur, kontrahentów i produktów z produkcją; wynik do monitoringu, błąd = alarm.
- Raz na kwartał pełne odtworzenie na nowym serwerze z pomiarem czasu. Cel: pełne przywrócenie w 1 h.
- Kopia, której odtworzenia nie sprawdzono, nie liczy się jako kopia.

## 7. Monitoring i alarmy

- Dostępność strony co minutę, publiczna strona statusu (UptimeRobot lub Better Stack, plan darmowy).
- Błędy aplikacji (Sentry, region UE, plan darmowy).
- Kopie i test odtwarzania zgłaszają się do healthchecks.io; brak zgłoszenia w terminie = alarm.
- Alarmy: dysk > 80%, RAM, CPU, nieudana kopia, nieudany test odtwarzania, wygasający certyfikat
  HTTPS lub KSeF, zerwane połączenie z bankiem, zablokowana kolejka zadań.
- Alarmy idą na info@margotsystem.com i jako powiadomienie na telefon.

## 8. Wdrożenia (bez iMaca)

1. Każda zmiana przez pull request; GitHub uruchamia testy (w tym testy izolacji organizacji).
2. Po scaleniu GitHub Actions buduje obraz i zapisuje w GitHub Container Registry.
3. Wdrożenie: kopia bazy, pobranie obrazu, migracje, restart, test zdrowia (`/health`).
   Brak odpowiedzi = automatyczny powrót do poprzedniego obrazu.
4. Gdy będą klienci zewnętrzni: wdrożenie produkcyjne wymaga zatwierdzenia w GitHubie (jeden przycisk).
5. Migracje bazy zawsze w dwóch krokach (najpierw dodaj, potem usuń stare, w osobnym wdrożeniu), żeby
   poprzednia wersja aplikacji działała na nowej bazie i powrót był możliwy.
6. Klucz SSH do wdrożeń ma uprawnienia tylko do wdrożeń; sekrety w GitHub Secrets.

Do czasu przejścia: wdrożenia jak dotąd przez iMaca, zawsze z kopią przed zmianą.

## 9. Bezpieczeństwo

- Serwer: logowanie tylko kluczem SSH, brak logowania hasłem, firewall Hetznera (80, 443, SSH z
  ograniczeniem), automatyczne łatki bezpieczeństwa, fail2ban.
- Użytkownicy: hasła argon2, weryfikacja adresu e-mail, logowanie dwuskładnikowe (obowiązkowe dla
  właściciela organizacji), blokada po nieudanych próbach, wylogowanie zdalne.
- Rejestracja z internetu: CAPTCHA, limity zapytań, okres próbny z limitami, ręczna blokada nadużyć
  w panelu administratora.
- Maile systemowe (rejestracja, reset hasła, alarmy) przez dostawcę maili transakcyjnych w UE
  z SPF/DKIM/DMARC, żeby nie trafiały do spamu.
- Dziennik zdarzeń per organizacja: kto, co i kiedy zmienił.
- Raz na kwartał przegląd zależności (aktualizacje bibliotek z łatkami bezpieczeństwa).

## 10. Abonamenty i płatności

- Rekomendacja: **Stripe** (abonamenty, karty, BLIK, Przelewy24, ponawianie nieudanych płatności,
  gotowy panel klienta). Przelewy24 bezpośrednio jest tańsze, ale abonamenty trzeba by pisać samemu.
- Faktury za abonament wystawia samo Sygnavo z firmy sprzedającej, z wysyłką do KSeF.
- Plany i moduły włączane per organizacja (np. moduł „Auta” tylko dla zainteresowanych).
- Brak płatności: przypomnienia, potem tryb tylko do odczytu, nigdy natychmiastowe usunięcie danych.

Otwarte: która firma (Margot czy ARI) sprzedaje Sygnavo; nazwa i domena produktu; regulamin,
polityka prywatności i umowa powierzenia (do sprawdzenia przez prawnika).

## 10a. Wiedza, pomoc i asystent AI

Wszystko, co przechodzimy przy budowie i używaniu Sygnavo, zapisujemy w `docs/wiedza/` (strony modułów i FAQ).
Z tego powstanie centrum pomocy z forum, treści na bloga i social media oraz asystent AI w programie,
który odpowiada klientom na podstawie tej wiedzy (wyszukiwanie w bazie wiedzy, odpowiedzi tylko z niej,
bez dostępu asystenta do danych innych organizacji). Asystent korzysta z istniejącego serwera MCP (`/mcp`)
tylko w zakresie organizacji zalogowanego użytkownika.

## 11. Kolejność prac

| Etap | Zakres | Termin |
| --- | --- | --- |
| A. Serwer | osobny CX33 + Volume + Storage Box, kopie, test odtwarzania, monitoring | przeprowadzka 1.12.2026 |
| B. Podstawa SaaS | organizacje, `organisation_id` + RLS, użytkownicy i role, zaproszenia, 2FA, worker, koniec danych Margot w kodzie, szyfrowanie per organizacja | do 31.12.2026 |
| C. Start własny | Margot i ARI pracują produkcyjnie na nowym serwerze | 1.01.2027 |
| D. Wdrożenia z GitHuba | CI/CD, automatyczny powrót, serwer testowy | styczeń 2027 |
| E. Sprzedaż | rejestracja po NIP, okres próbny, plany, Stripe, regulamin, domena, panel administratora, strona statusu | styczeń–luty 2027 |

## 12. Dziennik decyzji

| Data | Decyzja | Kto |
| --- | --- | --- |
| 25.11.2026 | Sygnavo będzie sprzedawane innym firmom; nowy kod pisany „pod wielu klientów” | właściciel |
| 25.11.2026 | Najpierw testy wewnętrzne, platforma sprzedaży później | właściciel |
| 26.11.2026 | Bezpieczna, prosta i odwracalna rozbudowa serwera to priorytet; zasady zapisane w repozytorium (ten plik) | właściciel |
| 26.11.2026 | Od teraz dokumentujemy procesy w bazie wiedzy (`docs/wiedza/`) pod forum, blog i asystenta AI | właściciel |
| 26.11.2026 | Rekomendacja: osobny serwer Hetzner CX33 + Volume + Storage Box; czeka na zgodę | Claude |
