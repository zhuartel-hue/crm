"""Ustawienia z zmiennych srodowiskowych (plik .env na serwerze)."""
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/crm.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-zmien-mnie")
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

if DATABASE_URL.startswith("postgres") and (SECRET_KEY == "dev-secret-zmien-mnie" or ADMIN_PASSWORD == "admin"):
    raise RuntimeError("Na serwerze ustaw SECRET_KEY i ADMIN_PASSWORD (plik .env tworzy deploy/install.sh).")
