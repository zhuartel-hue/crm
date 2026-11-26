#!/usr/bin/env bash
# Test odtwarzania: pobiera NAJNOWSZA kopie ze Storage Box, odtwarza ja w osobnej, tymczasowej bazie
# i porownuje z produkcja. Produkcji nie dotyka. Co niedziele (sygnavo-test-odtwarzania.timer).
# Uzycie: bash test-odtwarzania.sh
source "$(dirname "$0")/wspolne.sh"
hc_ping "${HC_TEST_URL:-}" start
TMP="$(mktemp -d /var/tmp/sygnavo-test.XXXX)"
KONTENER=sygnavo-test-odtwarzania
sprzatnij() { docker rm -f "$KONTENER" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap 'sprzatnij' EXIT
trap 'hc_ping "${HC_TEST_URL:-}" fail' ERR

[ -n "${RESTIC_REPOSITORY:-}" ] || { echo "Brak RESTIC_REPOSITORY: nie ma czego testowac."; false; }
log "Pobieram najnowsza kopie ze Storage Box"
restic restore latest --host sygnavo-1 --tag sygnavo --target "$TMP" --quiet
ZRZUT="$(find "$TMP" -name 'crm-*.dump' | head -1)"
[ -n "$ZRZUT" ] || { echo "W kopii nie ma zrzutu bazy."; false; }
echo "Zrzut: $(basename "$ZRZUT")"

log "Odtwarzam w tymczasowej bazie"
docker run -d --rm --name "$KONTENER" -e POSTGRES_PASSWORD=test -e POSTGRES_USER=crm -e POSTGRES_DB=crm \
  --memory 1g postgres:16-alpine >/dev/null
# Obraz postgres przy pierwszym starcie uruchamia baze tymczasowo (tylko gniazdo unix), potem restartuje.
# Czekamy na polaczenie TCP, ktore dziala dopiero po tym restarcie, i na udane zapytanie.
gotowa=0
for _ in $(seq 1 90); do
    if docker exec "$KONTENER" pg_isready -h 127.0.0.1 -U crm >/dev/null 2>&1 \
        && docker exec "$KONTENER" psql -h 127.0.0.1 -U crm -d crm -tAc 'select 1' >/dev/null 2>&1; then
        gotowa=1; break
    fi
    sleep 1
done
[ "$gotowa" = 1 ] || blad "tymczasowa baza testu nie wystartowala w 90 s"
docker exec -i "$KONTENER" pg_restore -U crm -d crm --no-owner --exit-on-error < "$ZRZUT"

log "Porownuje z produkcja"
SQL="select (select count(*) from invoices)||' '||(select count(*) from contractors)||' '||(select count(*) from products)||' '||(select count(*) from purchase_invoices)||' '||(select version_num from alembic_version)"
read -r t_f t_k t_p t_z t_v < <(docker exec "$KONTENER" psql -U crm -d crm -tAc "$SQL")
read -r p_f p_k p_p p_z p_v < <(dc exec -T db psql -U crm -d crm -tAc "$SQL")
printf '%-22s %10s %10s\n' "" kopia produkcja faktury "$t_f" "$p_f" kontrahenci "$t_k" "$p_k" produkty "$t_p" "$p_p" koszty "$t_z" "$p_z" "wersja bazy" "$t_v" "$p_v"
# Kopia moze miec mniej rekordow niz produkcja (doszly nowe od kopii), ale nie wiecej i nie zero.
for para in "$t_f:$p_f" "$t_k:$p_k" "$t_p:$p_p"; do
  t="${para%%:*}"; p="${para##*:}"
  [ "$t" -gt 0 ] && [ "$t" -le "$p" ] && [ "$t" -ge $(( p * 98 / 100 )) ] || { echo "Niezgodnosc liczby rekordow."; false; }
done
[ "$t_v" = "$p_v" ] || echo "Uwaga: inna wersja bazy (bylo wdrozenie po kopii) - to nie jest blad."
hc_ping "${HC_TEST_URL:-}"
log "Test odtwarzania OK"
