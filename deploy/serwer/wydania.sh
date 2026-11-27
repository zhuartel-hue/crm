#!/usr/bin/env bash
# Wdrozenia z GitHuba bez iMaca. Serwer sam (co 2 minuty, sygnavo-wydania.timer) pobiera kod z GitHuba:
#   1. panel testowy (TEST_DOMAIN) dostaje zawsze najnowszy kod z galezi DEPLOY_BRANCH,
#   2. produkcja dostaje tylko wersje wpisana w pliku deploy/wydanie/produkcja na tej galezi.
#      Ten plik zmienia Claude dopiero po "zatwierdzam" wlasciciela (docs/ARCHITEKTURA-SAAS.md, 8.1),
#      i tylko na wersje, ktora juz dzialala na panelu testowym.
# Przed produkcja: kopia bazy. Gdy nowa wersja nie wstanie: powrot do poprzedniego kodu i bazy sprzed zmiany.
# Serwer tylko CZYTA z GitHuba (klucz "deploy key" tylko do odczytu). Do serwera nic nie loguje sie z zewnatrz.
# Ustawienia w /etc/sygnavo/serwer.env: DEPLOY_REPO, DEPLOY_BRANCH, opcjonalnie DEPLOY_KEY.
# Recznie (root): bash wydania.sh          - jedno sprawdzenie teraz
#                 bash wydania.sh --stan   - co jest wgrane
source "$(dirname "$0")/wspolne.sh"
[ "$(id -u)" -eq 0 ] || blad "Uruchom jako root."
STAN=/var/lib/sygnavo/stan
REPO=/var/lib/sygnavo/repo.git
mkdir -p "$STAN" /var/log/sygnavo
if [ "${1:-}" = "--stan" ]; then cat "$STAN"/*.json 2>/dev/null; tail -n 20 "$STAN/wydania.log" 2>/dev/null; exit 0; fi
[ -n "${DEPLOY_REPO:-}" ] || exit 0
exec 9>/run/sygnavo-wydania.lock
flock -n 9 || exit 0

zapisz() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M')" "$*" >> "$STAN/wydania.log"; tail -n 300 "$STAN/wydania.log" > "$STAN/.log" && mv "$STAN/.log" "$STAN/wydania.log"; }
wersja_z() { sed -n 's/.*"wersja":"\([^"]*\)".*/\1/p' "$1" 2>/dev/null | head -1; }
krotki() { git -C "$REPO" rev-parse --short=7 "$1"; }
rozpakuj() { # rozpakuj <commit> <katalog>
  rm -rf "$2" && mkdir -p "$2"
  git -C "$REPO" archive "$1" | tar -x -C "$2"
  krotki "$1" > "$2/WERSJA"
}

export GIT_SSH_COMMAND="ssh -i ${DEPLOY_KEY:-/root/.ssh/github-deploy} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
[ -d "$REPO" ] || git init -q --bare "$REPO"
if ! git -C "$REPO" fetch -q --prune "$DEPLOY_REPO" "+refs/heads/${DEPLOY_BRANCH:?ustaw DEPLOY_BRANCH}:refs/heads/wdrozenia" 2>/var/log/sygnavo/fetch.err; then
  echo '{"stan":"blad","opis":"nie moge pobrac kodu z GitHuba"}' > "$STAN/github.json"; exit 1
fi
printf '{"stan":"ok","czas":"%s"}\n' "$(date -Is)" > "$STAN/github.json"
TIP=$(git -C "$REPO" rev-parse refs/heads/wdrozenia)
TIP_K=$(krotki "$TIP")

# ---- 1. Panel testowy: najnowszy kod -------------------------------------------------------------
if [ -n "${TEST_DOMAIN:-}" ] && [ "$TIP_K" != "$(wersja_z "$STAN/test.json")" ] \
   && [ "$TIP_K" != "$(cat "$STAN/test-nieudane" 2>/dev/null)" ]; then
  zapisz "panel testowy: wgrywam $TIP_K"
  rozpakuj "$TIP" /var/lib/sygnavo/kod-testowy
  if bash "$SERWER_DIR/panel-testowy.sh" /var/lib/sygnavo/kod-testowy "$TIP_K" > "/var/log/sygnavo/test-$TIP_K.log" 2>&1; then
    echo "$TIP_K" >> "$STAN/test-historia"
    zapisz "panel testowy: $TIP_K dziala"
  else
    echo "$TIP_K" > "$STAN/test-nieudane"
    zapisz "panel testowy: $TIP_K NIE wstal (log /var/log/sygnavo/test-$TIP_K.log)"
  fi
fi

