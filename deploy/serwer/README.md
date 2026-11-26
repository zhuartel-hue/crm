# Serwer Sygnavo (sygnavo-1): instalacja, przeprowadzka, rozbudowa

Zasady: [docs/ARCHITEKTURA-SAAS.md](../../docs/ARCHITEKTURA-SAAS.md). Każda czynność: najpierw kopia,
potem zmiana, potem sprawdzenie. Nic nie robimy ręcznie „na żywo” poza tym, co tu opisane.

| Plik | Do czego |
| --- | --- |
| `przygotuj-serwer.sh` | jednorazowo na nowym serwerze: łatki, Docker, zapora, SSH tylko kluczem, Volume na bazę |
| `zainstaluj.sh` | instalacja i każda aktualizacja programu (kopia przed zmianą, test zdrowia) |
| `kopia.sh` | kopia bazy: lokalnie + zaszyfrowana na Storage Box (co godzinę) |
| `test-odtwarzania.sh` | co tydzień odtwarza kopię w osobnej bazie i porównuje z produkcją |
| `przenies.sh` | uruchamiany na iMacu: przeniesienie danych ze starego serwera (próba lub końcowe) |
| `docker-compose.yml`, `Caddyfile`, `systemd/` | konfiguracja usług |

## 1. Zakup (właściciel, z telefonu)

Hetzner Cloud, projekt z chirpstack-1: serwer **CX33**, Nuremberg, Ubuntu 24.04, ten sam klucz SSH,
**Volume 20 GB** (`sygnavo-db`, Automatically, ext4), **Backups** włączone, nazwa `sygnavo-1`.
Storage Box **BX11**, Falkenstein. Umowa powierzenia (DPA) zaakceptowana w panelu.

## 2. Przygotowanie i próba (Claude przez iMac)

1. Na iMacu w `~/.ssh/config` dopisać host `sygnavo-1` (IP nowego serwera, użytkownik root).
2. Skopiować ten katalog na serwer i uruchomić przygotowanie z adresem tymczasowym
   (`<ip-z-kreskami>.sslip.io`, np. `1-2-3-4.sslip.io`), żeby próba nie ruszała prawdziwej domeny:
   `ssh sygnavo-1 'bash /opt/sygnavo-serwer/przygotuj-serwer.sh 1-2-3-4.sslip.io'`
   (pierwszy raz katalog kopiuje `przenies.sh`; można go też skopiować `scp -r`).
3. `bash przenies.sh proba` na iMacu. Skrypt kopiuje kod, `.env` (ten sam SECRET_KEY), bazę,
   porównuje liczbę faktur, kontrahentów, produktów i zdjęć i uruchamia program.
4. Właściciel sprawdza program pod adresem tymczasowym (logowanie, faktury, PDF, KSeF test, bank).

## 3. Kopie na Storage Box (raz)

1. Na serwerze: `ssh-keygen -t ed25519 -N '' -f /root/.ssh/storagebox`.
2. Klucz publiczny dodać do Storage Box (konsola Hetzner, Storage Box, SSH keys) i włączyć SSH.
3. `/root/.ssh/config`: `Host storagebox` → `HostName uXXXXXX.your-storagebox.de`, `User uXXXXXX`,
   `Port 23`, `IdentityFile /root/.ssh/storagebox`.
4. Hasło kopii: `openssl rand -base64 32 > /etc/sygnavo/restic-haslo && chmod 600 /etc/sygnavo/restic-haslo`.
   **To hasło zapisać poza serwerem** (menedżer haseł właściciela i iMac). Bez niego kopii nie da się odczytać.
5. W `/etc/sygnavo/serwer.env`: `RESTIC_REPOSITORY=sftp:storagebox:/sygnavo`, potem
   `set -a; . /etc/sygnavo/serwer.env; set +a; restic init`.
