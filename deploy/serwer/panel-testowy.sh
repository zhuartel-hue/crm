#!/usr/bin/env bash
# Panel testowy Sygnavo: wgrywa wskazany kod na adres TEST_DOMAIN (np. test.sygnavo.com) ze swieza kopia
# danych z produkcji. Produkcji nie zmienia (tylko ja czyta: pg_dump).
# Uzycie (root):  bash panel-testowy.sh <katalog-z-kodem> [wersja]
#                 bash panel-testowy.sh --wylacz          (zatrzymuje panel testowy, dane testowe zostaja)
# Zasady (docs/ARCHITEKTURA-SAAS.md, 8.1): kopia danych, ale bez maili, KSeF, banku i zadan w tle
# (TRYB_TESTOWY=1 w kodzie + usuniete z kopii hasla, tokeny i polaczenia z bankiem). Osobny SECRET_KEY,
# wiec nic zaszyfrowanego z produkcji nie da sie tu odczytac.
source "$(dirname "$0")/wspolne.sh"
[ "$(id -u)" -eq 0 ] || blad "Uruchom jako root."
TEST_DIR="${TEST_DIR:-/opt/crm-test}"
STAN=/var/lib/sygnavo/stan
tdc() { TEST_DIR="$TEST_DIR" docker compose -f "$SERWER_DIR/docker-compose.test.yml" --env-file /etc/sygnavo/test.env "$@"; }

if [ "${1:-}" = "--wylacz" ]; then
  tdc stop test-app test-db
  echo '{"stan":"wylaczony"}' > "$STAN/test.json"
  log "Panel testowy zatrzymany."
  exit 0
fi

ZRODLO="${1:?Podaj katalog z kodem}"
WERSJA="${2:-$(cat "$ZRODLO/WERSJA" 2>/dev/null || echo '?')}"
[ -n "${TEST_DOMAIN:-}" ] || blad "Brak TEST_DOMAIN w /etc/sygnavo/serwer.env (np. test.188-245-222-137.sslip.io)."
[ -f "$ZRODLO/Dockerfile" ] || blad "W $ZRODLO nie ma kodu aplikacji."
mkdir -p "$STAN"

# Ustawienia panelu testowego: tworzone raz, wlasne losowe sekrety (nigdy te z produkcji).
if [ ! -f /etc/sygnavo/test.env ]; then
  log "Tworze /etc/sygnavo/test.env (wlasne sekrety panelu testowego)"
  umask 077
  cat > /etc/sygnavo/test.env <<EOT
SECRET_KEY=$(openssl rand -hex 32)
TEST_DB_PASSWORD=$(openssl rand -hex 16)
ADMIN_PASSWORD=$(openssl rand -hex 16)
EOT
fi
set -a; . /etc/sygnavo/test.env; set +a
sed -i '/^PUBLIC_URL=/d;/^APP_DOMAIN=/d;/^APP_VERSION=/d;/^TRYB_TESTOWY=/d' /etc/sygnavo/test.env
{ echo "PUBLIC_URL=https://$TEST_DOMAIN"; echo "APP_DOMAIN=$TEST_DOMAIN"; echo "APP_VERSION=$WERSJA"; echo "TRYB_TESTOWY=1"; } >> /etc/sygnavo/test.env
chmod 600 /etc/sygnavo/test.env

log "Kod panelu testowego: $WERSJA"
if [ "$(readlink -f "$ZRODLO")" != "$(readlink -f "$TEST_DIR")" ]; then
  rm -rf "$TEST_DIR.nowy" && cp -a "$ZRODLO" "$TEST_DIR.nowy"
  rm -rf "$TEST_DIR" && mv "$TEST_DIR.nowy" "$TEST_DIR"
fi
tdc build test-app

log "Swieza kopia danych z produkcji"
tdc stop test-app >/dev/null 2>&1 || true
tdc up -d test-db
for _ in $(seq 1 60); do tdc exec -T test-db pg_isready -U crm >/dev/null 2>&1 && break; sleep 2; done
tdc exec -T test-db psql -U crm -d postgres -qc "DROP DATABASE IF EXISTS crm WITH (FORCE)" -c "CREATE DATABASE crm"
dc exec -T db pg_dump -U crm -Fc crm | tdc exec -T test-db pg_restore -U crm -d crm --no-owner --no-privileges --exit-on-error

log "Usuwam z kopii wszystko, co laczy sie ze swiatem"
tdc exec -T test-db psql -U crm -d crm -v ON_ERROR_STOP=1 -q <<'SQL'
UPDATE bank_connections SET session_id = '', auth_state = '', state = 'closed',
       last_error = 'Panel testowy: połączenie z bankiem wyłączone.';
DELETE FROM app_settings WHERE key IN ('enablebanking_app_id', 'enablebanking_key');
UPDATE mail_accounts SET password_enc = '', active = false;
UPDATE companies SET ksef_token_enc = '', ksef_key_enc = '';
UPDATE users SET totp_secret_enc = '';
DELETE FROM api_tokens;
DELETE FROM oauth_codes;
SQL

log "Uruchamiam panel testowy"
tdc up -d test-app
ok=0
for _ in $(seq 1 90); do
  if tdc exec -T test-app python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [ "$ok" != 1 ]; then
  printf '{"stan":"blad","wersja":"%s","czas":"%s"}\n' "$WERSJA" "$(date -Is)" > "$STAN/test.json"
  blad "Panel testowy nie odpowiada. Logi: docker compose -p crm-test logs test-app"
fi
printf '{"stan":"dziala","wersja":"%s","czas":"%s","adres":"https://%s"}\n' "$WERSJA" "$(date -Is)" "$TEST_DOMAIN" > "$STAN/test.json"
log "Panel testowy gotowy: https://$TEST_DOMAIN (wersja $WERSJA)"
