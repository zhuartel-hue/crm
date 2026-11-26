"""Uzytkownicy, role i organizacje (etap B architektury SaaS).

Kazdy uzytkownik nalezy do jednej organizacji i widzi tylko jej firmy. Rola decyduje o dostepnych
czesciach programu. Pierwsze logowanie starym loginem z .env (ADMIN_LOGIN) tworzy konto wlasciciela,
wiec przejscie z jednego loginu na konta uzytkownikow jest niewidoczne.
"""
from __future__ import annotations

import datetime as dt
import hmac
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from . import password as pw
from .db import get_db
from .models import AppSetting, Company, Organisation, User
from .services import audit

ROLES = {
    "wlasciciel": "Właściciel",
    "ksiegowosc": "Księgowość",
    "handlowiec": "Handlowiec",
    "magazyn": "Magazyn / serwis",
}
ROLE_HINTS = {
    "wlasciciel": "Wszystko, także użytkownicy, Claude i ustawienia.",
    "ksiegowosc": "Faktury, koszty, bank, należności i ustawienia firmy. Bez zarządzania użytkownikami.",
    "handlowiec": "Kontrahenci, oferty, faktury, umowy, zadania. Bez banku, kosztów i ustawień.",
    "magazyn": "Magazyn (WZ/PZ), zamówienia, produkty, sprzęt i serwis, zadania.",
}

# Sciezki zawsze dostepne po zalogowaniu.
_COMMON = ("/konto", "/logout", "/firma/", "/static", "/zdjecia", "/sw.js")
# Czego rola NIE moze (prefiksy sciezek). Wlasciciel moze wszystko.
_DENY = {
    "ksiegowosc": ("/ustawienia/uzytkownicy", "/dostep", "/import"),
    "handlowiec": ("/ustawienia", "/dostep", "/import", "/bank", "/koszty", "/dziennik", "/naleznosci"),
}
# Magazyn: tylko to, co jest tu wymienione.
_ALLOW_ONLY = {
    "magazyn": ("/start", "/wz", "/produkty", "/sprzet", "/zadania", "/kalendarz", "/zamowienia"),
}

MAX_FAILED = 5
LOCK_TIME = dt.timedelta(minutes=15)
LOGIN_RE = re.compile(r"^[a-z0-9._@-]{3,80}$")

router = APIRouter()


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def can_open(role: str | None, path: str) -> bool:
    """Czy rola moze otworzyc te strone programu."""
    role = role or "wlasciciel"
    if role == "wlasciciel" or path == "/" or path.startswith(_COMMON):
        return True
    if role in _ALLOW_ONLY:
        return path.startswith(_ALLOW_ONLY[role])
    if role not in _DENY:
        return False  # nieznana rola: nic poza wspolnymi stronami
    return not path.startswith(_DENY[role])


# --- organizacje ---------------------------------------------------------------


def ensure_default_org(db: Session) -> None:
    """Firmy bez organizacji (swieza baza po seed) trafiaja do pierwszej organizacji."""
    orphans = db.scalars(select(Company).where(Company.organisation_id.is_(None)).order_by(Company.id)).all()
    if not orphans:
        return
    org = db.scalar(select(Organisation).order_by(Organisation.id))
    if org is None:
        org = Organisation(name=orphans[0].short_name or "Moja organizacja")
        db.add(org)
        db.flush()
    for c in orphans:
        c.organisation_id = org.id
    db.commit()


def org_companies(db: Session, org_id: int | None) -> list[Company]:
    q = select(Company).order_by(Company.id)
    if org_id is not None:
        q = q.where(Company.organisation_id == org_id)
    return list(db.scalars(q))


# --- logowanie -------------------------------------------------------------------


def _by_login(db: Session, login: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.login) == login.strip().lower()))


