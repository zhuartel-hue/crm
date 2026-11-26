#!/usr/bin/env bash
# Jednorazowe przygotowanie NOWEGO serwera Sygnavo (Hetzner, Ubuntu 24.04, z podpietym Volume).
# Uzycie (root):  bash przygotuj-serwer.sh twoja.domena.pl
# Bezpieczne do ponownego uruchomienia. Nie dotyka starego serwera chirpstack-1.
source "$(dirname "$0")/wspolne.sh"

[ "$(id -u)" -eq 0 ] || blad "Uruchom jako root."
APP_DOMAIN_ARG="${1:-}"
[ -n "$APP_DOMAIN_ARG" ] || blad "Podaj domene, np. bash przygotuj-serwer.sh crm.margotsystem.com"
. /etc/os-release
[ "$ID" = ubuntu ] && [ "${VERSION_ID%%.*}" -ge 24 ] || blad "Oczekiwany Ubuntu 24.04, jest $PRETTY_NAME."

log "Klucz SSH (zanim wylacze logowanie haslem)"
[ -s /root/.ssh/authorized_keys ] || blad "Brak klucza SSH w /root/.ssh/authorized_keys. Nie wylacze hasel, zeby nie odciac dostepu."

log "Strefa czasowa i aktualizacje systemu"
timedatectl set-timezone Europe/Warsaw
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get -y -q upgrade
apt-get install -y -q ca-certificates curl git gnupg fail2ban unattended-upgrades ufw restic jq

log "Automatyczne latki bezpieczenstwa (bez samoczynnego restartu)"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF
systemctl enable --now unattended-upgrades fail2ban

log "Docker"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
mkdir -p /etc/docker
[ -f /etc/docker/daemon.json ] || echo '{"log-driver":"json-file","log-opts":{"max-size":"20m","max-file":"5"}}' > /etc/docker/daemon.json
systemctl enable --now docker
systemctl restart docker

log "SSH tylko kluczem"
cat > /etc/ssh/sshd_config.d/10-sygnavo.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
CONF
sshd -t && systemctl reload ssh

log "Zapora: tylko SSH, 80, 443"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

log "Plik wymiany 2 GB (zapas przy skokach pamieci)"
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
sysctl -q -w vm.swappiness=10 && echo 'vm.swappiness=10' > /etc/sysctl.d/90-sygnavo.conf

log "Dysk na baze (Hetzner Volume)"
mapfile -t VOLS < <(findmnt -rn -o TARGET | grep '^/mnt/HC_Volume_' || true)
[ "${#VOLS[@]}" -eq 1 ] || blad "Oczekiwany dokladnie jeden zamontowany Volume w /mnt/HC_Volume_*, jest: ${#VOLS[@]}."
VOL="${VOLS[0]}"
grep -q "$VOL" /etc/fstab || blad "Volume $VOL nie jest w /etc/fstab (nie zamontuje sie po restarcie)."
mkdir -p "$VOL/pgdata"

log "Ustawienia serwera: /etc/sygnavo/serwer.env"
mkdir -p /etc/sygnavo && chmod 700 /etc/sygnavo
touch /etc/sygnavo/serwer.env && chmod 600 /etc/sygnavo/serwer.env
setv() { sed -i "/^$1=/d" /etc/sygnavo/serwer.env; echo "$1=$2" >> /etc/sygnavo/serwer.env; }
setv APP_DIR /opt/crm
setv APP_DOMAIN "$APP_DOMAIN_ARG"
setv DB_DATA_DIR "$VOL/pgdata"
grep -q '^RESTIC_REPOSITORY=' /etc/sygnavo/serwer.env || cat >> /etc/sygnavo/serwer.env <<'CONF'
# Uzupelnij po zamowieniu Storage Box (patrz deploy/serwer/README.md, krok 3):
RESTIC_REPOSITORY=
RESTIC_PASSWORD_FILE=/etc/sygnavo/restic-haslo
# Adresy ping z healthchecks.io (alarm, gdy kopia/test nie zadziala):
HC_KOPIA_URL=
HC_TEST_URL=
CONF

log "Gotowe"
echo "Serwer przygotowany. Nastepny krok: deploy/serwer/README.md, krok 2 (kod i zainstaluj.sh)."
