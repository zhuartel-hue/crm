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
# Pobieranie faktur kosztowych z KSeF w tle co tyle godzin (0 = tylko recznie przyciskiem).
KSEF_PURCHASE_SYNC_HOURS = float(os.environ.get("KSEF_PURCHASE_SYNC_HOURS", "6"))
# Sprawdzanie faktur cyklicznych do wystawienia co tyle minut (0 = wylaczone, tylko przycisk).
RECURRING_CHECK_MINUTES = int(os.environ.get("RECURRING_CHECK_MINUTES", "60"))
# Pobieranie operacji z podlaczonych bankow (Enable Banking) co tyle godzin (0 = tylko przyciskiem).
# Banki zwykle pozwalaja na ok. 4 pobrania w tle dziennie.
BANK_SYNC_HOURS = float(os.environ.get("BANK_SYNC_HOURS", "6"))
# Zamowienia ze sklepu i Allegro oraz statusy przesylek co tyle minut (0 = tylko przyciskiem).
INTEGRATIONS_SYNC_MINUTES = int(os.environ.get("INTEGRATIONS_SYNC_MINUTES", "10"))

# Zadania w tle: "app" - watki w procesie aplikacji (jeden proces), "off" - osobny proces app.worker.
BACKGROUND_JOBS = os.environ.get("BACKGROUND_JOBS", "app").strip().lower()
