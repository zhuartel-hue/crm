# Wspolne ustawienia skryptow serwera Sygnavo. Wczytywane przez "source".
# Ustawienia serwera: /etc/sygnavo/serwer.env (tworzy zainstaluj.sh), hasla aplikacji: $APP_DIR/.env.
set -euo pipefail
SERWER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f /etc/sygnavo/serwer.env ] && set -a && . /etc/sygnavo/serwer.env && set +a
APP_DIR="${APP_DIR:-/opt/crm}"
log() { printf '\n==> %s\n' "$*"; }
blad() { printf '\nBLAD: %s\n' "$*" >&2; exit 1; }
dc() { docker compose -f "$SERWER_DIR/docker-compose.yml" --env-file "$APP_DIR/.env" --env-file /etc/sygnavo/serwer.env "$@"; }
# Powiadomienie do Healthchecks.io (alarm, gdy kopia lub test sie nie uda albo w ogole nie ruszy).
hc_ping() { # hc_ping <URL> [fail|start]
  [ -n "${1:-}" ] || return 0
  curl -fsS -m 10 --retry 3 "${1}${2:+/$2}" >/dev/null || true
}
