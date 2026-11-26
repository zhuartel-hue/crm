#!/usr/bin/env bash
# Przeniesienie Sygnavo ze starego serwera na nowy. Uruchamiane NA iMACU (ma klucze SSH do obu serwerow).
# Uzycie:
#   bash przenies.sh proba     # proba: stary dziala dalej, nowy dostaje kopie danych do sprawdzenia
#   bash przenies.sh koncowe   # przeprowadzka: stary program zatrzymany na czas kopii (ok. 2-5 min)
# Zmienne: STARY (domyslnie chirpstack-1), NOWY (domyslnie sygnavo-1) - nazwy z ~/.ssh/config.
# Stary serwer NIE jest kasowany ani zmieniany (poza zatrzymaniem programu w trybie "koncowe").
set -euo pipefail
TRYB="${1:-}"; [ "$TRYB" = proba ] || [ "$TRYB" = koncowe ] || { echo "Uzycie: bash przenies.sh proba|koncowe"; exit 1; }
STARY="${STARY:-chirpstack-1}"; NOWY="${NOWY:-sygnavo-1}"
TU="$(cd "$(dirname "$0")" && pwd)"
KOPIE="${KOPIE:-$HOME/kopie-faktur}"; mkdir -p "$KOPIE"
STAMP="$(date +%Y%m%d-%H%M)"
log() { printf '\n==> %s\n' "$*"; }
nowy() { ssh "$NOWY" "$@"; }
dcn() { nowy "docker compose -f /opt/sygnavo-serwer/docker-compose.yml --env-file /opt/crm/.env --env-file /etc/sygnavo/serwer.env $*"; }
SQL="select (select count(*) from invoices)||' '||(select count(*) from contractors)||' '||(select count(*) from products)||' '||(select count(*) from product_photos)||' '||(select version_num from alembic_version)"

log "Sprawdzam polaczenia"
ssh "$STARY" true; nowy true
log "Kopiuje skrypty serwera na $NOWY:/opt/sygnavo-serwer"
nowy "mkdir -p /opt/sygnavo-serwer"
tar -C "$TU" -czf - . | nowy "tar -C /opt/sygnavo-serwer -xzf -"
nowy "test -f /etc/sygnavo/serwer.env" || { echo "Na $NOWY najpierw przygotuj-serwer.sh"; exit 1; }


log "Kopiuje kod i .env (ten sam SECRET_KEY) z $STARY:/opt/crm"
nowy "test ! -d /opt/crm || mv /opt/crm /opt/crm.poprzedni-$STAMP"
ssh "$STARY" "tar -C /opt -czf - crm" | nowy "tar -C /opt -xzf -"
nowy "chmod 600 /opt/crm/.env"

if [ "$TRYB" = koncowe ]; then
  log "Zatrzymuje program na $STARY (dane przestaja sie zmieniac)"
  echo "Powrot w razie problemu:  ssh $STARY 'cd /opt/crm && docker compose start app'"
  ssh "$STARY" "cd /opt/crm && docker compose stop app"
fi

ZRZUT="$KOPIE/crm-przeprowadzka-$TRYB-$STAMP.dump"
log "Zrzut bazy ze $STARY -> $ZRZUT"
ssh "$STARY" "docker exec crm-db-1 pg_dump -U crm -Fc crm" > "$ZRZUT"
[ "$(wc -c < "$ZRZUT")" -gt 10000 ] || { echo "Zrzut podejrzanie maly, przerywam."; exit 1; }
read -r s_f s_k s_p s_z s_v < <(ssh "$STARY" "docker exec crm-db-1 psql -U crm -d crm -tAc \"$SQL\"")

log "Odtwarzam na $NOWY"
dcn "stop app" >/dev/null 2>&1 || true
dcn "up -d db"
for _ in $(seq 1 60); do dcn "exec -T db pg_isready -U crm" >/dev/null 2>&1 && break; sleep 2; done
dcn "exec -T db psql -U crm -d postgres -qc 'drop database if exists crm'"
dcn "exec -T db psql -U crm -d postgres -qc 'create database crm owner crm'"
dcn "exec -T db pg_restore -U crm -d crm --no-owner --exit-on-error" < "$ZRZUT"
read -r n_f n_k n_p n_z n_v < <(dcn "exec -T db psql -U crm -d crm -tAc \"$SQL\"")
printf '\n%-14s %10s %10s\n' "" stary nowy faktury "$s_f" "$n_f" kontrahenci "$s_k" "$n_k" produkty "$s_p" "$n_p" zdjecia "$s_z" "$n_z" "wersja bazy" "$s_v" "$n_v"
[ "$s_f $s_k $s_p $s_z $s_v" = "$n_f $n_k $n_p $n_z $n_v" ] || { echo "NIEZGODNOSC danych, przerywam (stary serwer bez zmian poza ew. zatrzymaniem)."; exit 1; }
echo "Dane zgodne."

log "Uruchamiam program na $NOWY"
nowy "bash /opt/sygnavo-serwer/zainstaluj.sh"

echo
if [ "$TRYB" = proba ]; then
  echo "PROBA OK. Sprawdz program przez:  ssh -L 8443:localhost:443 $NOWY  (albo tymczasowa domene)."
  echo "Stary serwer dziala bez zmian."
else
  echo "PRZEPROWADZKA: dane sa na $NOWY. Teraz zmien rekord A domeny na IP $NOWY (seohost.pl)."
  echo "Program na $STARY jest zatrzymany i zostaje jako zapas co najmniej 7 dni."
fi