6. Konta [healthchecks.io](https://healthchecks.io) (darmowe): dwa czeki „kopia” (co 1 h, grace 2 h)
   i „test odtwarzania” (co 7 dni, grace 1 dzień), adresy do `HC_KOPIA_URL` i `HC_TEST_URL`.
   Brak sygnału = mail na info@margotsystem.com.
7. `bash zainstaluj.sh` (włącza harmonogram), potem ręcznie `bash kopia.sh` i `bash test-odtwarzania.sh`.

## 4. Przeprowadzka (wieczorem, ok. 15 min przerwy)

1. Dzień wcześniej: w seohost.pl TTL rekordu `crm.margotsystem.com` na 300 s.
2. `ssh sygnavo-1 'bash /opt/sygnavo-serwer/przygotuj-serwer.sh crm.margotsystem.com'` (zmienia tylko adres).
3. `bash przenies.sh koncowe` na iMacu (zatrzymuje stary program, kopiuje, porównuje, uruchamia nowy).
4. Rekord A `crm.margotsystem.com` w seohost.pl → IP sygnavo-1. Caddy sam pobierze certyfikat.
5. Sprawdzić: logowanie, lista faktur, PDF, konektor Claude (`/mcp`), KSeF, bank, poczta.
6. Na iMacu w `~/kopie-faktur/kopia-faktur.sh` zmienić host z `chirpstack-1` na `sygnavo-1`.
7. Stary program na chirpstack-1 zostaje zatrzymany (nie usunięty) co najmniej 7 dni.

**Powrót w razie problemu:** rekord A z powrotem na stary IP i
`ssh chirpstack-1 'cd /opt/crm && docker compose start app'`. Dane z nowego serwera z czasu po
przeprowadzce trzeba wtedy przenieść z powrotem (`pg_dump` z sygnavo-1).

### Wariant bez czekania na DNS (użyty 26.11.2026)

Gdy właściciel nie może od razu zmienić DNS, stary serwer przekazuje ruch na nowy:

1. Na sygnavo-1 w `/etc/sygnavo/serwer.env`: `APP_ALIASES=", 188-245-222-137.sslip.io"` i
   ponowne `zainstaluj.sh` (Caddy obsługuje wtedy oba adresy; certyfikat dla domeny głównej pobierze
   sam po zmianie DNS).
2. Na chirpstack-1 blok `crm.margotsystem.com` w `/opt/chirpstack/Caddyfile` (kopia przed zmianą obok):
   `reverse_proxy https://188-245-222-137.sslip.io { header_up Host 188-245-222-137.sslip.io }`,
   potem `caddy validate` i łagodne przeładowanie. Pozostałych bloków (LoRaWAN) nie ruszamy.
3. Po zmianie DNS: usunąć ten blok ze starego serwera, a `APP_ALIASES` zostawić pusty.

Uwaga: bez `APP_ALIASES` w `serwer.env` kolejne wdrożenie usunie adres sslip z Caddy i przekazywanie
przestanie działać, dopóki DNS nie wskazuje nowego serwera.

## 5. Rozbudowa

### Więcej CPU / RAM
1. `bash kopia.sh przed-rescale` i migawka serwera w konsoli (Snapshots).
2. Konsola: serwer → Power off → Rescale → nowy typ, **zaznaczone „CPU and RAM only”** (dysk bez zmian,
   można wrócić do mniejszego typu) → Power on.
3. Sprawdzić `https://<domena>/health` i logowanie. Jeśli źle: Rescale z powrotem.
4. Po zmianie RAM dopasować `mem_limit` i `shared_buffers` w `docker-compose.yml` (PR) i `zainstaluj.sh`.

### Osobny proces zadań w tle (przed kilkoma procesami aplikacji)

1. Kopia: `bash /opt/sygnavo-serwer/kopia.sh przed-workerem`.
2. W `/opt/crm/.env` dopisać `BACKGROUND_JOBS=off`.
3. `docker compose --profile worker up -d` (z katalogu `/opt/sygnavo-serwer`, przez `dc` z `wspolne.sh`).
4. Sprawdzić `docker compose logs worker`: „Worker działa”.
Powrót: usunąć `BACKGROUND_JOBS=off`, `docker compose stop worker`, restart aplikacji. Nawet gdyby
zadania chwilowo działały w obu procesach, blokada w bazie nie pozwoli wykonać ich dwa razy naraz.

### Więcej miejsca na bazę (bez wyłączania)
1. `bash kopia.sh przed-volume`.
2. Konsola: Volumes → `sygnavo-db` → Resize (np. +20 GB; zmniejszyć się nie da).
3. Na serwerze: `resize2fs /dev/disk/by-id/scsi-0HC_Volume_<id>`, sprawdzić `df -h`.

### Awaria serwera
Nowy serwer (punkt 1), `przygotuj-serwer.sh`, Volume odpiąć od starego i podpiąć do nowego
(albo odtworzyć bazę ze Storage Box: `restic restore latest`), `.env` z kopii (`env-aplikacji`), `zainstaluj.sh`, DNS.
