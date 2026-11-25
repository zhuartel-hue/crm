"""Aplikacja www: faktury, kontrahenci, auta, ustawienia firm."""
from __future__ import annotations

import datetime as dt
import json
import re
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import api, auth, calc, config, mcp_server, migrate, numbering, password, payments, pdf, secretbox, wz
from . import products as prod
from .bank import service as bank
from .mail import service as mail
from .mail.service import MailError
from .bank.mt940 import MT940Error
from .ksef import ENVIRONMENTS as KSEF_ENVS
from .ksef import qr as ksef_qr
from .ksef import service as ksef
from .ksef import incoming as ksef_incoming
from .ksef import purchases
from . import services
from .services import ServiceError, audit, car_item_name
from .db import SessionLocal, get_db
from .models import (
    CAR_STATUSES,
    DELIVERY_KINDS,
    SCOPES,
    ApiToken,
    AuditLog,
    BankTransaction,
    MailAccount,
    MailAttachment,
    MailMessage,
    INVOICE_KINDS,
    NEW_KINDS,
    PAYMENT_METHODS,
    VAT_RATES,
    Car,
    Company,
    Contractor,
    Delivery,
    DeliveryItem,
    Invoice,
    InvoiceItem,
    Product,
    ProductPhoto,
    PURCHASE_STATUSES,
    PurchaseInvoice,
    StockMovement,
)
from .seed import seed

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["money"] = calc.format_money
templates.env.filters["qty"] = calc.format_qty
templates.env.filters["plnwords"] = calc.amount_in_words
templates.env.filters["date"] = lambda d: d.strftime("%d.%m.%Y") if d else ""
templates.env.globals.update(
    INVOICE_KINDS=INVOICE_KINDS, NEW_KINDS=NEW_KINDS, PAYMENT_METHODS=PAYMENT_METHODS, VAT_RATES=VAT_RATES, CAR_STATUSES=CAR_STATUSES
)

templates.env.globals["ksef_cert_info"] = ksef.cert_info


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate.upgrade()
    with SessionLocal() as db:
        seed(db)
    stops = [_start_mail_sync(), _start_purchase_sync()]
    yield
    for stop in stops:
        if stop is not None:
            stop.set()


def _start_mail_sync():
    """Pobieranie poczty w tle co MAIL_SYNC_MINUTES minut (jeden proces uvicorn)."""
    import logging
    import threading

    minutes = config.MAIL_SYNC_MINUTES
    if minutes <= 0:
        return None
    stop = threading.Event()

    def loop():
        while not stop.wait(minutes * 60):
            try:
                with SessionLocal() as db:
                    mail.sync_all(db)
            except Exception:  # noqa: BLE001
                logging.getLogger("sygnavo.mail").exception("Pobieranie poczty nie powiodło się")

    threading.Thread(target=loop, name="mail-sync", daemon=True).start()
    return stop


def _start_purchase_sync():
    """Faktury kosztowe z KSeF w tle co KSEF_PURCHASE_SYNC_HOURS godzin (dla firm z wlaczonym KSeF)."""
    import logging
    import threading

    hours = config.KSEF_PURCHASE_SYNC_HOURS
    if hours <= 0:
        return None
    stop = threading.Event()

    def loop():
        while not stop.wait(hours * 3600):
            try:
                with SessionLocal() as db:
                    purchases.fetch_all(db)
            except Exception:  # noqa: BLE001
                logging.getLogger("sygnavo.ksef").exception("Pobieranie faktur kosztowych nie powiodło się")

    threading.Thread(target=loop, name="ksef-purchases", daemon=True).start()
    return stop


app = FastAPI(title="Sygnavo", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

PUBLIC_PATHS = ("/login", "/static", "/health", "/sw.js", "/api/", "/mcp", "/oauth/", "/.well-known/")


@app.middleware("http")
async def require_login(request: Request, call_next):
    if not request.url.path.startswith(PUBLIC_PATHS) and not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    response = await call_next(request)
    # Strona zgody OAuth i formularze nie moga byc osadzone w cudzej ramce (clickjacking).
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


app.include_router(auth.router)
app.include_router(mcp_server.router)
api.install(app)

# SessionMiddleware musi byc dodany po require_login, zeby dzialal przed nim.
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    https_only=config.COOKIE_SECURE,
    same_site="lax",
    max_age=60 * 60 * 24 * 30,
)


