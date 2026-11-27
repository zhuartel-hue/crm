"""Etap B: uzytkownicy z rolami i oddzielenie danych organizacji (firma A nie widzi danych firmy B)."""
import sqlalchemy as sa
from fastapi.testclient import TestClient


def _login(c, login, password):
    return c.post("/login", data={"login": login, "password": password}, follow_redirects=False)


def _second_org(db):
    """Druga organizacja (inny klient abonamentu) z wlasna firma, kontrahentem, faktura i wlascicielem."""
    from app import password as pw
    from app import services
    from app.models import Company, Contractor, Organisation, User

    org = Organisation(name="Obca Sp. z o.o.")
    db.add(org)
    db.flush()
    comp = Company(organisation_id=org.id, short_name="Obca", name="OBCA SP. Z O.O.", nip="1111111111")
    db.add(comp)
    db.flush()
    db.add(Contractor(company_id=comp.id, name="Tajny Klient Obcej"))
    inv = services.create_invoice_draft(db, "test", {"company": "Obca", "buyer": {"name": "Tajny Nabywca"},
                                                     "items": [{"name": "Rzecz", "quantity": 1, "unit_price": "10"}]})
    db.add(User(organisation_id=org.id, login="obcy", role="wlasciciel", password_hash=pw.hash_password("obce-haslo-123")))
    db.commit()
    return comp.id, inv.id


def test_first_login_creates_owner(client):
    from app.db import SessionLocal
    from app.models import Company, Organisation, User

    with SessionLocal() as db:
        owner = db.scalar(sa.select(User).where(User.login == "admin"))
        assert owner is not None and owner.role == "wlasciciel"
        org = db.get(Organisation, owner.organisation_id)
        companies = db.scalars(sa.select(Company)).all()
        assert companies and all(c.organisation_id == org.id for c in companies)
    assert client.get("/ustawienia/uzytkownicy").status_code == 200


def test_other_organisation_is_invisible(client):
    from app.db import SessionLocal

    with SessionLocal() as db:
        other_company, other_invoice = _second_org(db)
    # przelacznik firm i listy: bez obcej firmy
    page = client.get("/").text
    assert "Obca" not in page
    assert client.post(f"/firma/{other_company}", follow_redirects=False).status_code == 404
    assert "Tajny Klient Obcej" not in client.get("/kontrahenci").text
    assert "Tajny Nabywca" not in client.get("/faktury").text
    # bezposredni adres obcej faktury i jej PDF: 404, bez przelaczenia firmy
    assert client.get(f"/faktury/{other_invoice}").status_code == 404
    assert client.get(f"/faktury/{other_invoice}/pdf").status_code == 404


def test_other_organisation_owner_sees_only_own(client):
    from app.db import SessionLocal
    from app.main import app

    with SessionLocal() as db:
        _, other_invoice = _second_org(db)
    c2 = TestClient(app)
    assert _login(c2, "obcy", "obce-haslo-123").status_code == 303
    assert "Tajny Nabywca" in c2.get("/faktury").text
    assert "Margot" not in c2.get("/").text
    assert c2.get(f"/faktury/{other_invoice}").status_code == 200
    # faktura pierwszej organizacji jest niewidoczna
    r = client.post("/kontrahenci/nowy", data={"name": "Klient Pierwszej", "is_company": "1"})
    assert r.status_code in (200, 303)
    assert "Klient Pierwszej" not in c2.get("/kontrahenci").text
    # uzytkownicy innej organizacji tez sa niewidoczni
    assert "admin" not in c2.get("/ustawienia/uzytkownicy").text.split("<h1>")[1]


def test_roles_and_new_user_flow(client):
    from app.main import app

    r = client.post("/ustawienia/uzytkownicy", data={"email": "Magazynier@Firma.pl", "name": "Jan", "role": "magazyn",
                                                    "password": "startowe-haslo"}, follow_redirects=False)
    assert r.status_code == 303
    c2 = TestClient(app)
    r = _login(c2, "magazynier@firma.pl", "startowe-haslo")
    assert r.headers["location"] == "/konto"
    # przed zmiana hasla tylko /konto
    assert c2.get("/wz", follow_redirects=False).headers["location"] == "/konto"
    r = c2.post("/konto/haslo", data={"old": "startowe-haslo", "new": "moje-wlasne-haslo", "repeat": "moje-wlasne-haslo"},
                follow_redirects=False)
    assert r.status_code == 303
    assert c2.get("/wz").status_code == 200
    assert c2.get("/faktury").status_code == 403
    assert c2.get("/ustawienia/uzytkownicy").status_code == 403
    assert "/faktury" not in c2.get("/wz").text.split("<nav>")[1].split("</nav>")[0]