# ---- 2. Produkcja: tylko zatwierdzona wersja ---------------------------------------------------
CEL=$(git -C "$REPO" show "$TIP:deploy/wydanie/produkcja" 2>/dev/null | grep -Eo '^[0-9a-f]{7,40}' | head -1 || true)
[ -n "$CEL" ] || exit 0
git -C "$REPO" cat-file -e "$CEL^{commit}" 2>/dev/null || { zapisz "produkcja: nie ma commita $CEL na galezi"; exit 0; }
git -C "$REPO" merge-base --is-ancestor "$CEL" "$TIP" || { zapisz "produkcja: $CEL nie pochodzi z $DEPLOY_BRANCH"; exit 0; }
CEL_K=$(krotki "$CEL")
[ "$CEL_K" != "$(wersja_z "$STAN/produkcja.json")" ] || exit 0
[ "$CEL_K" != "$(cat "$STAN/produkcja-nieudane" 2>/dev/null)" ] || exit 0
if [ -n "${TEST_DOMAIN:-}" ] && ! grep -qx "$CEL_K" "$STAN/test-historia" 2>/dev/null; then
  zapisz "produkcja: $CEL_K czeka, bo nie dzialala jeszcze na panelu testowym"; exit 0
fi

LOG="/var/log/sygnavo/produkcja-$CEL_K.log"
# Uwaga: funkcje wolane z "if"/"||" nie przerywaja sie same na bledzie (set -e nie dziala), stad "|| return 1".
przygotuj() {
  bash "$SERWER_DIR/kopia.sh" "przed-$CEL_K" || return 1
  ZRZUT=$(ls -1t /var/backups/sygnavo/crm-*-przed-"$CEL_K".dump | head -1)
  [ -s "$ZRZUT" ] || return 1
  BAZA_PRZED=$(dc exec -T db psql -U crm -d crm -tAc "SELECT version_num FROM alembic_version") || return 1
  [ -n "$BAZA_PRZED" ] || return 1
  rozpakuj "$CEL" /var/lib/sygnavo/kod-produkcja || return 1
  cp -a "$APP_DIR/.env" /var/lib/sygnavo/kod-produkcja/.env || return 1
  [ -f /var/lib/sygnavo/kod-produkcja/deploy/serwer/zainstaluj.sh ] || return 1
  echo "ZRZUT=$ZRZUT BAZA_PRZED=$BAZA_PRZED" > /var/lib/sygnavo/ostatnie-wdrozenie
  # Skrypty serwera z tej samej wersji (poprzednie zostaja obok do powrotu).
  rm -rf "$SERWER_DIR.nowy" "$SERWER_DIR.poprzedni" "$APP_DIR.poprzednia" || return 1
  cp -a /var/lib/sygnavo/kod-produkcja/deploy/serwer "$SERWER_DIR.nowy" || return 1
  ZAMIANA=1
  mv "$SERWER_DIR" "$SERWER_DIR.poprzedni" && mv "$SERWER_DIR.nowy" "$SERWER_DIR" || return 1
  mv "$APP_DIR" "$APP_DIR.poprzednia" && mv /var/lib/sygnavo/kod-produkcja "$APP_DIR" || return 1
}

wycofaj() {
  rm -rf "$APP_DIR.nieudana" "$SERWER_DIR.nieudany"
  if [ -d "$APP_DIR.poprzednia" ]; then mv "$APP_DIR" "$APP_DIR.nieudana" && mv "$APP_DIR.poprzednia" "$APP_DIR" || return 1; fi
  if [ -d "$SERWER_DIR.poprzedni" ]; then mv "$SERWER_DIR" "$SERWER_DIR.nieudany" && mv "$SERWER_DIR.poprzedni" "$SERWER_DIR" || return 1; fi
  BAZA_PO=$(dc exec -T db psql -U crm -d crm -tAc "SELECT version_num FROM alembic_version" || echo '?')
  if [ "$BAZA_PO" != "$BAZA_PRZED" ]; then
    echo "Baza zmieniona ($BAZA_PRZED -> $BAZA_PO), odtwarzam z $ZRZUT"
    dc stop app || return 1
    dc exec -T db psql -U crm -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE crm WITH (FORCE)" -c "CREATE DATABASE crm OWNER crm" || return 1
    dc exec -T db pg_restore -U crm -d crm --no-owner --exit-on-error < "$ZRZUT" || return 1
  fi
  bash "$SERWER_DIR/zainstaluj.sh" || return 1
}

zapisz "produkcja: wgrywam $CEL_K (log $LOG)"
ZRZUT="" BAZA_PRZED="" ZAMIANA=0
if ! przygotuj > "$LOG" 2>&1; then
  echo "$CEL_K" > "$STAN/produkcja-nieudane"
  # Przygotowanie moglo przerwac sie w polowie zamiany katalogow: wtedy wracamy do poprzednich.
  if [ "$ZAMIANA" = 1 ]; then wycofaj >> "$LOG" 2>&1 || zapisz "produkcja: POWROT NIEUDANY (log $LOG)"; fi
  zapisz "produkcja: przygotowanie $CEL_K nieudane, produkcja bez zmian (log $LOG)"
  exit 1
fi

if bash "$SERWER_DIR/zainstaluj.sh" >> "$LOG" 2>&1; then
  zapisz "produkcja: $CEL_K dziala"
  exit 0
fi

zapisz "produkcja: $CEL_K NIE wstala, wracam do poprzedniej wersji"
echo "$CEL_K" > "$STAN/produkcja-nieudane"
if wycofaj >> "$LOG" 2>&1; then
  zapisz "produkcja: przywrocona poprzednia wersja $(wersja_z "$STAN/produkcja.json")"
else
  zapisz "produkcja: POWROT NIEUDANY, potrzebna pomoc (log $LOG)"
fi
exit 1
