# Zasady pracy w repozytorium Sygnavo

Sygnavo jest budowane pod sprzedaż w abonamencie innym firmom. Przed każdą zmianą przeczytaj
[docs/ARCHITEKTURA-SAAS.md](docs/ARCHITEKTURA-SAAS.md). Ten plik jest obowiązujący.

Zasady obowiązkowe przy każdej zmianie:

1. Dane klienta zawsze w zakresie organizacji/firmy. Żadnych danych Margot/ARI na sztywno w kodzie.
2. Nowa tabela z danymi klienta ma `organisation_id` (gdy etap B jest wdrożony; do tego czasu `company_id`)
   i test izolacji (firma A nie widzi danych firmy B).
3. Sekrety (tokeny, hasła, certyfikaty) tylko zaszyfrowane, nigdy w logach.
4. Migracje bazy odwracalne i dwuetapowe (najpierw dodaj, potem usuń w osobnym wdrożeniu).
5. Przed każdym wdrożeniem i każdą zmianą serwera: kopia bazy. Potem zmiana, potem test zdrowia.
6. Rozbudowa serwera (CPU, RAM, dysk, nowy serwer) tylko według procedur z sekcji 5 dokumentu
   architektury, ze skryptu w `deploy/`, nigdy ręcznie „na żywo”.
7. Aplikacja nie trzyma stanu w pamięci procesu; zadania w tle idą przez kolejkę.
8. Zmiana zasad z dokumentu architektury wymaga zgody właściciela i wpisu w jego „Dzienniku decyzji”.
9. Baza wiedzy: każda nowa lub zmieniona funkcja aktualizuje `docs/wiedza/` (strona modułu, FAQ z pytań
   właściciela). To źródło dla centrum pomocy, bloga i asystenta AI dla klientów. Bez danych osobowych.