def script_json(value) -> str:
    """JSON do wstawienia w <script>: bez "</script>" i znakow HTML (nazwy produktow z importu)."""
    return (json.dumps(value, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def current_company(request: Request, db: Session) -> Company:
    companies = db.scalars(select(Company).order_by(Company.id)).all()
    cid = request.session.get("company_id")
    for c in companies:
        if c.id == cid:
            return c
    request.session["company_id"] = companies[0].id
    return companies[0]


def render(request: Request, db: Session, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("company", current_company(request, db))
    ctx["companies"] = db.scalars(select(Company).order_by(Company.id)).all()
    ctx["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(request, name, ctx)


def web_actor(request: Request) -> str:
    return f"www ({request.session.get('user', '?')})"


def flash(request: Request, text: str) -> None:
    request.session["flash"] = text


# --- logowanie ---------------------------------------------------------


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, login: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if not password_check(db, login, password):
        return templates.TemplateResponse(request, "login.html", {"error": "Zły login lub hasło."}, status_code=401)
    nxt = request.session.pop("next", None)
    request.session["user"] = login
    return redirect(nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else "/")


def password_check(db: Session, login: str, pw: str) -> bool:
    return password.check_login(db, login, pw)


@app.get("/konto", response_class=HTMLResponse)
def account(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "account.html")


@app.post("/konto/haslo")
def account_password(request: Request, old: str = Form(""), new: str = Form(""), repeat: str = Form(""),
                     db: Session = Depends(get_db)):
    user = request.session.get("user", "")
    try:
        password.change(db, user, old, new, repeat)
    except ValueError as e:
        return render(request, db, "account.html", error=str(e))
    audit(db, web_actor(request), "account.password", "Zmieniono hasło logowania")
    db.commit()
    flash(request, "Hasło zmienione. Od teraz loguj się nowym hasłem.")
    return redirect("/konto")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@app.post("/firma/{company_id}")
def switch_company(company_id: int, request: Request, db: Session = Depends(get_db)):
    if db.get(Company, company_id) is None:
        raise HTTPException(404)
    request.session["company_id"] = company_id
    back = request.headers.get("referer") or "/"
    # Po zmianie firmy nie zostajemy na fakturze innej firmy.
    if "/faktury/" in back:
        back = "/faktury"
    return redirect(back)


# --- pulpit -------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    today = dt.date.today()
    month_start = today.replace(day=1)
    month_invoices = db.scalars(
        select(Invoice).where(
            Invoice.company_id == company.id, Invoice.status == "issued", Invoice.issue_date >= month_start
        )
    ).all()
    recent = db.scalars(
        select(Invoice).where(Invoice.company_id == company.id).order_by(Invoice.id.desc()).limit(8)
    ).all()
    unpaid = db.scalars(
        select(Invoice).where(Invoice.company_id == company.id, Invoice.status == "issued", Invoice.paid < Invoice.total_gross)
    ).all()
    cars_on_lot = db.scalars(
        select(Car).where(Car.company_id == company.id, Car.status != "sold").order_by(Car.id.desc())
    ).all()
    stats = dict(
        month_count=len(month_invoices),
        month_gross=sum((i.total_gross for i in month_invoices), Decimal("0")),
        unpaid_count=len(unpaid),
        unpaid_sum=sum((i.to_pay for i in unpaid), Decimal("0")),
    )
    return render(request, db, "dashboard.html", company=company, recent=recent, stats=stats, cars=cars_on_lot)


# --- faktury --------------------------------------------------------------


@app.get("/faktury", response_class=HTMLResponse)
def invoice_list(request: Request, rodzaj: str = "", miesiac: str = "", q: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    stmt = select(Invoice).where(Invoice.company_id == company.id)
    if rodzaj in INVOICE_KINDS:
        stmt = stmt.where(Invoice.kind == rodzaj)
    if miesiac:
        try:
            y, m = (int(x) for x in miesiac.split("-"))
            start = dt.date(y, m, 1)
            end = dt.date(y + (m == 12), m % 12 + 1, 1)
            stmt = stmt.where(Invoice.issue_date >= start, Invoice.issue_date < end)
        except ValueError:
            pass
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Invoice.number.ilike(like), Invoice.buyer_name.ilike(like), Invoice.buyer_nip.ilike(like)))
    invoices = db.scalars(stmt.order_by(Invoice.issue_date.desc(), Invoice.id.desc())).all()
    total = sum((i.total_gross for i in invoices if i.status == "issued" and i.is_accounting), Decimal("0"))
    return render(request, db, "invoices.html", invoices=invoices, rodzaj=rodzaj, miesiac=miesiac, q=q, total=total)


def _form_context(db: Session, company: Company) -> dict:
    contractors = db.scalars(select(Contractor).order_by(Contractor.name)).all()
    cars = db.scalars(select(Car).where(Car.company_id == company.id).order_by(Car.id.desc())).all()
    contractors_json = json.dumps(
        {
            c.id: dict(name=c.name, nip=c.nip, address="\n".join(c.address_lines))
            for c in contractors
        },
        ensure_ascii=False,
    )
    products = prod.search(db, active_only=True)
    products_json = script_json(prod.picker_json(products))
    return dict(contractors=contractors, cars=cars, contractors_json=contractors_json,
                products=products, products_json=products_json)


@app.get("/faktury/nowa", response_class=HTMLResponse)
def invoice_new(request: Request, rodzaj: str = "vat", auto: int | None = None, db: Session = Depends(get_db)):
    company = current_company(request, db)
    kind = rodzaj if rodzaj in NEW_KINDS else "vat"
    today = dt.date.today()
    inv = Invoice(
        company_id=company.id,
        kind=kind,
        issue_date=today,
        sale_date=today,
        issue_place=company.issue_place or company.city,
        due_date=today + dt.timedelta(days=company.default_payment_days),
        payment_method="transfer",
        prices_gross=True,
        buyer_name="",
        buyer_nip="",
        buyer_address="",
        notes="",
        paid=Decimal("0"),
        correction_reason="",
    )
    inv.items = []
    car = db.get(Car, auto) if auto else None
    if car and car.company_id == company.id:
        inv.car_id = car.id
        inv.items.append(
            InvoiceItem(
                name=car_item_name(car),
                quantity=Decimal("1"),
                unit="szt.",
                unit_price=car.asking_price or Decimal("0"),
                vat_rate="23",
                purchase_price=car.purchase_price,
            )
        )
    return render(request, db, "invoice_form.html", inv=inv, **_form_context(db, company))


def _apply_form(db: Session, inv: Invoice, form) -> None:
    def date(name, required=True):
        v = form.get(name) or ""
        if not v:
            if required:
                raise ValueError("Brak daty.")
            return None
        return dt.date.fromisoformat(v)

    if inv.id is None:  # rodzaj ustala sie przy zakladaniu; korekta i koncowa powstaja z innych faktur
        kind = form.get("kind") or inv.kind or "vat"
        inv.kind = kind if kind in NEW_KINDS else "vat"
    inv.issue_date = date("issue_date")
    inv.sale_date = date("sale_date")
    inv.due_date = date("due_date", required=False)
    inv.issue_place = form.get("issue_place", "").strip()
    inv.payment_method = form.get("payment_method", "transfer")
    if inv.kind != "correction":  # korekta liczy ceny tak jak faktura korygowana
        inv.prices_gross = form.get("prices_gross") == "1"
    inv.paid = calc.parse_decimal(form.get("paid"), Decimal("0"))
    inv.notes = form.get("notes", "").strip()
    inv.buyer_name = form.get("buyer_name", "").strip()
    inv.buyer_nip = form.get("buyer_nip", "").replace("-", "").replace(" ", "").strip()
    inv.buyer_address = form.get("buyer_address", "").strip()
    if inv.kind == "correction":
        inv.correction_reason = form.get("correction_reason", "").strip()
    if inv.kind == "advance":
        inv.advance_gross = calc.parse_decimal(form.get("advance_gross"), None)
    inv.exemption_basis = (form.get("exemption_basis") or "").strip()[:256]
    inv.split_payment = form.get("split_payment") == "1"

    cid = form.get("contractor_id") or ""
    inv.contractor_id = int(cid) if cid.isdigit() else None
    if inv.contractor_id is None and form.get("save_contractor") == "1" and inv.buyer_name:
        lines = inv.buyer_address.splitlines()
        street = lines[0] if lines else ""
        postal, _, city = (lines[1] if len(lines) > 1 else "").partition(" ")
        c = Contractor(
            name=inv.buyer_name, nip=inv.buyer_nip, is_company=bool(inv.buyer_nip),
            street=street, postal_code=postal, city=city,
        )
        db.add(c)
        db.flush()
        inv.contractor_id = c.id

    car_id = form.get("car_id") or ""
    inv.car_id = int(car_id) if car_id.isdigit() else None

    names = form.getlist("item_name")
    qtys = form.getlist("item_qty")
    units = form.getlist("item_unit")
    prices = form.getlist("item_price")
    rates = form.getlist("item_rate")
    product_ids = form.getlist("item_product")
    valid_products = set(db.scalars(select(Product.id)).all()) if any(product_ids) else set()
    # Pozycje "przed korekta" sa kopia faktury korygowanej i nie zmieniaja sie w formularzu.
    for it in [i for i in inv.items if not i.is_before]:
        inv.items.remove(it)
    for i, name in enumerate(names):
        name = name.strip()
        if not name:
            continue
        price = calc.parse_decimal(prices[i] if i < len(prices) else "", None)
        if price is None:
            raise ValueError(f"Brak ceny w pozycji „{name}”.")
        rate = rates[i] if i < len(rates) and rates[i] in VAT_RATES else "23"
        pid = product_ids[i] if i < len(product_ids) else ""
        pid = int(pid) if pid.isdigit() and int(pid) in valid_products else None
        inv.items.append(
            InvoiceItem(
                name=name,
                quantity=calc.parse_decimal(qtys[i] if i < len(qtys) else "", Decimal("1")),
                unit=(units[i] if i < len(units) else "").strip() or "szt.",
                unit_price=price,
                vat_rate=rate,
                product_id=pid,
            )
        )
    calc.recalculate(inv)


async def _save(request: Request, db: Session, inv: Invoice, is_new: bool):
    form = await request.form()
    company = current_company(request, db)
    try:
        _apply_form(db, inv, form)
        if is_new:
            db.add(inv)
        db.flush()
        if form.get("action") == "issue":
            numbering.issue(db, inv)
            audit(db, web_actor(request), "invoice.issue",
                  f"Wystawiono {inv.number} dla {inv.buyer_name} na {calc.format_money(inv.total_gross)} zł",
                  company_id=inv.company_id, entity="invoice", entity_id=inv.id)
        db.commit()
    except ValueError as e:
        db.rollback()
        ctx = _form_context(db, company)
        return render(request, db, "invoice_form.html", inv=inv, error=str(e), **ctx)
    flash(request, f"Wystawiono fakturę {inv.number}." if inv.status == "issued" else "Zapisano szkic.")
    return redirect(f"/faktury/{inv.id}")


@app.post("/faktury/nowa")
async def invoice_create(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    inv = Invoice(company_id=company.id, kind="vat", correction_reason="")
    inv.company = company
    inv.items = []
    return await _save(request, db, inv, is_new=True)


def get_invoice(db: Session, request: Request, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404, "Nie ma takiej faktury.")
    # Link do faktury drugiej firmy (np. od Claude) przelacza firme.
    request.session["company_id"] = inv.company_id
    return inv


@app.get("/faktury/{invoice_id}", response_class=HTMLResponse)
def invoice_view(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    corrections = db.scalars(
        select(Invoice).where(Invoice.related_id == inv.id).order_by(Invoice.issue_date, Invoice.id)
    ).all()
    url = ksef.qr_url(inv)
    bank_payments = db.scalars(select(BankTransaction).where(BankTransaction.invoice_id == inv.id)
                               .order_by(BankTransaction.booking_date)).all()
    deliveries = db.scalars(select(Delivery).where(Delivery.invoice_id == inv.id).order_by(Delivery.id)).all()
    mails = db.scalars(select(MailMessage).where(MailMessage.invoice_id == inv.id)
                       .order_by(MailMessage.date.desc()).limit(20)).all()
    return render(request, db, "invoice_view.html", inv=inv, summary=calc.invoice_summary(inv), derived=corrections,
                  today_iso=dt.date.today().isoformat(),
                  bank_payments=bank_payments, mails=mails, deliveries=deliveries, has_mail=_company_accounts(db, inv.company_id) != [],
                  ksef_enabled=ksef.enabled(inv.company), ksef_can_send=ksef.can_send(inv),
                  ksef_label=ksef.status_label(inv), ksef_url=url, ksef_qr=ksef_qr.svg(url) if url else "")


@app.get("/faktury/{invoice_id}/edytuj", response_class=HTMLResponse)
def invoice_edit(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    if inv.status != "draft":
        flash(request, "Wystawionej faktury nie można zmieniać. Zmiany robi się fakturą korygującą.")
        return redirect(f"/faktury/{inv.id}")
    return render(request, db, "invoice_form.html", inv=inv, **_form_context(db, inv.company))


@app.post("/faktury/{invoice_id}/edytuj")
async def invoice_update(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    if inv.status != "draft":
        raise HTTPException(409, "Faktura jest już wystawiona.")
    return await _save(request, db, inv, is_new=False)


@app.post("/faktury/{invoice_id}/wystaw")
def invoice_issue(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    try:
        numbering.issue(db, inv)
        audit(db, web_actor(request), "invoice.issue",
              f"Wystawiono {inv.number} dla {inv.buyer_name} na {calc.format_money(inv.total_gross)} zł",
              company_id=inv.company_id, entity="invoice", entity_id=inv.id)
        db.commit()
        flash(request, f"Wystawiono fakturę {inv.number}.")
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
    return redirect(f"/faktury/{inv.id}")


@app.post("/faktury/{invoice_id}/zaplacona")
def invoice_mark_paid(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    try:
        p = payments.add_payment(db, inv, inv.to_pay, note="Oznaczono jako zapłaconą")
    except ValueError as e:
        flash(request, str(e))
        return redirect(f"/faktury/{inv.id}")
    audit(db, web_actor(request), "invoice.paid", f"{inv.number}: zapłacono {calc.format_money(p.amount)} zł",
          company_id=inv.company_id, entity="invoice", entity_id=inv.id)
    db.commit()
    flash(request, "Oznaczono jako zapłaconą.")
    return redirect(_back(request, f"/faktury/{inv.id}"))


def _back(request: Request, default: str) -> str:
    """Powrot na liste naleznosci, gdy akcja byla wywolana stamtad (tylko wlasne sciezki)."""
    ref = request.headers.get("referer") or ""
    base = str(request.base_url)
    if ref.startswith(base + "naleznosci"):
        return "/" + ref[len(base):]
    return default


@app.post("/faktury/{invoice_id}/wplata")
async def invoice_add_payment(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    form = await request.form()
    try:
        amount = calc.parse_decimal(form.get("amount"), None)
        try:
            date = dt.date.fromisoformat(form.get("date") or "")
        except ValueError:
            date = dt.date.today()
        p = payments.add_payment(db, inv, amount, date, form.get("method") or "transfer", (form.get("note") or "").strip())
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
        return redirect(_back(request, f"/faktury/{inv.id}"))
    audit(db, web_actor(request), "invoice.paid",
          f"{inv.number}: wpłata {calc.format_money(p.amount)} zł ({p.date:%d.%m.%Y}, {p.method_label})",
          company_id=inv.company_id, entity="invoice", entity_id=inv.id)
    db.commit()
    flash(request, f"Zapisano wpłatę {calc.format_money(p.amount)} zł. Pozostało {calc.format_money(max(inv.to_pay, 0))} zł.")
    return redirect(_back(request, f"/faktury/{inv.id}"))


@app.post("/faktury/{invoice_id}/wplata/{payment_id}/usun")
def invoice_delete_payment(invoice_id: int, payment_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    try:
        p = payments.delete_payment(db, inv, payment_id)
    except ValueError as e:
        flash(request, str(e))
        return redirect(f"/faktury/{inv.id}")
    audit(db, web_actor(request), "invoice.unpaid", f"{inv.number}: usunięto wpłatę {calc.format_money(p.amount)} zł",
          company_id=inv.company_id, entity="invoice", entity_id=inv.id)
    db.commit()
    flash(request, "Usunięto wpłatę.")
    return redirect(f"/faktury/{inv.id}")


@app.get("/naleznosci", response_class=HTMLResponse)
def receivables_view(request: Request, filtr: str = "", q: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    filtr = filtr if filtr in payments.FILTERS else ""
    rows = payments.receivables(db, company, filtr, q)
    everything = rows if not filtr and not q else payments.receivables(db, company)
    return render(request, db, "receivables.html", rows=rows, filtr=filtr, q=q, FILTERS=payments.FILTERS,
                  total=payments.summary(everything), shown=payments.summary(rows), today=dt.date.today(),
                  has_mail=_company_accounts(db, company.id) != [])


@app.post("/faktury/{invoice_id}/usun")
def invoice_delete(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    try:
        services.delete_draft(db, web_actor(request), inv)
    except ServiceError as e:
        flash(request, str(e))
        return redirect(f"/faktury/{inv.id}")
    flash(request, "Usunięto szkic.")
    return redirect("/faktury")


def _derive(request: Request, db: Session, invoice_id: int, fn, done: str):
    get_invoice(db, request, invoice_id)
    try:
        inv = fn(db, web_actor(request), invoice_id)
    except ServiceError as e:
        flash(request, str(e))
        return redirect(f"/faktury/{invoice_id}")
    flash(request, done)
    return redirect(f"/faktury/{inv.id}/edytuj")


@app.post("/faktury/{invoice_id}/korekta")
def invoice_correction(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    return _derive(request, db, invoice_id, services.start_correction,
                   "Szkic korekty: zmień pozycje „po korekcie” i wpisz przyczynę.")


@app.post("/faktury/{invoice_id}/z-proformy")
def invoice_from_proforma(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    return _derive(request, db, invoice_id, services.start_from_proforma, "Szkic faktury VAT z proformy.")


@app.post("/faktury/{invoice_id}/koncowa")
def invoice_final(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    return _derive(request, db, invoice_id, services.start_final,
                   "Szkic faktury końcowej: całe zamówienie minus otrzymane zaliczki.")


@app.post("/faktury/{invoice_id}/kopiuj")
def invoice_copy(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    src = get_invoice(db, request, invoice_id)
    if src.kind not in NEW_KINDS:
        flash(request, "Tę fakturę można skorygować, ale nie skopiować.")
        return redirect(f"/faktury/{src.id}")
    today = dt.date.today()
    inv = Invoice(
        company_id=src.company_id, kind=src.kind, advance_gross=src.advance_gross, correction_reason="", issue_date=today, sale_date=today,
        issue_place=src.issue_place, due_date=today + dt.timedelta(days=src.company.default_payment_days),
        payment_method=src.payment_method, prices_gross=src.prices_gross, contractor_id=src.contractor_id,
        buyer_name=src.buyer_name, buyer_nip=src.buyer_nip, buyer_address=src.buyer_address, notes=src.notes,
        paid=Decimal("0"),
    )
    inv.items = [
        InvoiceItem(name=i.name, quantity=i.quantity, unit=i.unit, unit_price=i.unit_price, vat_rate=i.vat_rate,
                    product_id=i.product_id)
        for i in src.after_items
    ]
    inv.company = src.company
    calc.recalculate(inv)
    db.add(inv)
    db.commit()
    flash(request, "Utworzono szkic na podstawie faktury.")
    return redirect(f"/faktury/{inv.id}/edytuj")


@app.get("/faktury/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, request: Request, db: Session = Depends(get_db), pobierz: int = 0):
    inv = get_invoice(db, request, invoice_id)
    data = pdf.render_invoice(inv)
    filename = (inv.number or f"szkic-{inv.id}").replace("/", "-") + ".pdf"
    disposition = "attachment" if pobierz else "inline"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})


# --- KSeF -------------------------------------------------------------------


@app.post("/faktury/{invoice_id}/ksef")
def invoice_ksef_send(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    try:
        inv = ksef.send(db, web_actor(request), inv.id)
        if inv.ksef_status == "accepted":
            flash(request, f"KSeF przyjął fakturę {inv.number}. Numer KSeF: {inv.ksef_number}")
        elif inv.ksef_status == "sent":
            flash(request, "KSeF jeszcze przetwarza fakturę. Sprawdź status za chwilę.")
        else:
            flash(request, f"KSeF odrzucił fakturę: {inv.ksef_message}")
    except ServiceError as e:
        flash(request, str(e))
    return redirect(f"/faktury/{invoice_id}")


@app.post("/faktury/{invoice_id}/ksef/status")
def invoice_ksef_refresh(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    try:
        inv = ksef.refresh(db, web_actor(request), inv.id)
        flash(request, f"Status KSeF: {ksef.status_label(inv)} {inv.ksef_number}".strip())
    except ServiceError as e:
        flash(request, str(e))
    return redirect(f"/faktury/{invoice_id}")


@app.get("/faktury/{invoice_id}/ksef/xml")
def invoice_ksef_xml(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """Wyslany plik XML, a przed wysylka podglad (bez zapisu)."""
    inv = get_invoice(db, request, invoice_id)
    if inv.ksef_xml:
        data = inv.ksef_xml.encode("utf-8")
    else:
        try:
            data = ksef.preview_xml(inv)
        except ServiceError as e:
            return Response(str(e), status_code=422, media_type="text/plain; charset=utf-8")
    name = (inv.number or f"szkic-{inv.id}").replace("/", "-") + ".xml"
    return Response(data, media_type="application/xml", headers={"Content-Disposition": f'inline; filename="{name}"'})


@app.get("/faktury/{invoice_id}/ksef/upo")
def invoice_ksef_upo(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    if not inv.ksef_upo:
        raise HTTPException(404)
    name = "UPO-" + (inv.number or str(inv.id)).replace("/", "-") + ".xml"
    return Response(inv.ksef_upo, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# --- kontrahenci ----------------------------------------------------------


@app.get("/kontrahenci", response_class=HTMLResponse)
def contractor_list(request: Request, q: str = "", db: Session = Depends(get_db)):
    stmt = select(Contractor)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Contractor.name.ilike(like), Contractor.nip.ilike(like), Contractor.city.ilike(like)))
    return render(request, db, "contractors.html", contractors=db.scalars(stmt.order_by(Contractor.name)).all(), q=q)


CONTRACTOR_FIELDS = ["name", "nip", "pesel", "street", "postal_code", "city", "country", "email", "phone", "notes"]


@app.get("/kontrahenci/nowy", response_class=HTMLResponse)
def contractor_new(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "contractor_form.html", c=Contractor(is_company=True, country="PL"))


@app.get("/kontrahenci/{cid}", response_class=HTMLResponse)
def contractor_edit(cid: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(Contractor, cid)
    if c is None:
        raise HTTPException(404)
    invoices = db.scalars(select(Invoice).where(Invoice.contractor_id == cid).order_by(Invoice.issue_date.desc())).all()
    mails = db.scalars(select(MailMessage).where(MailMessage.contractor_id == cid)
                       .order_by(MailMessage.date.desc()).limit(30)).all()
    return render(request, db, "contractor_form.html", c=c, invoices=invoices, mails=mails)


async def _save_contractor(request: Request, db: Session, c: Contractor):
    form = await request.form()
    for f in CONTRACTOR_FIELDS:
        setattr(c, f, (form.get(f) or "").strip())
    c.nip = c.nip.replace("-", "").replace(" ", "")
    c.country = (c.country or "PL").upper()[:2]
    c.is_company = form.get("is_company") == "1"
    if not c.name:
        return render(request, db, "contractor_form.html", c=c, error="Podaj nazwę albo imię i nazwisko.")
    db.add(c)
    db.commit()
    flash(request, "Zapisano kontrahenta.")
    return redirect("/kontrahenci")


@app.post("/kontrahenci/nowy")
async def contractor_create(request: Request, db: Session = Depends(get_db)):
    return await _save_contractor(request, db, Contractor())


@app.post("/kontrahenci/{cid}")
async def contractor_update(cid: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(Contractor, cid)
    if c is None:
        raise HTTPException(404)
    return await _save_contractor(request, db, c)


@app.post("/kontrahenci/{cid}/usun")
def contractor_delete(cid: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(Contractor, cid)
    if c is None:
        raise HTTPException(404)
    invoices = db.scalars(select(Invoice).where(Invoice.contractor_id == cid)).all()
    if any(i.status != "draft" for i in invoices):
        flash(request, "Kontrahent ma wystawione faktury, nie można go usunąć.")
        return redirect(f"/kontrahenci/{cid}")
    for inv in invoices:  # szkice zachowuja dane nabywcy, tracą tylko powiazanie
        inv.contractor_id = None
    audit(db, web_actor(request), "contractor.delete", f"Usunięto kontrahenta {c.name}", entity="contractor")
    db.delete(c)
    db.commit()
    flash(request, f"Usunięto kontrahenta {c.name}.")
    return redirect("/kontrahenci")


# --- auta ---------------------------------------------------------------


@app.get("/auta", response_class=HTMLResponse)
def car_list(request: Request, status: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    stmt = select(Car).where(Car.company_id == company.id)
    if status in CAR_STATUSES:
        stmt = stmt.where(Car.status == status)
    return render(request, db, "cars.html", cars=db.scalars(stmt.order_by(Car.id.desc())).all(), status=status)


@app.get("/auta/nowe", response_class=HTMLResponse)
def car_new(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    return render(request, db, "car_form.html", car=Car(company_id=company.id, status="available"))


@app.get("/auta/{car_id}", response_class=HTMLResponse)
def car_edit(car_id: int, request: Request, db: Session = Depends(get_db)):
    car = db.get(Car, car_id)
    if car is None or car.company_id != current_company(request, db).id:
        raise HTTPException(404)
    invoices = db.scalars(select(Invoice).where(Invoice.car_id == car_id)).all()
    return render(request, db, "car_form.html", car=car, invoices=invoices)


async def _save_car(request: Request, db: Session, car: Car):
    form = await request.form()

    def opt_int(name):
        v = (form.get(name) or "").replace(" ", "")
        return int(v) if v.isdigit() else None

    try:
        car.make = (form.get("make") or "").strip()
        car.model = (form.get("model") or "").strip()
        car.vin = (form.get("vin") or "").strip().upper()
        car.year = opt_int("year")
        car.mileage_km = opt_int("mileage_km")
        car.registration = (form.get("registration") or "").strip().upper()
        pd = form.get("purchase_date") or ""
        car.purchase_date = dt.date.fromisoformat(pd) if pd else None
        car.purchase_price = calc.parse_decimal(form.get("purchase_price"), Decimal("0"))
        car.purchase_document = (form.get("purchase_document") or "").strip()
        car.asking_price = calc.parse_decimal(form.get("asking_price"), None)
        st = form.get("status") or "available"
        car.status = st if st in CAR_STATUSES else "available"
        car.notes = (form.get("notes") or "").strip()
        if not car.make:
            raise ValueError("Podaj markę.")
        if car.vin and len(car.vin) != 17:
            raise ValueError("VIN powinien mieć 17 znaków.")
    except ValueError as e:
        return render(request, db, "car_form.html", car=car, error=str(e))
    db.add(car)
    db.commit()
    flash(request, f"Zapisano {car.label}.")
    return redirect("/auta")


@app.post("/auta/nowe")
async def car_create(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    return await _save_car(request, db, Car(company_id=company.id))


@app.post("/auta/{car_id}")
async def car_update(car_id: int, request: Request, db: Session = Depends(get_db)):
    car = db.get(Car, car_id)
    if car is None or car.company_id != current_company(request, db).id:
        raise HTTPException(404)
    return await _save_car(request, db, car)


@app.post("/auta/{car_id}/usun")
def car_delete(car_id: int, request: Request, db: Session = Depends(get_db)):
    car = db.get(Car, car_id)
    if car is None or car.company_id != current_company(request, db).id:
        raise HTTPException(404)
    invoices = db.scalars(select(Invoice).where(Invoice.car_id == car_id)).all()
    if any(i.status != "draft" for i in invoices):
        flash(request, "Auto ma wystawioną fakturę, nie można go usunąć.")
        return redirect(f"/auta/{car_id}")
    for inv in invoices:
        inv.car_id = None
    label = car.label
    audit(db, web_actor(request), "car.delete", f"Usunięto auto {label}", company_id=car.company_id, entity="car")
    db.delete(car)
    db.commit()
    flash(request, f"Usunięto auto {label}.")
    return redirect("/auta")


# --- produkty -------------------------------------------------------------


@app.get("/produkty", response_class=HTMLResponse)
def product_list(request: Request, q: str = "", pokaz: str = "", db: Session = Depends(get_db)):
    rows = prod.search(db, q, active_only=pokaz != "wszystkie")
    return render(request, db, "products.html", products=rows, q=q, pokaz=pokaz)


@app.get("/produkty/nowy", response_class=HTMLResponse)
def product_new(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "product_form.html", product=Product(name="", code="", unit="szt.", vat_rate="23",
                                                              ean="", description="", active=True))


def _get_product(db: Session, pid: int) -> Product:
    p = db.get(Product, pid)
    if p is None:
        raise HTTPException(404)
    return p


@app.get("/produkty/{pid}", response_class=HTMLResponse)
def product_edit(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_product(db, pid)
    used = db.scalar(select(func.count()).select_from(InvoiceItem).where(InvoiceItem.product_id == pid))
    moves = db.scalars(select(StockMovement).where(StockMovement.product_id == pid)
                       .order_by(StockMovement.date.desc(), StockMovement.id.desc()).limit(50)).all()
    return render(request, db, "product_form.html", product=p, used=used, moves=moves)


async def _save_product(request: Request, db: Session, p: Product):
    form = await request.form()
    is_new = p.id is None
    try:
        p.code = (form.get("code") or "").strip()
        p.name = (form.get("name") or "").strip()
        p.unit = (form.get("unit") or "").strip() or "szt."
        p.vat_rate = form.get("vat_rate") or "23"
        p.ean = (form.get("ean") or "").strip()
        p.description = (form.get("description") or "").strip()
        p.active = form.get("active") == "1"
        p.is_service = form.get("is_service") == "1"
        net = calc.parse_decimal(form.get("price_net"), None)
        gross = calc.parse_decimal(form.get("price_gross"), None)
        if form.get("price_mode") == "gross" and gross is not None:
            pct = Decimal(p.vat_rate) if p.vat_rate.isdigit() else Decimal("0")
            net = (gross * 100 / (100 + pct)).quantize(Decimal("0.01"))
        p.price_net = net
        prod.validate(p)
        stock = calc.parse_decimal(form.get("stock"), None)
        if stock is not None and not p.is_service:
            if is_new:
                p.stock = Decimal("0")
                db.add(p)
                db.flush()
            wz.adjust_stock(db, p, stock, (form.get("stock_note") or "").strip())
    except ValueError as e:
        db.rollback()
        return render(request, db, "product_form.html", product=p, error=str(e))
    files = [(f.filename or "", await f.read()) for f in form.getlist("photos") if getattr(f, "filename", None)]
    errors = prod.add_photos(p, files)
    db.add(p)
    db.flush()
    audit(db, web_actor(request), "product.create" if is_new else "product.update",
          f"{'Dodano' if is_new else 'Zmieniono'} produkt {p.label}", entity="product", entity_id=p.id)
    db.commit()
    flash(request, f"Zapisano {p.label}." + (" " + " ".join(errors) if errors else ""))
    return redirect(f"/produkty/{p.id}")


@app.post("/produkty/nowy")
async def product_create(request: Request, db: Session = Depends(get_db)):
    return await _save_product(request, db, Product())


@app.post("/produkty/{pid}")
async def product_update(pid: int, request: Request, db: Session = Depends(get_db)):
    return await _save_product(request, db, _get_product(db, pid))


@app.post("/produkty/{pid}/usun")
def product_delete(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_product(db, pid)
    label = p.label
    # Pozycje faktur zachowuja nazwe i cene; traca tylko powiazanie z katalogiem.
    db.execute(update(InvoiceItem).where(InvoiceItem.product_id == pid).values(product_id=None))
    db.execute(update(DeliveryItem).where(DeliveryItem.product_id == pid).values(product_id=None))
    audit(db, web_actor(request), "product.delete", f"Usunięto produkt {label}", entity="product")
    db.delete(p)
    db.commit()
    flash(request, f"Usunięto {label}.")
    return redirect("/produkty")


@app.post("/produkty/{pid}/zdjecie/{photo_id}/usun")
def product_photo_delete(pid: int, photo_id: int, request: Request, db: Session = Depends(get_db)):
    p = _get_product(db, pid)
    photo = next((ph for ph in p.photos if ph.id == photo_id), None)
    if photo is None:
        raise HTTPException(404)
    p.photos.remove(photo)
    audit(db, web_actor(request), "product.photo_delete", f"Usunięto zdjęcie produktu {p.label}",
          entity="product", entity_id=p.id)
    db.commit()
    return redirect(f"/produkty/{pid}")


@app.post("/produkty/{pid}/zdjecie/{photo_id}/glowne")
def product_photo_main(pid: int, photo_id: int, db: Session = Depends(get_db)):
    p = _get_product(db, pid)
    prod.set_main_photo(p, photo_id)
    db.commit()
    return redirect(f"/produkty/{pid}")


@app.get("/zdjecia/{photo_id}")
def product_photo(photo_id: int, mini: int = 0, db: Session = Depends(get_db)):
    photo = db.get(ProductPhoto, photo_id)
    if photo is None:
        raise HTTPException(404)
    return Response(photo.thumb if mini else photo.data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


# --- ustawienia firmy -----------------------------------------------------

COMPANY_FIELDS = ["short_name", "name", "nip", "street", "postal_code", "city", "bank_name", "bank_account",
                  "email", "phone", "issue_place", "pattern_vat", "pattern_proforma", "pattern_advance", "pattern_wz",
                  "pattern_pz", "pattern_correction"]


@app.get("/ustawienia", response_class=HTMLResponse)
def settings_form(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "settings.html", KSEF_ENVS=KSEF_ENVS)


@app.post("/ustawienia/ksef")
async def settings_ksef(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    async def _file(name):
        f = form.get(name)
        if getattr(f, "filename", None):
            data = await f.read()
            return data[:200_000] or None
        return None
    try:
        ksef.set_token(db, web_actor(request), company, form.get("ksef_env") or "off",
                       (form.get("ksef_token") or "").strip() or None, auth=form.get("ksef_auth") or None,
                       cert=await _file("ksef_cert"), key=await _file("ksef_key"),
                       key_password=form.get("ksef_key_password") or "")
    except ServiceError as e:
        db.rollback()
        return render(request, db, "settings.html", KSEF_ENVS=KSEF_ENVS, error=str(e))
    flash(request, "Zapisano ustawienia KSeF.")
    return redirect("/ustawienia")


@app.post("/ustawienia")
async def settings_save(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    for f in COMPANY_FIELDS:
        if f in form:
            setattr(company, f, (form.get(f) or "").strip())
    company.nip = company.nip.replace("-", "").replace(" ", "")
    company.numbering_reset = "year" if form.get("numbering_reset") == "year" else "month"
    days = form.get("default_payment_days") or "7"
    company.default_payment_days = int(days) if days.isdigit() else 7
    for p in (company.pattern_vat, company.pattern_correction, company.pattern_advance, company.pattern_proforma, company.pattern_wz,
              company.pattern_pz):
        if "{nr}" not in p and "{NNN}" not in p:
            db.rollback()
            return render(request, db, "settings.html", KSEF_ENVS=KSEF_ENVS, error="Wzór numeru musi zawierać {nr} albo {NNN}.")
    db.commit()
    flash(request, "Zapisano ustawienia firmy.")
    return redirect("/ustawienia")


# --- dziennik i dostep dla Claude -------------------------------------------


@app.get("/dziennik", response_class=HTMLResponse)
def audit_log(request: Request, db: Session = Depends(get_db)):
    entries = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(200)).all()
    return render(request, db, "audit.html", entries=entries)


def _grants(db: Session) -> list[dict]:
    """Aktywne polaczenia: zgody OAuth (po tokenie odswiezania) i klucze API."""
    rows = db.scalars(
        select(ApiToken).where(ApiToken.revoked.is_(False), ApiToken.kind.in_(["refresh", "key"]))
        .order_by(ApiToken.id.desc())
    ).all()
    out = []
    for t in rows:
        if t.kind == "refresh" and t.expires_at and t.expires_at < auth.now():
            continue
        last = db.scalar(select(ApiToken.last_used_at).where(ApiToken.grant_id == t.grant_id)
                         .order_by(ApiToken.last_used_at.desc().nulls_last()).limit(1))
        out.append(dict(grant_id=t.grant_id, name=t.name, kind=t.kind, scopes=[SCOPES.get(s, s) for s in t.scope_list],
                        created_at=t.created_at, last_used_at=last))
    return out


@app.get("/dostep", response_class=HTMLResponse)
def access_page(request: Request, db: Session = Depends(get_db)):
    new_key = request.session.pop("new_key", None)
    return render(request, db, "access.html", grants=_grants(db), scopes=SCOPES, new_key=new_key,
                  mcp_url=auth.base_url(request) + "/mcp")


@app.post("/dostep/klucz")
async def access_new_key(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    scopes = [s for s in form.getlist("scope") if s in SCOPES] or ["read"]
    name = (form.get("name") or "").strip() or "Klucz API"
    request.session["new_key"] = auth.create_api_key(db, name, scopes)
    audit(db, web_actor(request), "access.key", f"Nowy klucz API „{name}” ({', '.join(scopes)})")
    db.commit()
    return redirect("/dostep")


@app.post("/dostep/{grant_id}/cofnij")
def access_revoke(grant_id: str, request: Request, db: Session = Depends(get_db)):
    auth.revoke_grant(db, grant_id)
    audit(db, web_actor(request), "access.revoke", f"Cofnięto dostęp {grant_id}")
    db.commit()
    flash(request, "Dostęp cofnięty.")
    return redirect("/dostep")


# ---------------------------------------------------------------- bank

BANK_FILTERS = {
    "do-sprawdzenia": ("suggested", "none"),
    "wplywy": ("auto", "manual", "suggested", "none", "ignored"),
    "zaksiegowane": ("auto", "manual"),
    "wszystkie": None,
}


@app.get("/bank", response_class=HTMLResponse)
def bank_list(request: Request, pokaz: str = "do-sprawdzenia", db: Session = Depends(get_db)):
    company = current_company(request, db)
    if pokaz not in BANK_FILTERS:
        pokaz = "do-sprawdzenia"
    stmt = select(BankTransaction).where(BankTransaction.company_id == company.id)
    states = BANK_FILTERS[pokaz]
    if states:
        stmt = stmt.where(BankTransaction.state.in_(states))
    rows = db.scalars(stmt.order_by(BankTransaction.booking_date.desc(), BankTransaction.id.desc()).limit(300)).all()
    counts = dict(db.execute(select(BankTransaction.state, func.count()).where(BankTransaction.company_id == company.id)
                             .group_by(BankTransaction.state)).all())
    return render(request, db, "bank.html", rows=rows, pokaz=pokaz, counts=counts, states=bank.STATES,
                  open_invoices=bank.open_invoices(db, company.id))


@app.post("/bank/import")
async def bank_import(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    files = [f for f in form.getlist("files") if getattr(f, "filename", None)]
    if not files:
        flash(request, "Wybierz plik MT940 z iPKO.")
        return redirect("/bank")
    msgs = []
    for f in files:
        data = await f.read()
        if len(data) > 10 * 1024 * 1024:
            msgs.append(f"{f.filename}: plik za duży.")
            continue
        try:
            res = bank.import_file(db, web_actor(request), data, f.filename or "plik", company)
            db.commit()
            msgs.append(res["summary"])
        except MT940Error as e:
            db.rollback()
            msgs.append(f"{f.filename}: {e}")
    flash(request, " ".join(msgs))
    return redirect("/bank")


def _bank_action(request: Request, db: Session, fn, *args) -> RedirectResponse:
    company = current_company(request, db)
    back = request.headers.get("referer") or ""
    base = str(request.base_url)
    back = "/" + back[len(base):] if back.startswith(base) else "/bank"
    try:
        fn(db, web_actor(request), company.id, *args)
        db.commit()
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
    return redirect(back)


@app.post("/bank/{tx_id}/potwierdz")
async def bank_confirm(tx_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    company = current_company(request, db)
    invoice_id = None
    chosen = (form.get("invoice") or "").strip()
    if chosen:
        inv = bank.find_invoice_by_number(db, company.id, chosen)
        if inv is None:
            flash(request, f"Nie znalazłem wystawionej faktury {chosen}.")
            return redirect("/bank")
        invoice_id = inv.id
    return _bank_action(request, db, bank.confirm, tx_id, invoice_id)


@app.post("/bank/{tx_id}/cofnij")
def bank_undo(tx_id: int, request: Request, db: Session = Depends(get_db)):
    return _bank_action(request, db, bank.undo, tx_id)


@app.post("/bank/{tx_id}/pomin")
def bank_ignore(tx_id: int, request: Request, db: Session = Depends(get_db)):
    return _bank_action(request, db, bank.ignore, tx_id)


# ---------------------------------------------------------------- poczta

def _company_accounts(db: Session, company_id: int) -> list[MailAccount]:
    return list(db.scalars(select(MailAccount).where(MailAccount.company_id == company_id).order_by(MailAccount.id)))


def _get_message(db: Session, request: Request, mid: int) -> MailMessage:
    m = db.get(MailMessage, mid)
    if m is None or m.account.company_id != current_company(request, db).id:
        raise HTTPException(404)
    return m


@app.get("/poczta", response_class=HTMLResponse)
def mail_list(request: Request, konto: int = 0, q: str = "", kierunek: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    accounts = _company_accounts(db, company.id)
    ids = [a.id for a in accounts if not konto or a.id == konto]
    rows = []
    if ids:
        stmt = select(MailMessage).where(MailMessage.account_id.in_(ids))
        if kierunek in ("in", "out"):
            stmt = stmt.where(MailMessage.direction == kierunek)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(MailMessage.subject.ilike(like), MailMessage.from_addr.ilike(like),
                                  MailMessage.from_name.ilike(like), MailMessage.to_addrs.ilike(like)))
        rows = db.scalars(stmt.order_by(MailMessage.date.desc(), MailMessage.id.desc()).limit(150)).all()
    return render(request, db, "mail_list.html", accounts=accounts, rows=rows, konto=konto, q=q, kierunek=kierunek)


@app.post("/poczta/odswiez")
def mail_refresh(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    n, errors = 0, []
    for acc in _company_accounts(db, company.id):
        if acc.active:
            n += mail.sync_account(db, acc)
            db.commit()
            if acc.last_error:
                errors.append(f"{acc.email}: {acc.last_error}")
    flash(request, f"Nowych wiadomości: {n}." + (" Błędy: " + "; ".join(errors) if errors else ""))
    return redirect("/poczta")


@app.get("/poczta/nowa", response_class=HTMLResponse)
def mail_compose(request: Request, faktura: int = 0, odpowiedz: int = 0, kontrahent: int = 0, do: str = "",
                 przypomnienie: int = 0, db: Session = Depends(get_db)):
    company = current_company(request, db)
    accounts = [a for a in _company_accounts(db, company.id) if a.active]
    draft = {"to": do, "cc": "", "subject": "", "body": "", "in_reply_to": "", "invoice_id": 0, "account_id": 0,
             "reminder": ""}
    inv = None
    if faktura:
        inv = get_invoice(db, request, faktura)
        draft["invoice_id"] = inv.id
        draft["to"] = draft["to"] or (inv.contractor.email.split(",")[0].strip() if inv.contractor and inv.contractor.email else "")
        draft["subject"] = f"{inv.kind_label} {inv.display_number} – {company.name}"
        lines = ["Dzień dobry,", "", f"w załączniku przesyłam {inv.kind_label.lower()} {inv.display_number}"
                 f" na kwotę {calc.format_money(inv.total_gross)} zł brutto."]
        if inv.due_date and inv.to_pay > 0 and inv.kind != "correction":
            lines.append(f"Termin płatności: {inv.due_date:%d.%m.%Y}"
                         + (f", konto: {company.bank_account}" if company.bank_account else "") + ".")
        lines += ["", "Pozdrawiam"]
        draft["body"] = "\n".join(lines)
        if przypomnienie and inv.status == "issued" and inv.to_pay > 0:
            draft["subject"], draft["body"] = payments.reminder_draft(inv)
            draft["reminder"] = "1"
    if kontrahent and not draft["to"]:
        c = db.get(Contractor, kontrahent)
        if c and c.email:
            draft["to"] = c.email.split(",")[0].strip()
    if odpowiedz:
        orig = _get_message(db, request, odpowiedz)
        draft["account_id"] = orig.account_id
        draft["to"] = orig.from_addr if orig.direction == "in" else orig.to_addrs
        subj = orig.subject or ""
        draft["subject"] = subj if subj.lower().startswith(("re:", "odp:")) else f"Re: {subj}"
        draft["in_reply_to"] = orig.message_id
        when = orig.date.strftime("%d.%m.%Y %H:%M") if orig.date else ""
        quoted = "\n".join("> " + line for line in (orig.body_text or "").splitlines()[:200])
        draft["body"] = f"\n\n{when}, {orig.from_name or orig.from_addr} napisał(a):\n{quoted}"
        if orig.invoice_id:
            draft["invoice_id"] = orig.invoice_id
    return render(request, db, "mail_compose.html", accounts=accounts, draft=draft, inv=inv)


@app.post("/poczta/nowa")
async def mail_send(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    accounts = {a.id: a for a in _company_accounts(db, company.id) if a.active}
    draft = {k: (form.get(k) or "") for k in ("to", "cc", "subject", "body", "in_reply_to", "reminder")}
    draft["account_id"] = int(form.get("account_id") or 0)
    draft["invoice_id"] = int(form.get("invoice_id") or 0)
    acc = accounts.get(draft["account_id"])
    inv = get_invoice(db, request, draft["invoice_id"]) if draft["invoice_id"] else None
    files = []
    if inv is not None and form.get("attach_invoice") == "1":
        name = (inv.number or f"szkic-{inv.id}").replace("/", "-") + ".pdf"
        files.append((name, "application/pdf", pdf.render_invoice(inv)))
    for f in form.getlist("files"):
        if getattr(f, "filename", None):
            data = await f.read()
            if data:
                files.append((f.filename, f.content_type or "application/octet-stream", data))
    if sum(len(d) for _, _, d in files) > 20 * 1024 * 1024:
        return render(request, db, "mail_compose.html", accounts=list(accounts.values()), draft=draft, inv=inv,
                      error="Załączniki są za duże (razem ponad 20 MB).")
    try:
        if acc is None:
            raise MailError("Wybierz skrzynkę, z której wysłać.")
        msg = mail.send(db, acc, draft["to"], draft["subject"], draft["body"], cc=draft["cc"], attachments=files,
                        in_reply_to=draft["in_reply_to"], invoice_id=inv.id if inv else None)
        if inv is not None and draft["reminder"] == "1":
            payments.record_reminder(inv)
        audit(db, web_actor(request), "mail.send", f"E-mail z {acc.email} do {draft['to']}: {draft['subject'][:120]}",
              company_id=company.id, entity="invoice" if inv else "mail", entity_id=inv.id if inv else (msg.id if msg else None))
        db.commit()
    except MailError as e:
        db.rollback()
        return render(request, db, "mail_compose.html", accounts=list(accounts.values()), draft=draft, inv=inv, error=str(e))
    flash(request, f"Wysłano {'przypomnienie ' if inv is not None and draft['reminder'] == '1' else ''}do {draft['to']}.")
    return redirect(f"/faktury/{inv.id}" if inv else "/poczta?kierunek=out")


@app.get("/poczta/{mid}", response_class=HTMLResponse)
def mail_view(mid: int, request: Request, db: Session = Depends(get_db)):
    m = _get_message(db, request, mid)
    if not m.seen:
        m.seen = True
        db.commit()
    return render(request, db, "mail_view.html", m=m)


@app.get("/poczta/{mid}/html")
def mail_html(mid: int, request: Request, db: Session = Depends(get_db)):
    m = _get_message(db, request, mid)
    # Ramka sandbox bez skryptow; CSP blokuje skrypty, formularze i obrazki z internetu (sledzace piksele).
    return HTMLResponse(m.body_html or "", headers={
        "Content-Security-Policy": "default-src 'none'; img-src data: cid:; style-src 'unsafe-inline'; "
                                   "form-action 'none'; frame-ancestors 'self'; sandbox allow-popups",
        "X-Frame-Options": "SAMEORIGIN",
    })


@app.get("/poczta/zalacznik/{aid}")
def mail_attachment(aid: int, request: Request, db: Session = Depends(get_db)):
    a = db.get(MailAttachment, aid)
    if a is None or a.data is None:
        raise HTTPException(404)
    _get_message(db, request, a.message_id)
    safe = re.sub(r'[^\w.\- ]', "_", a.filename) or "zalacznik"
    return Response(a.data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"',
                             "Content-Security-Policy": "sandbox"})


@app.post("/poczta/{mid}/przypisz")
async def mail_assign(mid: int, request: Request, db: Session = Depends(get_db)):
    m = _get_message(db, request, mid)
    form = await request.form()
    number = (form.get("invoice") or "").strip()
    if number:
        inv = bank.find_invoice_by_number(db, m.account.company_id, number)
        if inv is None:
            flash(request, f"Nie znalazłem faktury {number}.")
            return redirect(f"/poczta/{mid}")
        m.invoice_id = inv.id
    else:
        m.invoice_id = None
    db.commit()
    return redirect(f"/poczta/{mid}")


# --- ustawienia poczty

MAIL_FIELDS = ("email", "display_name", "imap_host", "smtp_host", "username", "signature")


@app.get("/ustawienia/poczta", response_class=HTMLResponse)
def mail_settings(request: Request, edytuj: int = 0, db: Session = Depends(get_db)):
    company = current_company(request, db)
    accounts = _company_accounts(db, company.id)
    acc = next((a for a in accounts if a.id == edytuj), None) or MailAccount(
        email="", display_name=company.name, imap_host="", imap_port=993, smtp_host="", smtp_port=465,
        smtp_security="ssl", username="", signature="", active=True)
    return render(request, db, "mail_settings.html", accounts=accounts, acc=acc)


@app.post("/ustawienia/poczta")
async def mail_settings_save(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    aid = int(form.get("id") or 0)
    acc = db.get(MailAccount, aid) if aid else MailAccount(company_id=company.id, sync_state="{}", last_error="",
                                                            password_enc="")
    if acc is None or acc.company_id != company.id:
        raise HTTPException(404)
    for f in MAIL_FIELDS:
        setattr(acc, f, (form.get(f) or "").strip())
    acc.email = acc.email.lower()
    try:
        acc.imap_port = int(form.get("imap_port") or 993)
        acc.smtp_port = int(form.get("smtp_port") or 465)
    except ValueError:
        acc.imap_port, acc.smtp_port = 993, 465
    acc.smtp_security = "starttls" if form.get("smtp_security") == "starttls" else "ssl"
    acc.active = form.get("active") == "1"
    pw = form.get("password") or ""
    if pw:
        acc.password_enc = secretbox.seal(pw)
    if "@" not in acc.email or not acc.imap_host or not acc.smtp_host:
        return render(request, db, "mail_settings.html", accounts=_company_accounts(db, company.id), acc=acc,
                      error="Podaj adres e-mail, serwer IMAP i serwer SMTP.")
    db.add(acc)
    audit(db, web_actor(request), "mail.account", f"Skrzynka {acc.email} zapisana", company_id=company.id)
    db.commit()
    flash(request, f"Zapisano skrzynkę {acc.email}. Sprawdź połączenie przyciskiem „Test”.")
    return redirect("/ustawienia/poczta")


@app.post("/ustawienia/poczta/{aid}/test")
def mail_settings_test(aid: int, request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    acc = db.get(MailAccount, aid)
    if acc is None or acc.company_id != company.id:
        raise HTTPException(404)
    msgs = []
    try:
        m = mail.connect_imap(acc)
        folder = mail.sent_folder(m)
        m.logout()
        msgs.append(f"IMAP OK (wysłane: {folder or 'nie znaleziono folderu'})")
    except (MailError, Exception) as e:  # noqa: BLE001
        msgs.append(f"IMAP: {e}")
    try:
        s = mail.connect_smtp(acc)
        s.quit()
        msgs.append("SMTP OK")
    except (MailError, Exception) as e:  # noqa: BLE001
        msgs.append(f"SMTP: {e}")
    flash(request, f"{acc.email}: " + "; ".join(msgs))
    return redirect("/ustawienia/poczta")


@app.post("/ustawienia/poczta/{aid}/usun")
def mail_settings_delete(aid: int, request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    acc = db.get(MailAccount, aid)
    if acc is None or acc.company_id != company.id:
        raise HTTPException(404)
    msg_ids = select(MailMessage.id).where(MailMessage.account_id == aid)
    db.execute(MailAttachment.__table__.delete().where(MailAttachment.message_id.in_(msg_ids)))
    db.execute(MailMessage.__table__.delete().where(MailMessage.account_id == aid))
    db.delete(acc)
    audit(db, web_actor(request), "mail.account", f"Usunięto skrzynkę {acc.email} z Sygnavo (poczta na serwerze zostaje)",
          company_id=company.id)
    db.commit()
    flash(request, "Usunięto skrzynkę z Sygnavo. Wiadomości na serwerze poczty nie zostały ruszone.")
    return redirect("/ustawienia/poczta")


# ---------------------------------------------------------------- WZ

def _get_wz(db: Session, request: Request, wid: int) -> Delivery:
    d = db.get(Delivery, wid)
    if d is None or d.company_id != current_company(request, db).id:
        raise HTTPException(404)
    return d


def _wz_form(request: Request, db: Session, d: Delivery, error: str | None = None):
    products = prod.search(db, "", active_only=True)
    contractors = db.scalars(select(Contractor).order_by(Contractor.name)).all()
    return render(request, db, "wz_form.html", d=d, products=products, products_json=script_json(prod.picker_json(products)),
                  contractors=contractors, error=error)


@app.get("/wz", response_class=HTMLResponse)
def wz_list(request: Request, q: str = "", rodzaj: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    stmt = select(Delivery).where(Delivery.company_id == company.id)
    if rodzaj in DELIVERY_KINDS:
        stmt = stmt.where(Delivery.kind == rodzaj)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Delivery.number.ilike(like), Delivery.recipient_name.ilike(like)))
    rows = db.scalars(stmt.order_by(Delivery.issue_date.desc(), Delivery.id.desc()).limit(200)).all()
    return render(request, db, "wz_list.html", rows=rows, q=q, rodzaj=rodzaj)


@app.get("/wz/nowa", response_class=HTMLResponse)
def wz_new(request: Request, faktura: int = 0, rodzaj: str = "wz", db: Session = Depends(get_db)):
    company = current_company(request, db)
    if faktura:
        d = wz.from_invoice(get_invoice(db, request, faktura))
    else:
        d = Delivery(company_id=company.id, kind=rodzaj if rodzaj in DELIVERY_KINDS else "wz", status="draft",
                     issue_date=dt.date.today(), recipient_name="", recipient_address="", notes="", signed_by="",
                     prices_gross=False)
    return _wz_form(request, db, d)


async def _wz_save(request: Request, db: Session, d: Delivery):
    form = await request.form()
    d.recipient_name = (form.get("recipient_name") or "").strip()[:300]
    d.recipient_address = (form.get("recipient_address") or "").strip()
    d.notes = (form.get("notes") or "").strip()
    if d.id is None and form.get("kind") in DELIVERY_KINDS:
        d.kind = form.get("kind")
    d.prices_gross = form.get("prices_gross") == "1"
    cid = form.get("contractor_id") or ""
    d.contractor_id = int(cid) if cid.isdigit() and db.get(Contractor, int(cid)) else None
    iid = form.get("invoice_id") or ""
    if iid.isdigit():
        inv = db.get(Invoice, int(iid))
        d.invoice_id = inv.id if inv and inv.company_id == d.company_id else None
    try:
        d.issue_date = dt.date.fromisoformat(form.get("issue_date") or "")
    except ValueError:
        d.issue_date = dt.date.today()
    try:
        wz.set_items(db, d, form.getlist("item_name"), form.getlist("item_qty"), form.getlist("item_unit"),
                     form.getlist("item_product"), form.getlist("item_price"), form.getlist("item_rate"))
        db.add(d)
        db.flush()
        if form.get("action") == "issue":
            wz.issue(db, d)
            audit(db, web_actor(request), "wz.issue", f"Wystawiono {d.number} dla {d.recipient_name}",
                  company_id=d.company_id, entity="wz", entity_id=d.id)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _wz_form(request, db, d, error=str(e))
    flash(request, f"Wystawiono {d.number}." if d.status == "issued" else f"Zapisano szkic {d.kind_short}.")
    return redirect(f"/wz/{d.id}")


@app.post("/wz/nowa")
async def wz_create(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    return await _wz_save(request, db, Delivery(company_id=company.id, kind="wz", status="draft", signed_by=""))


@app.get("/wz/{wid}", response_class=HTMLResponse)
def wz_view(wid: int, request: Request, db: Session = Depends(get_db)):
    d = _get_wz(db, request, wid)
    return render(request, db, "wz_view.html", d=d, signature=wz.signature_data_url(d))


@app.get("/wz/{wid}/edytuj", response_class=HTMLResponse)
def wz_edit(wid: int, request: Request, db: Session = Depends(get_db)):
    d = _get_wz(db, request, wid)
    if d.status != "draft":
        raise HTTPException(409, "Dokument jest już wystawiony.")
    return _wz_form(request, db, d)


@app.post("/wz/{wid}/edytuj")
async def wz_update(wid: int, request: Request, db: Session = Depends(get_db)):
    d = _get_wz(db, request, wid)
    if d.status != "draft":
        raise HTTPException(409, "Dokument jest już wystawiony.")
    return await _wz_save(request, db, d)


@app.post("/wz/{wid}/wystaw")
def wz_issue(wid: int, request: Request, db: Session = Depends(get_db)):
    d = _get_wz(db, request, wid)
    try:
        wz.issue(db, d)
        audit(db, web_actor(request), "wz.issue", f"Wystawiono {d.number} dla {d.recipient_name}",
              company_id=d.company_id, entity="wz", entity_id=d.id)
        db.commit()
        flash(request, f"Wystawiono {d.number}.")
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
    return redirect(f"/wz/{d.id}")


@app.post("/wz/{wid}/podpis")
async def wz_sign(wid: int, request: Request, db: Session = Depends(get_db)):
    d = _get_wz(db, request, wid)
    form = await request.form()
    try:
        wz.sign(d, form.get("signature") or "", form.get("signed_by") or "")
        audit(db, web_actor(request), "wz.sign", f"{d.number}: podpis odbiorcy {d.signed_by}",
              company_id=d.company_id, entity="wz", entity_id=d.id)
        db.commit()
        flash(request, "Zapisano podpis odbiorcy.")
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
    return redirect(f"/wz/{d.id}")


@app.post("/wz/{wid}/faktura")
def wz_to_invoice(wid: int, request: Request, db: Session = Depends(get_db)):
    d = _get_wz(db, request, wid)
    try:
        inv = wz.to_invoice(db, web_actor(request), d)
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
        return redirect(f"/wz/{d.id}")
    flash(request, f"Utworzono szkic faktury z {d.number}. Sprawdź i wystaw.")
    return redirect(f"/faktury/{inv.id}/edytuj")


@app.get("/wz/{wid}/pdf")
def wz_pdf(wid: int, request: Request, db: Session = Depends(get_db), pobierz: int = 0):
    d = _get_wz(db, request, wid)
    filename = (d.number or f"wz-szkic-{d.id}").replace("/", "-") + ".pdf"
    disposition = "attachment" if pobierz else "inline"
    return Response(wz.render_pdf(d), media_type="application/pdf",
                    headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})


@app.post("/wz/{wid}/usun")
def wz_delete(wid: int, request: Request, db: Session = Depends(get_db)):
    d = _get_wz(db, request, wid)
    if d.status != "draft":
        flash(request, "Wystawionego dokumentu nie można usunąć.")
        return redirect(f"/wz/{d.id}")
    db.delete(d)
    db.commit()
    flash(request, f"Usunięto szkic {d.kind_short}.")
    return redirect("/wz")


# ---------------------------------------------------------------- aplikacja na telefon (PWA)

@app.get("/sw.js")
def service_worker():
    # Minimalny service worker: pozwala zainstalowac Sygnavo jako aplikacje; niczego nie zapisuje offline
    # (dane firmy nie zostaja w pamieci telefonu).
    js = "self.addEventListener('install', e => self.skipWaiting());\n" \
         "self.addEventListener('activate', e => self.clients.claim());\n" \
         "self.addEventListener('fetch', () => {});\n"
    return Response(js, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- Koszty (faktury z KSeF)

def _get_purchase(db: Session, request: Request, pid: int) -> PurchaseInvoice:
    p = db.get(PurchaseInvoice, pid)
    if p is None or p.company_id != current_company(request, db).id:
        raise HTTPException(404)
    return p


@app.get("/koszty", response_class=HTMLResponse)
def purchases_list(request: Request, status: str = "", q: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    base = select(PurchaseInvoice).where(PurchaseInvoice.company_id == company.id)
    stmt = base
    if status in PURCHASE_STATUSES:
        stmt = stmt.where(PurchaseInvoice.status == status)
    elif status == "nieoplacone":
        stmt = stmt.where(PurchaseInvoice.paid_at.is_(None), PurchaseInvoice.status != "rejected")
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(PurchaseInvoice.invoice_number.ilike(like), PurchaseInvoice.seller_name.ilike(like),
                              PurchaseInvoice.seller_nip.ilike(like), PurchaseInvoice.ksef_number.ilike(like)))
    rows = db.scalars(stmt.order_by(PurchaseInvoice.issue_date.desc(), PurchaseInvoice.id.desc()).limit(300)).all()
    new_count = db.scalar(select(func.count()).select_from(PurchaseInvoice).where(
        PurchaseInvoice.company_id == company.id, PurchaseInvoice.status == "new"))
    unpaid = db.scalars(base.where(PurchaseInvoice.paid_at.is_(None), PurchaseInvoice.status != "rejected")).all()
    return render(request, db, "purchases.html", rows=rows, status=status, q=q, STATUSES=PURCHASE_STATUSES,
                  new_count=new_count, unpaid_count=len(unpaid),
                  unpaid_sum=sum((p.gross for p in unpaid if p.currency == "PLN"), Decimal("0")),
                  ksef_on=ksef.enabled(company), today=dt.date.today())


@app.post("/koszty/pobierz")
async def purchases_fetch(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    since = None
    if form.get("since"):
        try:
            since = dt.date.fromisoformat(form.get("since"))
        except ValueError:
            since = None
    try:
        n = purchases.fetch(db, web_actor(request), company, since=since)
        flash(request, f"Pobrano z KSeF {n} nowych faktur kosztowych." if n else "Brak nowych faktur kosztowych w KSeF.")
    except ServiceError as e:
        flash(request, str(e))
    return redirect("/koszty")


@app.get("/koszty/{pid}", response_class=HTMLResponse)
def purchase_view(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_purchase(db, request, pid)
    try:
        data = ksef_incoming.parse(p.xml) if p.xml else {}
    except Exception:  # noqa: BLE001
        data = {}
    return render(request, db, "purchase_view.html", pi=p, data=data, STATUSES=PURCHASE_STATUSES,
                  today_iso=dt.date.today().isoformat())


@app.post("/koszty/{pid}")
async def purchase_update(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_purchase(db, request, pid)
    form = await request.form()
    action = form.get("action") or ""
    if action in PURCHASE_STATUSES:
        p.status = action
    if action == "paid":
        try:
            p.paid_at = dt.date.fromisoformat(form.get("paid_at") or "")
        except ValueError:
            p.paid_at = dt.date.today()
    elif action == "unpaid":
        p.paid_at = None
    if "notes" in form:
        p.notes = (form.get("notes") or "").strip()
    audit(db, web_actor(request), "purchase.update",
          f"Koszt {p.invoice_number} od {p.seller_name[:80]}: {p.status_label}{', zapłacona' if p.paid_at else ''}",
          company_id=p.company_id, entity="purchase", entity_id=p.id)
    db.commit()
    flash(request, "Zapisano.")
    return redirect(f"/koszty/{p.id}")


@app.get("/koszty/{pid}/xml")
def purchase_xml(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_purchase(db, request, pid)
    return Response(p.xml.encode("utf-8"), media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{p.ksef_number}.xml"'})