def _bootstrap_owner(db: Session, login: str, password: str) -> User | None:
    """Stary login z .env: przy pierwszym udanym logowaniu zakladamy konto wlasciciela."""
    if not pw.check_login(db, login, password):
        return None
    ensure_default_org(db)
    org = db.scalar(select(Organisation).order_by(Organisation.id))
    if org is None:
        org = Organisation(name="Moja organizacja")
        db.add(org)
        db.flush()
    row = db.get(AppSetting, pw.KEY)
    user = User(organisation_id=org.id, login=login.strip().lower(), name="Właściciel", role="wlasciciel",
                password_hash=row.value if row and row.value else pw.hash_password(password))
    db.add(user)
    db.flush()
    audit(db, f"www ({user.login})", "user.bootstrap", "Utworzono konto właściciela z dotychczasowego loginu")
    return user


def authenticate(db: Session, login: str, password: str) -> User | None:
    """Sprawdza login i haslo. Po 5 blednych probach konto jest blokowane na 15 minut."""
    user = _by_login(db, login)
    if user is None:
        if hmac.compare_digest(login.strip().lower(), config.ADMIN_LOGIN.lower()):
            user = _bootstrap_owner(db, config.ADMIN_LOGIN, password)
            if user is not None:
                user.last_login_at = now()
                db.commit()
            return user
        pw.hash_password("x")  # taki sam czas odpowiedzi dla nieznanego loginu
        return None
    if user.locked_until and user.locked_until > now():
        return None
    org = db.get(Organisation, user.organisation_id)
    if not pw._verify_hash(password, user.password_hash) or not user.active or org is None or org.status != "active":
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until = now() + LOCK_TIME
            user.failed_logins = 0
        db.commit()
        return None
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now()
    db.commit()
    return user


def start_session(request: Request, user: User) -> None:
    request.session["user"] = user.login
    request.session["user_id"] = user.id
    request.session["org_id"] = user.organisation_id
    request.session["role"] = user.role


def refresh_session(db: Session, request: Request) -> bool:
    """Wywolywane przy kazdym zapytaniu zalogowanej osoby. False = sesja niewazna (konto wylaczone)."""
    uid = request.session.get("user_id")
    if uid is None:
        # Sesja sprzed etapu B (tylko "user"): dopinamy konto o tym loginie albo zakladamy wlasciciela.
        login = request.session.get("user", "")
        user = _by_login(db, login)
        if user is None and hmac.compare_digest(login.lower(), config.ADMIN_LOGIN.lower()):
            ensure_default_org(db)
            org = db.scalar(select(Organisation).order_by(Organisation.id))
            row = db.get(AppSetting, pw.KEY)
            if org is not None:
                user = User(organisation_id=org.id, login=login.lower(), name="Właściciel", role="wlasciciel",
                            password_hash=row.value if row and row.value else pw.hash_password(config.ADMIN_PASSWORD))
                db.add(user)
                db.commit()
        if user is None:
            return False
    else:
        user = db.get(User, uid)
    if user is None or not user.active:
        return False
    org = db.get(Organisation, user.organisation_id)
    if org is None or org.status != "active":
        return False
    start_session(request, user)
    return True


def change_password(db: Session, user: User, old: str, new: str, repeat: str) -> None:
    if not pw._verify_hash(old, user.password_hash):
        raise ValueError("Obecne hasło jest niepoprawne.")
    if new != repeat:
        raise ValueError("Nowe hasła nie są takie same.")
    if len(new) < pw.MIN_LEN:
        raise ValueError(f"Nowe hasło musi mieć co najmniej {pw.MIN_LEN} znaków.")
    if new == old:
        raise ValueError("Nowe hasło musi być inne niż obecne.")
    user.password_hash = pw.hash_password(new)
    user.must_change_password = False


# --- zarzadzanie uzytkownikami (tylko wlasciciel) -----------------------------------------


def _owner(request: Request, db: Session) -> User:
    user = db.get(User, request.session.get("user_id") or 0)
    if user is None or user.role != "wlasciciel":
        raise HTTPException(403, "Użytkownikami zarządza tylko właściciel.")
    return user


def _member(db: Session, owner: User, user_id: int) -> User:
    u = db.get(User, user_id)
    if u is None or u.organisation_id != owner.organisation_id:
        raise HTTPException(404)
    return u


