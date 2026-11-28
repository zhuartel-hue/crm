"""Zrzuty ekranu dla asystenta AI (okienko Pomoc) i bazy wiedzy: robi je sam, na danych testowych.

Uruchom po kazdej zmianie wygladu (albo przed wydaniem):

    python tools/zrzuty_pomocy.py            # wszystkie zrzuty
    python tools/zrzuty_pomocy.py faktura-nowa ustawienia-logo

Co robi:
1. Startuje Sygnavo na pustej, tymczasowej bazie SQLite z firmami i klientami testowymi (zadnych prawdziwych danych).
2. W Chromium (Playwright) otwiera kazda strone z listy SCREENS, podswietla elementy do klikniecia
   ta sama funkcja co przycisk „Pokaż na ekranie” (SygPomoc.highlight z app/static/pomoc.js): czerwona ramka i numer.
3. Zapisuje PNG i opis do docs/wiedza/zrzuty/ (zrzuty.json czyta asystent).
Gdy przycisku nie ma na stronie (zmienil sie wyglad), skrypt konczy sie bledem i mowi, ktory zrzut poprawic.

Nowy zrzut: dopisz go do SCREENS (id, tytul, strona, kroki: css + opcjonalny tekst + opis dla asystenta).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "wiedza" / "zrzuty"
WIDTH = 1280

# id: krotki, z malych liter i myslnikow. steps: css (selektor), text (fragment napisu), label (opis dla asystenta).
SCREENS = [
    {"id": "pomoc-okienko", "title": "Przycisk Pomoc", "page": "/",
     "steps": [{"css": ".pz-btn", "label": "przycisk Pomoc (znak zapytania) w prawym dolnym rogu"}]},
    {"id": "firma-przelacznik", "title": "Przełącznik firm", "page": "/",
     "steps": [{"css": ".switch button", "label": "przełącznik firm u góry menu"}]},
    {"id": "faktura-nowa", "title": "Lista faktur: nowa faktura", "page": "/faktury",
     "steps": [{"css": "a.btn", "text": "+ Faktura", "label": "przycisk + Faktura"}]},
    {"id": "faktura-formularz", "title": "Formularz faktury", "page": "/faktury/nowa",
     "steps": [{"css": "#buyer_nip", "label": "pole NIP nabywcy"},
               {"css": "button", "text": "Pobierz po NIP", "label": "Pobierz po NIP (dane z białej listy VAT)"},
               {"css": "#add", "label": "+ Pozycja (kolejny towar lub usługa)"},
               {"css": "button[value=issue]", "label": "Wystaw fakturę (nadaje numer)"}]},
    {"id": "ustawienia-dane-firmy", "title": "Ustawienia: dane firmy na fakturze", "page": "/ustawienia",
     "steps": [{"css": "input[name=nip]", "label": "NIP firmy"},
               {"css": "input[name=bank_account]", "label": "numer konta na fakturze"}]},
    {"id": "ustawienia-logo", "title": "Ustawienia: logo i wygląd faktury", "page": "/ustawienia",
     "steps": [{"css": "input[name=logo]", "label": "Wybierz plik z logo (PNG lub JPG)"},
               {"css": "button", "text": "Zapisz wygląd", "label": "Zapisz wygląd"}]},
    {"id": "ustawienia-ksef", "title": "Ustawienia: KSeF", "page": "/ustawienia",
     "steps": [{"css": "select[name=ksef_env]", "label": "Środowisko KSeF (testowe albo produkcyjne)"},
               {"css": "select[name=ksef_auth]", "label": "Logowanie: certyfikat albo token"},
               {"css": "button", "text": "Zapisz KSeF", "label": "Zapisz KSeF"}]},
    {"id": "ustawienia-poczta", "title": "Ustawienia: przejście do skrzynek pocztowych", "page": "/ustawienia",
     "steps": [{"css": "a.btn", "text": "Skrzynki pocztowe", "label": "Skrzynki pocztowe"}]},
    {"id": "poczta-skrzynka", "title": "Nowa skrzynka pocztowa", "page": "/ustawienia/poczta",
     "steps": [{"css": "input[name=email]", "label": "adres e-mail skrzynki"},
               {"css": "input[name=imap_host]", "label": "serwer IMAP (od dostawcy poczty)"},
               {"css": "button", "text": "Zapisz skrzynkę", "label": "Zapisz skrzynkę"}]},
    {"id": "uzytkownik-nowy", "title": "Dodawanie użytkownika", "page": "/ustawienia/uzytkownicy",
     "steps": [{"css": "input[name=email][type=email]", "label": "e-mail nowej osoby (to jej login)"},
               {"css": "form:has(input[type=email]) select[name=role]", "label": "rola (co widzi w programie)"},
               {"css": "button", "text": "Dodaj", "label": "Dodaj"}]},
    {"id": "kontrahent-nowy", "title": "Kontrahenci: nowy kontrahent", "page": "/kontrahenci",
     "steps": [{"css": "a.btn", "text": "+ Kontrahent", "label": "przycisk + Kontrahent"}]},
    {"id": "kontrahent-notatki", "title": "Karta kontrahenta: notatki (wpisz i zmień w miejscu)", "page": "/kontrahenci/1#notatki",
     "steps": [{"css": ".note-pin textarea", "label": "stała notatka: kliknij i pisz, zapisuje się po wyjściu z pola"},
               {"css": "#notatki ~ form textarea[name=text]", "label": "nowa notatka (Ctrl+Enter zapisuje)"},
               {"css": ".note-edit", "label": "Edytuj (albo kliknij w treść notatki), potem Zapisz"}]},
    {"id": "produkt-nowy", "title": "Produkty: nowy produkt", "page": "/produkty",
     "steps": [{"css": "a.btn", "text": "+ Produkt", "label": "przycisk + Produkt"}]},
    {"id": "oferta-nowa", "title": "Oferty: nowa oferta", "page": "/oferty",
     "steps": [{"css": "a.btn", "text": "+ Oferta", "label": "przycisk + Oferta"}]},
    {"id": "wz-faktura", "title": "Magazyn: faktura z kilku WZ", "page": "/wz",
     "steps": [{"css": "select[name=rodzaj]", "label": "filtr: wybierz WZ bez faktury"},
               {"css": "button", "text": "Faktura z zaznaczonych WZ", "label": "Faktura z zaznaczonych WZ"}]},
    {"id": "koszty-dodaj", "title": "Koszty: faktura kosztowa spoza KSeF", "page": "/koszty",
     "steps": [{"css": "a.btn", "text": "Dodaj koszt", "label": "Dodaj koszt"}]},
    {"id": "raport-sprzedazy", "title": "Raport sprzedaży dla księgowej", "page": "/raporty/sprzedaz",
     "steps": [{"css": "a.btn", "text": "Poprzedni miesiąc", "label": "Poprzedni miesiąc"},
               {"css": "a.btn", "text": "PDF", "label": "PDF (albo Drukuj / Excel obok)"}]},
    {"id": "bank-polaczenie", "title": "Bank: podłączenie konta", "page": "/bank",
     "steps": [{"css": "a.btn", "text": "bank", "label": "Połącz z bankiem"}]},
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _demo_data() -> None:
    """Firmy i klienci testowi (fikcyjne nazwy i NIP-y)."""
    from sqlalchemy import select

    from app import features, services
    from app.db import SessionLocal
    from app.models import Company, Contractor, ContractorNote, Organisation, Product

    with SessionLocal() as db:
        demo = [("Demo", "DEMO SPÓŁKA Z O.O.", "1234563218"), ("Przykład JDG", "JAN PRZYKŁADOWY", "5260250274")]
        for c, (short, name, nip) in zip(db.scalars(select(Company).order_by(Company.id)).all(), demo):
            c.short_name, c.name, c.nip = short, name, nip
            c.street, c.postal_code, c.city = "ul. Przykładowa 1", "00-001", "Warszawa"
            c.bank_account, c.bank_name, c.email, c.phone = "12 1020 0000 0000 0000 0000 0000", "Bank Demo", "biuro@example.com", ""
        db.commit()
        first = db.scalar(select(Company).order_by(Company.id))
        for name in ("Hurtownia Przykład Sp. z o.o.", "Anna Testowa", "Serwis Demo s.c."):
            db.add(Contractor(company_id=first.id, name=name, city="Kraków"))
        db.flush()
        hurt = db.scalar(select(Contractor).where(Contractor.name.like("Hurtownia%")))
        hurt.notes = "Płaci przelewem 14 dni. Decyzje: pan Nowak."
        db.add(ContractorNote(contractor_id=hurt.id, author="admin", at=dt.datetime(2026, 11, 27, 10, 15),
                              text="Rozmowa telefoniczna: chce ofertę na serwis kwartalny."))
        db.add(Product(company_id=first.id, name="Usługa serwisowa", unit="szt.", price_net=150, vat_rate="23",
                       is_service=True))
        db.commit()
        for buyer, price in (("Hurtownia Przykład Sp. z o.o.", "1200"), ("Anna Testowa", "350")):
            services.create_invoice_draft(db, "zrzuty", {"company": first.short_name, "buyer": {"name": buyer},
                                                         "items": [{"name": "Usługa serwisowa", "quantity": 1, "unit_price": price}]})
        for org in db.scalars(select(Organisation)).all():
            features.set_enabled(db, org, "asystent_ai", True)
        db.commit()


def main(only: list[str]) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="zrzuty-"))
    os.environ.update({
        "DATABASE_URL": f"sqlite:///{tmp / 'demo.db'}", "ADMIN_PASSWORD": "demo-haslo-zrzuty", "SECRET_KEY": "zrzuty",
        "MAIL_SYNC_MINUTES": "0", "KSEF_PURCHASE_SYNC_HOURS": "0", "RECURRING_CHECK_MINUTES": "0", "BANK_SYNC_HOURS": "0",
        "BACKGROUND_JOBS": "off",
    })
    sys.path.insert(0, str(ROOT))
    import uvicorn
    from playwright.sync_api import sync_playwright

    from app.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    _demo_data()
    base = f"http://127.0.0.1:{port}"
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "zrzuty.json"
    old = {s["id"]: s for s in json.loads(manifest_path.read_text("utf-8")).get("screens", [])} if manifest_path.exists() else {}
    errors, done = [], {}
    with sync_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")  # np. /opt/pw-browsers/chromium, gdy przegladarka jest gdzie indziej
        args = ["--lang=pl-PL"]  # polskie napisy przegladarki (pole pliku, daty)
        browser = (p.chromium.launch(executable_path=exe, args=args) if exe else p.chromium.launch(args=args))
        page = browser.new_page(viewport={"width": WIDTH, "height": 800}, device_scale_factor=1, locale="pl-PL",
                                color_scheme="light")
        page.goto(base + "/login")
        page.fill("input[name=login]", "admin")
        page.fill("input[name=password]", "demo-haslo-zrzuty")
        page.click("button")
        page.wait_for_load_state("networkidle")
        for s in SCREENS:
            if only and s["id"] not in only:
                continue
            page.set_viewport_size({"width": WIDTH, "height": 800})
            page.goto(base + s["page"])
            page.wait_for_load_state("networkidle")
            # Okno tak wysokie jak strona: menu boczne i przycisk Pomoc sa na swoich miejscach, bez przewijania.
            full = min(page.evaluate("() => document.documentElement.scrollHeight"), 3000)
            page.set_viewport_size({"width": WIDTH, "height": max(800, full)})
            missing = page.evaluate("(steps) => window.SygPomoc.highlight(steps, {keep: true, scroll: false})", s["steps"])
            if missing:
                errors.append(f"{s['id']}: na stronie {s['page']} brak elementów nr {missing}")
                continue
            boxes = page.evaluate("() => Array.from(document.querySelectorAll('.pz-hl')).map(b => b.getBoundingClientRect())"
                                  ".map(r => [r.top + window.scrollY, r.bottom + window.scrollY])")
            vh = max(800, full)
            top = max(0, min(b[0] for b in boxes) - 160)
            height = min(max(520, max(b[1] for b in boxes) + 160 - top), 1400)
            if top + height > vh:
                top = max(0, vh - height)
            image = s["id"] + ".png"
            page.screenshot(path=str(OUT / image), clip={"x": 0, "y": top, "width": WIDTH, "height": height})
            done[s["id"]] = {**s, "image": image}
        browser.close()
    server.should_exit = True
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    screens = []
    for s in SCREENS:
        if s["id"] in done:
            screens.append(done[s["id"]])
        elif s["id"] in old and (OUT / old[s["id"]]["image"]).exists():
            screens.append({**s, "image": old[s["id"]]["image"]})  # nie odswiezany teraz: zostaje poprzedni plik
    manifest_path.write_text(json.dumps({"generated": dt.date.today().isoformat(), "commit": commit, "screens": screens},
                                        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Zrzuty: {len(done)} zapisane w {OUT.relative_to(ROOT)}")
    for e in errors:
        print("BŁĄD:", e)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
