"""Ustawienia z zmiennych srodowiskowych (plik .env na serwerze)."""
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/crm.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-zmien-mnie")
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
# Adres programu z internetu, np. https://faktury.1-2-3-4.sslip.io (potrzebny dla OAuth za Caddy).
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")
# Dokad OAuth moze odeslac po zgodzie (konektory Claude). localhost zawsze dozwolony.
OAUTH_REDIRECT_HOSTS = [
    h.strip().lower() for h in os.environ.get("OAUTH_REDIRECT_HOSTS", "claude.ai,claude.com").split(",") if h.strip()
]
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

if DATABASE_URL.startswith("postgres") and (SECRET_KEY == "dev-secret-zmien-mnie" or ADMIN_PASSWORD == "admin"):
    raise RuntimeError("Na serwerze ustaw SECRET_KEY i ADMIN_PASSWORD (plik .env tworzy deploy/install.sh).")
# Co ile minut pobierac poczte w tle (0 = wylaczone, np. w testach).
MAIL_SYNC_MINUTES = int(os.environ.get("MAIL_SYNC_MINUTES", "5"))