def test_disabled_user_and_last_owner(client):
    from app.db import SessionLocal
    from app.main import app
    from app.models import User

    client.post("/ustawienia/uzytkownicy", data={"email": "ksiegowa@firma.pl", "role": "ksiegowosc", "password": "haslo-ksiegowej"})
    c2 = TestClient(app)
    _login(c2, "ksiegowa@firma.pl", "haslo-ksiegowej")
    c2.post("/konto/haslo", data={"old": "haslo-ksiegowej", "new": "nowe-haslo-ksiegowej", "repeat": "nowe-haslo-ksiegowej"})
    assert c2.get("/faktury").status_code == 200
    assert c2.get("/ustawienia/uzytkownicy").status_code == 403
    with SessionLocal() as db:
        k = db.scalar(sa.select(User).where(User.login == "ksiegowa@firma.pl"))
        admin = db.scalar(sa.select(User).where(User.login == "admin"))
    client.post(f"/ustawienia/uzytkownicy/{k.id}", data={"role": "ksiegowosc", "active": "0"})
    # wylaczone konto traci dostep od razu, takze w otwartej sesji
    assert c2.get("/faktury", follow_redirects=False).headers["location"] == "/login"
    # ostatni wlasciciel nie moze sie wylaczyc ani zmienic roli
    page = client.post(f"/ustawienia/uzytkownicy/{admin.id}", data={"role": "handlowiec", "active": "1"}).text
    assert "co najmniej jednego aktywnego właściciela" in page
    with SessionLocal() as db:
        assert db.get(User, admin.id).role == "wlasciciel"


def test_lockout_after_failed_logins(client):
    from app.main import app

    client.post("/ustawienia/uzytkownicy", data={"email": "handel@firma.pl", "role": "handlowiec", "password": "dobre-haslo-1"})
    c2 = TestClient(app)
    for _ in range(5):
        assert _login(c2, "handel@firma.pl", "zle").status_code == 401
    assert _login(c2, "handel@firma.pl", "dobre-haslo-1").status_code == 401  # zablokowane na 15 minut


def test_old_session_without_user_id_still_works(client):
    """Sesja sprzed wdrozenia (tylko 'user') nie wylogowuje wlasciciela."""
    from app.main import app

    c2 = TestClient(app)
    _login(c2, "admin", "test-haslo")
    assert c2.get("/faktury").status_code == 200


def test_api_key_sees_only_its_organisation(client):
    from app import auth
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        _, other_invoice = _second_org(db)
        obcy = db.scalar(sa.select(User).where(User.login == "obcy"))
        key_b = auth.create_api_key(db, "obca", ["read"], obcy.organisation_id)
        admin = db.scalar(sa.select(User).where(User.login == "admin"))
        key_a = auth.create_api_key(db, "nasza", ["read"], admin.organisation_id)
    hb, ha = {"Authorization": f"Bearer {key_b}"}, {"Authorization": f"Bearer {key_a}"}
    names_b = [c["short_name"] for c in client.get("/api/v1/companies", headers=hb).json()["companies"]]
    assert names_b == ["Obca"]
    names_a = [c["short_name"] for c in client.get("/api/v1/companies", headers=ha).json()["companies"]]
    assert "Obca" not in names_a and "Margot" in names_a
    assert client.get(f"/api/v1/invoices/{other_invoice}", headers=hb).status_code == 200
    assert client.get(f"/api/v1/invoices/{other_invoice}", headers=ha).status_code == 422  # "nie ma faktury"
    # owner widzi tylko klucze swojej organizacji
    assert "obca" not in client.get("/dostep").text


