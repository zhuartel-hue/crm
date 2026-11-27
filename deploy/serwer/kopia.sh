#!/usr/bin/env bash
# Kopia bazy Sygnavo: zrzut lokalnie (dysk systemowy, nie Volume) + zaszyfrowana kopia na Storage Box (restic).
# Uzycie: bash kopia.sh [etykieta]   (np. przed-wdrozeniem). Uruchamiane co godzine przez sygnavo-kopia.timer.
# Wszystkie dane programu (takze zdjecia produktow, logo, zalaczniki) sa w bazie, wiec to jest pelna kopia.
source "$(dirname "$0")/wspolne.sh"
ETYKIETA="${1:-co-godzine}"
LOKALNIE=/var/backups/sygnavo
mkdir -p "$LOKALNIE" && chmod 700 "$LOKALNIE"
umask 077

hc_ping "${HC_KOPIA_URL:-}" start
trap 'hc_ping "${HC_KOPIA_URL:-}" fail' ERR

PLIK="$LOKALNIE/crm-$(date +%Y%m%d-%H%M)-$ETYKIETA.dump"
log "Zrzut bazy: $PLIK"
dc exec -T db pg_dump -U crm -Fc crm > "$PLIK.tmp"
# Sprawdzenie, czy zrzut jest kompletny i czytelny (spis zawartosci).
dc exec -T db pg_restore -l < "$PLIK.tmp" > /dev/null
[ "$(stat -c %s "$PLIK.tmp")" -gt 10000 ] || { echo "Zrzut podejrzanie maly."; false; }
mv "$PLIK.tmp" "$PLIK"
cp "$APP_DIR/.env" "$LOKALNIE/env-aplikacji"

# Lokalnie: 48 ostatnich kopii godzinowych + 10 ostatnich przed wdrozeniami.
ls -1t "$LOKALNIE"/crm-*-co-godzine.dump 2>/dev/null | tail -n +49 | xargs -r rm -f || true
ls -1t "$LOKALNIE"/crm-*.dump 2>/dev/null | grep -v co-godzine | tail -n +11 | xargs -r rm -f || true

if [ -n "${RESTIC_REPOSITORY:-}" ]; then
  log "Kopia na Storage Box (szyfrowana)"
  restic backup --quiet --host sygnavo-1 --tag sygnavo --tag "$ETYKIETA" "$PLIK" "$LOKALNIE/env-aplikacji" /etc/sygnavo/serwer.env
  # Raz na dobe (o 3:xx) porzadkowanie: 48 godzinowych, 30 dziennych, 12 miesiecznych, 6 rocznych.
  if [ "$(date +%H)" = 03 ]; then
    restic forget --quiet --host sygnavo-1 --tag sygnavo --keep-hourly 48 --keep-daily 30 --keep-monthly 12 --keep-yearly 6 --prune
    restic check --quiet
  fi
else
  echo "UWAGA: brak RESTIC_REPOSITORY, kopia tylko lokalna."
fi
hc_ping "${HC_KOPIA_URL:-}"
log "Kopia OK"
