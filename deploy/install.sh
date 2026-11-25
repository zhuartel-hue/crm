#!/usr/bin/env bash
# Instalacja / aktualizacja programu do faktur na serwerze z ChirpStackiem.
# Uzycie (root), z katalogu /opt/crm, w ktorym jest kod z repozytorium:
#   bash deploy/install.sh
# Ponowne uruchomienie aktualizuje program; hasla i dane zostaja.
set -euo pipefail
cd "$(dirname "$0")/.."
log() { printf '\n==> %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Uruchom jako root."; exit 1; }
CHIRPSTACK_DIR="${CHIRPSTACK_DIR:-/opt/chirpstack}"
[ -f "$CHIRPSTACK_DIR/.env" ] || { echo "Nie widze ChirpStacka w $CHIRPSTACK_DIR."; exit 1; }
CS_DOMAIN="$(grep '^DOMAIN=' "$CHIRPSTACK_DIR/.env" | cut -d= -f2-)"
DEFAULT_DOMAIN="faktury.$CS_DOMAIN"
# Wlasny adres (np. crm.margotsystem.com) ustawia sie w /opt/crm/.env: APP_DOMAIN=...
# Rekord DNS A tego adresu musi wskazywac na serwer. Stary adres faktury.<domena> przekierowuje na nowy.
APP_DOMAIN="$( (grep '^APP_DOMAIN=' .env 2>/dev/null || true) | cut -d= -f2-)"
APP_DOMAIN="${APP_DOMAIN:-$DEFAULT_DOMAIN}"

# Najpierw pelna kopia serwera, jesli jest skrypt kopii Sygnavo.
if [ -f /opt/sygnavo/kopia.sh ]; then bash /opt/sygnavo/kopia.sh || echo "Kopia sie nie udala, kontynuuje."; fi

if [ ! -f .env ]; then
  log "Generuje hasla"
  umask 077
  ADMIN_PASSWORD="$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-12)"
  cat > .env <<ENV
DB_PASSWORD=$(openssl rand -hex 24)
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_LOGIN=admin
ADMIN_PASSWORD=$ADMIN_PASSWORD
ENV
  {
    echo "Program do faktur: https://$APP_DOMAIN"
    echo "Login: admin"
    echo "Haslo: $ADMIN_PASSWORD"
    echo "Konektor Claude (MCP): https://$APP_DOMAIN/mcp"
  } > /root/crm-dostep.txt
  chmod 600 /root/crm-dostep.txt
fi

# Adres publiczny (potrzebny dla logowania konektora Claude przez OAuth).
sed -i '/^PUBLIC_URL=/d' .env
echo "PUBLIC_URL=https://$APP_DOMAIN" >> .env

log "Buduje i uruchamiam program"
docker compose build
docker compose up -d --remove-orphans

log "Adres HTTPS w Caddy: $APP_DOMAIN"
CADDYFILE="$CHIRPSTACK_DIR/Caddyfile"
if [ "$APP_DOMAIN" = "$DEFAULT_DOMAIN" ]; then
  BLOCK="$(printf '%s\n' '# >>> crm (program do faktur, /opt/crm; blok zarzadzany przez deploy/install.sh)' \
    'faktury.{$DOMAIN} {' '	encode gzip' '	reverse_proxy crm-app:8000' '}' '# <<< crm')"
else
  BLOCK="$(printf '%s\n' '# >>> crm (program do faktur, /opt/crm; blok zarzadzany przez deploy/install.sh)' \
    "$APP_DOMAIN {" '	encode gzip' '	reverse_proxy crm-app:8000' '}' \
    'faktury.{$DOMAIN} {' "	redir https://$APP_DOMAIN{uri} permanent" '}' '# <<< crm')"
fi
# Usun stary blok (bez znacznikow z pierwszej instalacji albo ze znacznikami) i dopisz aktualny.
python3 - "$CADDYFILE" "$BLOCK" <<'PY'
import re, sys
path, block = sys.argv[1], sys.argv[2]
text = open(path).read()
new = re.sub(r"\n*# >>> crm.*?# <<< crm\n?", "\n", text, flags=re.S)
new = re.sub(r"\n*# Program do faktur \(katalog /opt/crm\)\.\nfaktury\.\{\$DOMAIN\} \{\n\tencode gzip\n\treverse_proxy crm-app:8000\n\}\n?", "\n", new)
new = new.rstrip("\n") + "\n\n" + block + "\n"
if new != text:
    open(path + ".przed-crm-install", "w").write(text)
    open(path, "w").write(new)
    print("Caddyfile zmieniony (poprzedni: " + path + ".przed-crm-install)")
else:
    print("Caddyfile bez zmian")
PY
(cd "$CHIRPSTACK_DIR" && docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile) \
  || (cd "$CHIRPSTACK_DIR" && docker compose restart caddy)

log "Czekam na program"
for _ in $(seq 1 60); do
  docker compose exec -T app python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')" >/dev/null 2>&1 && break
  sleep 2
done
docker compose ps
log "Gotowe"
echo "Adres, login i haslo: /root/crm-dostep.txt (haslo celowo nie jest wypisywane)."