def _owners_left(db: Session, org_id: int, without: int) -> int:
    return db.scalar(select(func.count(User.id)).where(
        User.organisation_id == org_id, User.role == "wlasciciel", User.active.is_(True), User.id != without)) or 0


def _page(request: Request, db: Session, owner: User, **ctx):
    from .main import render  # unikamy importu cyklicznego

    people = db.scalars(select(User).where(User.organisation_id == owner.organisation_id).order_by(User.login)).all()
    return render(request, db, "users.html", people=people, roles=ROLES, role_hints=ROLE_HINTS, me=owner, **ctx)


@router.get("/ustawienia/uzytkownicy")
def users_list(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, _owner(request, db))


@router.post("/ustawienia/uzytkownicy")
def users_add(request: Request, login: str = Form(""), name: str = Form(""), email: str = Form(""),
              role: str = Form("handlowiec"), password: str = Form(""), db: Session = Depends(get_db)):
    owner = _owner(request, db)
    login = login.strip().lower()
    error = None
    if not LOGIN_RE.match(login):
        error = "Login: 3–80 znaków, małe litery, cyfry, kropka, myślnik, @."
    elif _by_login(db, login) is not None:
        error = "Taki login już istnieje."
    elif role not in ROLES:
        error = "Wybierz rolę."
    elif len(password) < pw.MIN_LEN:
        error = f"Hasło startowe musi mieć co najmniej {pw.MIN_LEN} znaków."
    if error:
        return _page(request, db, owner, error=error, form=dict(login=login, name=name, email=email, role=role))
    db.add(User(organisation_id=owner.organisation_id, login=login, name=name.strip()[:200], email=email.strip()[:200],
                role=role, password_hash=pw.hash_password(password), must_change_password=True))
    audit(db, f"www ({owner.login})", "user.add", f"Dodano użytkownika {login} ({ROLES[role]})")
    db.commit()
    request.session["flash"] = f"Dodano użytkownika {login}. Przy pierwszym logowaniu zmieni hasło."
    return RedirectResponse("/ustawienia/uzytkownicy", status_code=303)


@router.post("/ustawienia/uzytkownicy/{user_id}")
def users_edit(user_id: int, request: Request, name: str = Form(""), email: str = Form(""), role: str = Form(""),
               active: str = Form(""), db: Session = Depends(get_db)):
    owner = _owner(request, db)
    u = _member(db, owner, user_id)
    new_role = role if role in ROLES else u.role
    new_active = active == "1"
    if (u.role == "wlasciciel" and (new_role != "wlasciciel" or not new_active)
            and _owners_left(db, owner.organisation_id, u.id) == 0):
        return _page(request, db, owner, error="Organizacja musi mieć co najmniej jednego aktywnego właściciela.")
    u.name, u.email, u.role, u.active = name.strip()[:200], email.strip()[:200], new_role, new_active
    audit(db, f"www ({owner.login})", "user.edit",
          f"Użytkownik {u.login}: rola {ROLES[u.role]}, {'aktywny' if u.active else 'wyłączony'}")
    db.commit()
    request.session["flash"] = f"Zapisano: {u.login}."
    return RedirectResponse("/ustawienia/uzytkownicy", status_code=303)


@router.post("/ustawienia/uzytkownicy/{user_id}/haslo")
def users_reset_password(user_id: int, request: Request, password: str = Form(""), db: Session = Depends(get_db)):
    owner = _owner(request, db)
    u = _member(db, owner, user_id)
    if len(password) < pw.MIN_LEN:
        return _page(request, db, owner, error=f"Hasło musi mieć co najmniej {pw.MIN_LEN} znaków.")
    u.password_hash = pw.hash_password(password)
    u.must_change_password = u.id != owner.id
    u.failed_logins, u.locked_until = 0, None
    audit(db, f"www ({owner.login})", "user.password", f"Nadano nowe hasło użytkownikowi {u.login}")
    db.commit()
    request.session["flash"] = f"Nowe hasło dla {u.login} zapisane."
    return RedirectResponse("/ustawienia/uzytkownicy", status_code=303)