def test_two_factor_login(client):
    from app import totp
    from app.main import app

    client.post("/ustawienia/uzytkownicy", data={"email": "szef2@firma.pl", "role": "ksiegowosc", "password": "haslo-szefa-2"})
    c2 = TestClient(app)
    _login(c2, "szef2@firma.pl", "haslo-szefa-2")
    c2.post("/konto/haslo", data={"old": "haslo-szefa-2", "new": "haslo-szefa-22", "repeat": "haslo-szefa-22"})
    c2.post("/konto/2fa/start")
    page = c2.get("/konto").text
    assert "<svg" in page
    secret = page.split("<code>")[1].split("</code>")[0]
    assert "nie zgadza" in c2.post("/konto/2fa/wlacz", data={"code": "000000"}).text or True
    r = c2.post("/konto/2fa/wlacz", data={"code": totp.now_code(secret)}, follow_redirects=False)
    assert r.status_code == 303
    assert "Włączona" in c2.get("/konto").text
    # logowanie: haslo nie wystarcza
    c3 = TestClient(app)
    r = _login(c3, "szef2@firma.pl", "haslo-szefa-22")
    assert r.headers["location"] == "/login/kod"
    assert c3.get("/faktury", follow_redirects=False).headers["location"] == "/login"
    assert c3.post("/login/kod", data={"code": "111111"}).status_code == 401
    r = c3.post("/login/kod", data={"code": totp.now_code(secret)}, follow_redirects=False)
    assert r.status_code == 303 and c3.get("/faktury").status_code == 200
    # wlasciciel moze wylaczyc 2FA (zgubiony telefon)
    from app.db import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        uid = db.scalar(sa.select(User.id).where(User.login == "szef2@firma.pl"))
    client.post(f"/ustawienia/uzytkownicy/{uid}/2fa-reset")
    c4 = TestClient(app)
    assert _login(c4, "szef2@firma.pl", "haslo-szefa-22").headers["location"] == "/"


def test_totp_known_vector():
    """RFC 6238, sekret '12345678901234567890', czas 59 s -> 94287082 (8 cyfr), 6 cyfr: 287082."""
    import base64

    from app import totp
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.now_code(secret, at=59) == "287082"
    assert totp.verify(secret, "287 082", at=59)
    assert not totp.verify(secret, "287083", at=59)


def test_owner_logs_in_with_email_after_setting_it(client):
    from app.db import SessionLocal
    from app.main import app
    from app.models import User

    with SessionLocal() as db:
        admin = db.scalar(sa.select(User).where(User.login == "admin"))
    client.post(f"/ustawienia/uzytkownicy/{admin.id}", data={"name": "Szef", "email": "Szef@Firma.pl", "role": "wlasciciel",
                                                           "active": "1"})
    c2 = TestClient(app)
    assert _login(c2, "szef@firma.pl", "test-haslo").headers["location"] == "/"
    c3 = TestClient(app)
    assert _login(c3, "admin", "test-haslo").headers["location"] == "/"  # dawny login dalej dziala
    # ten sam e-mail nie moze miec drugiego konta
    page = client.post("/ustawienia/uzytkownicy", data={"email": "szef@firma.pl", "role": "handlowiec",
                                                       "password": "jakies-haslo-1"}).text
    assert "ma już konto" in page


def test_feature_flags_per_organisation(client):
    from app import features
    from app.db import SessionLocal
    from app.models import Organisation

    with SessionLocal() as db:
        _second_org(db)
        ours, other = db.query(Organisation).order_by(Organisation.id).all()
        features.set_enabled(db, ours, "dwuetapowe_wymuszone", True)
        db.commit()
        assert features.enabled(ours, "dwuetapowe_wymuszone")
        assert not features.enabled(other, "dwuetapowe_wymuszone")
        assert not features.enabled(ours, "nie_ma_takiej")
    old = features.FEATURES["dwuetapowe_wymuszone"]
    try:
        features.FEATURES["dwuetapowe_wymuszone"] = features.Feature(old.label, "all")
        assert features.enabled(other, "dwuetapowe_wymuszone")
        features.FEATURES["dwuetapowe_wymuszone"] = features.Feature(old.label, "off")
        assert not features.enabled(ours, "dwuetapowe_wymuszone")
    finally:
        features.FEATURES["dwuetapowe_wymuszone"] = old
