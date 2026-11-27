#!/usr/bin/env bash
# Instalacja / aktualizacja Sygnavo na serwerze sygnavo-1 (po przygotuj-serwer.sh).
# Uzycie (root):  bash zainstaluj.sh
# Wymaga: kod aplikacji w $APP_DIR (domyslnie /opt/crm) i plik $APP_DIR/.env.
# WAZNE: przy przeprowadzce .env MUSI byc skopiowany ze starego serwera (ten sam SECRET_KEY),
# inaczej zaszyfrowane tokeny KSeF, banku i poczty nie dadza sie odczytac.
source "$(dirname "$0")/wspolne.sh"
[ "$(id -u)" -eq 0 ] || blad "Uruchom jako root."
[ -f /etc/sygnavo/serwer.env ] || blad "Najpierw przygotuj-serwer.sh."
[ -f "$APP_DIR/Dockerfile" ] || blad "Brak kodu aplikacji w $APP_DIR."
[ -f "$APP_DIR/.env" ] || blad "Brak $APP_DIR/.env. Skopiuj go ze starego serwera (README, krok 2)."
for k in SECRET_KEY DB_PASSWORD ADMIN_PASSWORD; do
  grep -q "^$k=." "$APP_DIR/.env" || blad "W $APP_DIR/.env brakuje $k."
done
chmod 600 "$APP_DIR/.env"

# Adres publiczny dla logowania konektora Claude (OAuth).
WERSJA="$(cat "$APP_DIR/WERSJA" 2>/dev/null || git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
sed -i '/^PUBLIC_URL=/d;/^APP_DOMAIN=/d;/^APP_VERSION=/d' "$APP_DIR/.env"
echo "PUBLIC_URL=https://$APP_DOMAIN" >> "$APP_DIR/.env"
echo "APP_DOMAIN=$APP_DOMAIN" >> "$APP_DIR/.env"
echo "APP_VERSION=$WERSJA" >> "$APP_DIR/.env"
mkdir -p /var/lib/sygnavo/stan
install -D -m 644 "$SERWER_DIR/Caddyfile" /etc/sygnavo/caddy/Caddyfile

if dc ps --status running --services 2>/dev/null | grep -qx db; then
  log "Kopia bazy przed zmiana"
  bash "$SERWER_DIR/kopia.sh" przed-wdrozeniem || blad "Kopia sie nie udala. Przerywam, nic nie zmienilem."
fi

log "Buduje i uruchamiam (wersja kodu: $WERSJA)"
dc build app
dc up -d --remove-orphans
# Nowy Caddyfile bez przerwy w dzialaniu (gdy sie nie zmienil, nic sie nie dzieje).
ok=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if dc exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[ "$ok" = 1 ] || blad "Caddy nie przyjal Caddyfile: docker compose logs caddy (program dziala, adresy bez zmian)."

log "Czekam na program"
ok=0
for _ in $(seq 1 90); do
  if dc exec -T app python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
dc ps
[ "$ok" = 1 ] || blad "Program nie odpowiada na /health. Logi: docker compose logs app (stare dane sa w kopii)."
printf '{"stan":"dziala","wersja":"%s","czas":"%s","adres":"https://%s"}\n' "$WERSJA" "$(date -Is)" "$APP_DOMAIN" > /var/lib/sygnavo/stan/produkcja.json

log "Harmonogram kopii i testu odtwarzania"
install -m 644 "$SERWER_DIR/systemd/"*.service "$SERWER_DIR/systemd/"*.timer /etc/systemd/system/
sed -i "s#@SERWER_DIR@#$SERWER_DIR#g" /etc/systemd/system/sygnavo-*.service
systemctl daemon-reload
if grep -q '^RESTIC_REPOSITORY=.' /etc/sygnavo/serwer.env; then
  systemctl enable --now sygnavo-kopia.timer sygnavo-test-odtwarzania.timer
  echo "Kopie wlaczone (co godzine), test odtwarzania co niedziele."
else
  echo "UWAGA: Storage Box nie jest jeszcze ustawiony (RESTIC_REPOSITORY). Kopie poza serwerem NIE dzialaja."
fi
if grep -q '^DEPLOY_REPO=.' /etc/sygnavo/serwer.env; then
  systemctl enable --now sygnavo-wydania.timer
  echo "Wdrozenia z GitHuba wlaczone (sprawdzanie co 2 minuty, wydania.sh)."
fi
log "Gotowe: https://$APP_DOMAIN"
