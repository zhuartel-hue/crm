"""Aplikacja www: faktury, kontrahenci, auta, ustawienia firm."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from urllib.parse import quote
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, defer, selectinload
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import dashboard as dash
from . import offer_statuses as ost
from . import calls, orders, reports
from . import assistant, features, jobs, tenancy
from . import users as users_mod
from . import sms as sms_mod
from . import api, auth, calc, config, firmao_import, mail_templates, mcp_server, migrate, nip_lookup, numbering, offers, password, payments, pdf, recurring, secretbox, wz
from . import products as prod
from . import tasks as tasks_mod
from .bank import service as bank
from .bank import connect as bank_connect
from .bank import enablebanking
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
    CALL_DIRECTIONS,
    CALL_STATUSES,
    PhoneCall,
    ORDER_DIRECTIONS,
    Order,
    CONTRACT_FILE_KINDS,
    ContractFile,
    CAR_STATUSES,
    DELIVERY_KINDS,
    SCOPES,
    ApiToken,
    AppSetting,
    AuditLog,
    BankConnection,
    BankTransaction,
    MailAccount,
    MailAttachment,
    MailMessage,
    OFFER_STATUSES,
    Offer,
    OfferTemplate,
    INVOICE_KINDS,
    INVOICE_TEMPLATES,
    OFFER_PDF_TEMPLATES,
    NEW_KINDS,
    PAYMENT_METHODS,
    VAT_RATES,
    Car,
    Company,
    Contact,
    Contractor,
    ContractorFile,
    CUSTOMER_TYPES,
    SmsMessage,
    ContractorNote,
    Delivery,
    DeliveryItem,
    Invoice,
    InvoiceItem,
    Product,
    ProductPhoto,
    PURCHASE_STATUSES,
    PurchaseFile,
    PurchaseInvoice,
    StockMovement,
    TEMPLATE_KINDS,
    EmailTemplate,
    RECURRING_INTERVALS,
    RECURRING_MODES,
    RecurringInvoice,
    SERVICE_KINDS,
    TASK_PRIORITIES,
    TASK_STATUSES,
    Equipment,
    ServiceEntry,
    Task,
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
templates.env.globals["ksef_can_send"] = ksef.can_send
templates.env.globals["wz_payment"] = wz.payment_state
templates.env.globals["CUSTOMER_TYPES"] = CUSTOMER_TYPES
templates.env.globals["TEST_PANEL"] = config.TEST_PANEL


def _klink(contractor_id, name, cls: str = "klink"):
    """Nazwa kontrahenta jako wyrazny link do jego karty (gdy jest w bazie), inaczej sam tekst."""
    from markupsafe import Markup, escape
    if contractor_id:
        return Markup('<a class="{}" href="/kontrahenci/{}">{}</a>').format(cls, int(contractor_id), name or "kontrahent")
    return escape(name or "")


templates.env.globals["klink"] = _klink
templates.env.globals["INVOICE_TEMPLATES"] = INVOICE_TEMPLATES
templates.env.globals["OFFER_PDF_TEMPLATES"] = OFFER_PDF_TEMPLATES
templates.env.globals["ksef_status_label"] = ksef.status_label
templates.env.globals["pomoc_ai"] = assistant.ai_enabled


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate.upgrade()
    with SessionLocal() as db:
        seed(db)
        users_mod.ensure_default_org(db)
    # Zadania w tle w tym procesie (BACKGROUND_JOBS=app) albo w osobnym workerze (off), app/jobs.py.
    stop = jobs.start() if config.BACKGROUND_JOBS == "app" and not config.TEST_PANEL else None
    yield
    if stop is not None:
        stop.set()


app = FastAPI(title="Sygnavo", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

PUBLIC_PATHS = ("/polityka-prywatnosci", "/regulamin", "/login", "/static", "/health", "/sw.js", "/api/", "/mcp", "/oauth/", "/.well-known/")


@app.middleware("http")
async def require_login(request: Request, call_next):
    if not request.url.path.startswith(PUBLIC_PATHS) and not request.session.get("user"):
        if request.method == "GET" and request.url.path == "/bank/powrot":
            # Powrot z banku moze otworzyc sie w innej karcie: po zalogowaniu wracamy tutaj.
            request.session["next"] = request.url.path + ("?" + request.url.query if request.url.query else "")
        return RedirectResponse("/login", status_code=303)
    if request.session.get("user") and not request.url.path.startswith(PUBLIC_PATHS):
        ids = await run_in_threadpool(_check_session, request)
        if ids is None:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)
        path = request.url.path
        if request.session.get("must_change") and not path.startswith(("/konto", "/logout", "/static")):
            return RedirectResponse("/konto", status_code=303)
        if not users_mod.can_open(request.session.get("role"), path):
            return HTMLResponse("<p>Ta część programu nie jest dostępna dla Twojej roli. "
                                "<a href=\"/\">Wróć na Start</a></p>", status_code=403)
        # Od tej chwili wszystkie zapytania tej strony widza tylko firmy organizacji (app/tenancy.py).
        token = tenancy.set_scope(ids)
        try:
            response = await call_next(request)
        finally:
            tenancy.reset_scope(token)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response
    response = await call_next(request)
    # Strona zgody OAuth i formularze nie moga byc osadzone w cudzej ramce (clickjacking).
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


def _check_session(request: Request) -> list[int] | None:
    """Sprawdza konto zalogowanej osoby; zwraca id firm jej organizacji albo None (wyloguj)."""
    with SessionLocal() as db:
        if not users_mod.refresh_session(db, request):
            return None
        user = db.get(users_mod.User, request.session["user_id"])
        request.session["must_change"] = bool(user and user.must_change_password)
        return [c.id for c in users_mod.org_companies(db, request.session.get("org_id"))]


app.include_router(auth.router)
app.include_router(users_mod.router)
app.include_router(mcp_server.router)
app.include_router(assistant.router)
from .integrations import web as integrations_web  # noqa: E402

app.include_router(integrations_web.router)
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


def _own(db: Session, request: Request, model, oid):
    """Rekord (kontrahent, produkt, osoba) bieżącej firmy albo None. Nie pokazujemy danych innej firmy."""
    try:
        oid = int(oid)
    except (TypeError, ValueError):
        return None
    obj = db.get(model, oid) if oid else None
    return obj if obj is not None and obj.company_id == current_company(request, db).id else None


def _contractors(db: Session, company_id: int) -> list[Contractor]:
    return list(db.scalars(select(Contractor).where(Contractor.company_id == company_id).order_by(Contractor.name)))


def current_company(request: Request, db: Session) -> Company:
    companies = users_mod.org_companies(db, request.session.get("org_id"))
    if not companies:
        raise HTTPException(409, "Ta organizacja nie ma jeszcze żadnej firmy.")
    cid = request.session.get("company_id")
    for c in companies:
        if c.id == cid:
            return c
    request.session["company_id"] = companies[0].id
    return companies[0]


def render(request: Request, db: Session, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("company", current_company(request, db))
    ctx["companies"] = users_mod.org_companies(db, request.session.get("org_id"))
    ctx["role"] = request.session.get("role", "wlasciciel")
    ctx["can_open"] = lambda path: users_mod.can_open(ctx["role"], path)
    org = db.get(users_mod.Organisation, request.session.get("org_id") or 0)
    ctx["feature"] = lambda key: features.enabled(org, key)
    ctx["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(request, name, ctx)


def web_actor(request: Request) -> str:
    return f"www ({request.session.get('user', '?')})"


def flash(request: Request, text: str) -> None:
    request.session["flash"] = text


# --- logowanie ---------------------------------------------------------


@app.get("/health")
def health():
    return {"ok": True, "version": config.APP_VERSION, "test": config.TEST_PANEL}


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, login: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = users_mod.authenticate(db, login, password)
    if user is None:
        return templates.TemplateResponse(request, "login.html", {"error": "Zły login lub hasło."}, status_code=401)
    nxt = request.session.pop("next", None)
    if users_mod.has_2fa(user):
        users_mod.begin_2fa_login(request, user, nxt)
        return redirect("/login/kod")
    request.session.clear()  # nowa sesja po zalogowaniu (ochrona przed przejeciem sesji)
    users_mod.start_session(request, user)
    if user.must_change_password:
        request.session["must_change"] = True
        flash(request, "Ustaw własne hasło, zanim zaczniesz pracę.")
        return redirect("/konto")
    return redirect(nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else "/")


def password_check(db: Session, login: str, pw: str) -> bool:
    return password.check_login(db, login, pw)


@app.get("/konto", response_class=HTMLResponse)
def account(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "account.html", **users_mod.account_context(request, db))


@app.post("/konto/haslo")
def account_password(request: Request, old: str = Form(""), new: str = Form(""), repeat: str = Form(""),
                     db: Session = Depends(get_db)):
    user = db.get(users_mod.User, request.session.get("user_id") or 0)
    if user is None:
        return redirect("/login")
    try:
        users_mod.change_password(db, user, old, new, repeat)
    except ValueError as e:
        return render(request, db, "account.html", error=str(e), **users_mod.account_context(request, db))
    audit(db, web_actor(request), "account.password", "Zmieniono hasło logowania")
    db.commit()
    request.session.pop("must_change", None)
    flash(request, "Hasło zmienione. Od teraz loguj się nowym hasłem.")
    return redirect("/konto")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@app.post("/firma/{company_id}")
def switch_company(company_id: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(Company, company_id)
    org_id = request.session.get("org_id")
    if c is None or (org_id is not None and c.organisation_id != org_id):
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
    issued = [Invoice.company_id == company.id, Invoice.status == "issued", Invoice.kind != "proforma"]
    month_count, month_gross = db.execute(select(func.count(), func.coalesce(func.sum(Invoice.total_gross), 0))
                                          .where(*issued, Invoice.issue_date >= month_start)).one()
    open_cond = [*issued, Invoice.paid < Invoice.total_gross]
    unpaid_count, unpaid_sum = db.execute(select(func.count(), func.coalesce(func.sum(Invoice.total_gross - Invoice.paid), 0))
                                          .where(*open_cond)).one()
    overdue = db.scalars(select(Invoice).where(*open_cond, Invoice.due_date < today)
                         .order_by(Invoice.due_date.desc()).limit(8)).all()
    overdue_count = db.scalar(select(func.count()).select_from(Invoice).where(*open_cond, Invoice.due_date < today))
    recent = db.scalars(select(Invoice).where(Invoice.company_id == company.id)
                        .order_by(Invoice.issue_date.desc(), Invoice.id.desc()).limit(8)).all()
    deliveries = db.scalars(select(Delivery).where(Delivery.company_id == company.id)
                            .order_by(Delivery.issue_date.desc(), Delivery.id.desc()).limit(8)).all()
    mails = db.scalars(select(MailMessage).join(MailAccount, MailMessage.account_id == MailAccount.id)
                       .where(MailAccount.company_id == company.id, MailMessage.direction == "in")
                       .order_by(MailMessage.date.desc()).limit(8)).all()
    cars_on_lot = db.scalars(
        select(Car).where(Car.company_id == company.id, Car.status != "sold").order_by(Car.id.desc())
    ).all()
    ksef_todo = db.scalar(select(func.count()).select_from(Invoice).where(
        *issued, Invoice.ksef_status.in_(("", "rejected", "error")))) if ksef.enabled(company) else None
    stats = dict(month_count=month_count, month_gross=month_gross, unpaid_count=unpaid_count, unpaid_sum=unpaid_sum,
                 overdue_count=overdue_count, ksef_todo=ksef_todo)
    equipment_due = [e for e in db.scalars(select(Equipment).where(
        Equipment.company_id == company.id, Equipment.active,
        or_(Equipment.next_service <= today + dt.timedelta(days=14),
            Equipment.warranty_until.between(today, today + dt.timedelta(days=90))))
        .order_by(Equipment.next_service, Equipment.warranty_until).limit(30)).all()
        if e.service_state(today) in ("over", "soon") or e.warranty_state(today) == "soon"][:8]
    tasks_today = tasks_mod.today_tasks(db, company.id, today, limit=8)
    layout, custom = dash.load(db, company.id, request.session.get("user", ""), has_cars=bool(cars_on_lot))
    shown = {it["k"] for it in layout}
    return render(request, db, "dashboard.html", company=company, recent=recent, stats=stats, cars=cars_on_lot,
                  equipment_due=equipment_due, tasks_today=tasks_today, now=dt.datetime.now(),
                  bank_warnings=bank_connect.warnings(db, company.id), overdue=overdue, deliveries=deliveries,
                  mails=mails, offers_expiring=offers.expiring(db, company.id), today=today,
                  orders_due=orders.due_soon(db, company.id),
                  layout=layout, custom_layout=custom, WIDGETS=dash.WIDGETS, DASH_COLS=dash.COLS, DASH_MAX_H=dash.MAX_H,
                  hidden=[k for k in dash.WIDGETS if k not in shown])


def _cols_key(request: Request, table: str) -> str:
    import hashlib

    user = request.session.get("user", "")
    return "cols:" + hashlib.sha1(f"{user}|{table}".encode()).hexdigest()[:40]


@app.get("/ustawienia/kolumny")
def table_columns_get(request: Request, t: str = "", db: Session = Depends(get_db)):
    """Zapamietane kolumny listy (kolejnosc i szerokosci) dla zalogowanego uzytkownika."""
    row = db.get(AppSetting, _cols_key(request, t[:300]))
    try:
        return json.loads(row.value) if row else {}
    except ValueError:
        return {}


@app.post("/ustawienia/kolumny")
async def table_columns_save(request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400)
    key = _cols_key(request, str(data.get("t") or "")[:300])
    row = db.get(AppSetting, key)
    if data.get("reset"):
        if row is not None:
            db.delete(row)
        db.commit()
        return {"ok": True}
    order = [int(x) for x in (data.get("order") or []) if isinstance(x, int) and 0 <= x < 60][:60]
    widths = {str(int(k)): min(max(int(v), 40), 2000) for k, v in (data.get("widths") or {}).items()
              if str(k).isdigit() and isinstance(v, (int, float))}
    row = row or AppSetting(key=key)
    row.value = json.dumps({"order": order, "widths": widths})
    db.add(row)
    db.commit()
    return {"ok": True}


@app.post("/start/uklad")
async def dashboard_layout_save(request: Request, db: Session = Depends(get_db)):
    """Zapis ukladu Startu (JSON z edytora) albo powrot do domyslnego."""
    company = current_company(request, db)
    user = request.session.get("user", "")
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400)
    if data.get("reset"):
        dash.reset(db, company.id, user)
        db.commit()
        return {"ok": True}
    layout = dash.save(db, company.id, user, data.get("items"))
    db.commit()
    return {"ok": True, "items": layout}


# --- faktury --------------------------------------------------------------


LIST_LIMIT = 200


@app.get("/faktury", response_class=HTMLResponse)
def invoice_list(request: Request, rodzaj: str = "", miesiac: str = "", q: str = "", ksef_f: str = Query("", alias="ksef"),
                 wszystkie: int = 0, db: Session = Depends(get_db)):
    company = current_company(request, db)
    cond = [Invoice.company_id == company.id]
    if rodzaj in INVOICE_KINDS:
        cond.append(Invoice.kind == rodzaj)
    if miesiac:
        try:
            y, m = (int(x) for x in miesiac.split("-"))
            start = dt.date(y, m, 1)
            end = dt.date(y + (m == 12), m % 12 + 1, 1)
            cond += [Invoice.issue_date >= start, Invoice.issue_date < end]
        except ValueError:
            pass
    if q:
        like = f"%{q}%"
        cond.append(or_(Invoice.number.ilike(like), Invoice.buyer_name.ilike(like), Invoice.buyer_nip.ilike(like)))
    if ksef_f == "do-wyslania":
        cond += [Invoice.status == "issued", Invoice.kind != "proforma",
                 Invoice.ksef_status.in_(("", "rejected", "error"))]
    count = db.scalar(select(func.count()).select_from(Invoice).where(*cond)) or 0
    total = db.scalar(select(func.coalesce(func.sum(Invoice.total_gross), 0)).where(
        *cond, Invoice.status == "issued", Invoice.kind != "proforma")) or Decimal("0")
    stmt = select(Invoice).where(*cond).order_by(Invoice.issue_date.desc(), Invoice.id.desc())
    if not wszystkie:
        stmt = stmt.limit(LIST_LIMIT)
    invoices = db.scalars(stmt).all()
    return render(request, db, "invoices.html", invoices=invoices, rodzaj=rodzaj, miesiac=miesiac, q=q, total=total,
                  ksef_f=ksef_f, count=count, limited=len(invoices) < count, ksef_select=True,
                  ksef_on=ksef.enabled(company))


@app.post("/faktury/ksef")
async def invoice_ksef_send_many(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    ids = [int(x) for x in form.getlist("ids") if str(x).isdigit()]
    back = form.get("back") or "/faktury"
    if not back.startswith("/faktury"):
        back = "/faktury"
    if not ids:
        flash(request, "Zaznacz faktury do wysłania.")
        return redirect(back)
    res = ksef.send_many(db, web_actor(request), ids, company.id)
    parts = []
    if res["accepted"]:
        parts.append(f"KSeF przyjął {len(res['accepted'])}: {', '.join(res['accepted'])}")
    if res["pending"]:
        parts.append(f"w trakcie {len(res['pending'])}: {', '.join(res['pending'])} (sprawdź status za chwilę)")
    if res["failed"]:
        parts.append(f"nie poszło {len(res['failed'])}: {'; '.join(res['failed'])}")
    flash(request, ". ".join(parts) or "Nic nie wysłano.")
    return redirect(back)


def _form_context(db: Session, company: Company) -> dict:
    contractors = _contractors(db, company.id)
    cars = db.scalars(select(Car).where(Car.company_id == company.id).order_by(Car.id.desc())).all()
    contractors_json = json.dumps(
        {
            c.id: dict(name=c.name, nip=c.nip, address="\n".join(c.address_lines))
            for c in contractors
        },
        ensure_ascii=False,
    )
    products = prod.search(db, company.id, active_only=True)
    products_json = script_json(prod.picker_json(products))
    return dict(contractors=contractors, cars=cars, contractors_json=contractors_json,
                products=products, products_json=products_json)


@app.get("/faktury/nowa", response_class=HTMLResponse)
def invoice_new(request: Request, rodzaj: str = "vat", auto: int | None = None, kontrahent: int = 0,
                db: Session = Depends(get_db)):
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
    buyer = _own(db, request, Contractor, kontrahent) if kontrahent else None
    if buyer is not None:
        inv.contractor_id, inv.buyer_name, inv.buyer_nip = buyer.id, buyer.name, buyer.nip
        inv.buyer_address = "\n".join(buyer.address_lines)
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
    own = db.get(Contractor, int(cid)) if cid.isdigit() else None
    inv.contractor_id = own.id if own is not None and own.company_id == inv.company_id else None
    if inv.contractor_id is None and form.get("save_contractor") == "1" and inv.buyer_name:
        lines = inv.buyer_address.splitlines()
        street = lines[0] if lines else ""
        postal, _, city = (lines[1] if len(lines) > 1 else "").partition(" ")
        c = Contractor(
            company_id=inv.company_id, name=inv.buyer_name, nip=inv.buyer_nip, is_company=bool(inv.buyer_nip),
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
    valid_products = set(db.scalars(select(Product.id).where(Product.company_id == inv.company_id)).all()) if any(product_ids) else set()
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
    if inv.status == "issued":
        msg = ksef.auto_send(db, web_actor(request), inv)
        if msg:
            flash(request, msg)
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
    msg = ksef.auto_send(db, web_actor(request), inv)
    if msg:
        flash(request, msg)
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
def contractor_list(request: Request, q: str = "", widok: str = "", db: Session = Depends(get_db)):
    stmt = select(Contractor).where(Contractor.company_id == current_company(request, db).id)
    if widok == "kontakt":
        stmt = stmt.where(Contractor.next_contact.is_not(None), Contractor.next_contact <= dt.date.today())
    elif widok in CUSTOMER_TYPES:
        stmt = stmt.where(Contractor.customer_type == widok)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Contractor.name.ilike(like), Contractor.nip.ilike(like), Contractor.city.ilike(like),
                              Contractor.email.ilike(like), Contractor.phone.ilike(like),
                              Contractor.id.in_(select(Contact.contractor_id).where(or_(
                                  Contact.last_name.ilike(like), Contact.first_name.ilike(like),
                                  Contact.email.ilike(like), Contact.phone.ilike(like))))))
    return render(request, db, "contractors.html", contractors=db.scalars(stmt.order_by(Contractor.name)).all(), q=q,
                  widok=widok, CUSTOMER_TYPES=CUSTOMER_TYPES, today=dt.date.today())


CONTRACTOR_FIELDS = ["name", "nip", "pesel", "street", "postal_code", "city", "country", "email", "phone", "website",
                     "notes", "regon", "krs", "bank_account", "corr_address"]


@app.get("/kontrahenci/nowy", response_class=HTMLResponse)
def contractor_new(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "contractor_form.html", c=Contractor(is_company=True, country="PL"))


@app.get("/kontrahenci/{cid}", response_class=HTMLResponse)
def contractor_edit(cid: int, request: Request, db: Session = Depends(get_db)):
    c = _own(db, request, Contractor, cid)
    if c is None:
        raise HTTPException(404)
    invoices = db.scalars(select(Invoice).where(Invoice.contractor_id == cid)
                          .order_by(Invoice.issue_date.desc()).limit(100)).all()
    mail_stmt = _contractor_mail_stmt(db, c, current_company(request, db).id)
    mails = db.scalars(mail_stmt.options(selectinload(MailMessage.attachments).defer(MailAttachment.data))
                       .order_by(MailMessage.date.desc().nulls_last()).limit(25)).all() \
        if mail_stmt is not None else []
    mail_count = db.scalar(select(func.count()).select_from(mail_stmt.subquery())) if mail_stmt is not None else 0
    offer_rows = db.scalars(select(Offer).where(Offer.contractor_id == cid,
                                                Offer.company_id == current_company(request, db).id)
                            .order_by(Offer.issue_date.desc()).limit(50)).all()
    deliveries = db.scalars(select(Delivery).where(Delivery.contractor_id == cid,
                                                   Delivery.company_id == current_company(request, db).id)
                            .order_by(Delivery.issue_date.desc()).limit(30)).all()
    equipment = db.scalars(select(Equipment).where(Equipment.contractor_id == cid,
                                                   Equipment.company_id == current_company(request, db).id)
                           .order_by(Equipment.active.desc(), Equipment.name)).all()
    contracts = db.scalars(select(RecurringInvoice).where(RecurringInvoice.contractor_id == cid,
                                                          RecurringInvoice.company_id == current_company(request, db).id)
                           .order_by(RecurringInvoice.active.desc(), RecurringInvoice.id.desc())).all()
    order_rows = db.scalars(select(Order).where(Order.contractor_id == cid,
                                                Order.company_id == current_company(request, db).id)
                            .order_by(Order.issue_date.desc()).limit(30)).all()
    call_rows = db.scalars(select(PhoneCall).where(PhoneCall.contractor_id == cid,
                                                   PhoneCall.company_id == current_company(request, db).id)
                           .order_by(PhoneCall.started_at.desc()).limit(15)).all()
    company_id = current_company(request, db).id
    task_rows = db.scalars(select(Task).where(Task.contractor_id == cid, Task.parent_id.is_(None),
                                              Task.company_id == company_id)
                           .order_by(Task.status.in_(("done", "cancelled")), Task.due_at.desc()).limit(30)).all()
    # koszty od tego kontrahenta (powiazane albo po NIP sprzedawcy)
    pcond = PurchaseInvoice.contractor_id == cid
    nip = "".join(ch for ch in (c.nip or "") if ch.isdigit())
    if len(nip) == 10:
        pcond = or_(pcond, PurchaseInvoice.seller_nip == nip)
    purchase_rows = db.scalars(select(PurchaseInvoice).where(PurchaseInvoice.company_id == company_id, pcond)
                               .order_by(PurchaseInvoice.issue_date.desc(), PurchaseInvoice.id.desc()).limit(30)).all()
    # naleznosci od kontrahenta (wszystkie faktury, nie tylko pokazane)
    unpaid = db.scalars(select(Invoice).where(Invoice.contractor_id == cid, Invoice.company_id == company_id,
                                              Invoice.status == "issued", Invoice.kind.in_(payments.PAYABLE_KINDS),
                                              Invoice.paid < Invoice.total_gross)).all()
    owed = payments.summary(list(unpaid))
    sales_total = db.scalar(select(func.coalesce(func.sum(Invoice.total_gross), 0)).where(
        Invoice.contractor_id == cid, Invoice.company_id == company_id, Invoice.status == "issued",
        Invoice.kind.in_(payments.PAYABLE_KINDS)))
    # historia zmian: kontrahent i jego dokumenty
    refs = {"contractor": [cid], "invoice": [i.id for i in invoices], "wz": [d.id for d in deliveries],
            "offer": [o.id for o in offer_rows], "order": [z.id for z in order_rows],
            "recurring": [r.id for r in contracts], "equipment": [e.id for e in equipment],
            "task": [t.id for t in task_rows], "purchase": [x.id for x in purchase_rows]}
    conds = [and_(AuditLog.entity == k, AuditLog.entity_id.in_(v)) for k, v in refs.items() if v]
    history = db.scalars(select(AuditLog).where(or_(AuditLog.company_id == company_id, AuditLog.entity == "contractor"),
                                                or_(*conds))
                         .order_by(AuditLog.id.desc()).limit(40)).all()
    sms_rows = db.scalars(select(SmsMessage).where(SmsMessage.contractor_id == cid, SmsMessage.company_id == company_id)
                          .order_by(SmsMessage.id.desc()).limit(30)).all()
    timeline = _contractor_timeline(c, invoices, deliveries, offer_rows, order_rows, mails, call_rows, sms_rows,
                                    task_rows, purchase_rows, history)
    files = db.scalars(select(ContractorFile).options(defer(ContractorFile.data))
                       .where(ContractorFile.contractor_id == cid, ContractorFile.company_id == company_id)
                       .order_by(ContractorFile.id.desc())).all()
    contract_files = db.scalars(select(ContractFile).options(defer(ContractFile.data))
                                .where(ContractFile.recurring_id.in_([r.id for r in contracts]),
                                       ContractFile.company_id == company_id)
                                .order_by(ContractFile.id.desc())).all() if contracts else []
    mail_files = [(m, a) for m in mails for a in m.attachments]
    accounts = [a for a in _company_accounts(db, company_id) if a.active]
    phones = [x for x in [c.phone] + [p.phone for p in c.contacts] if x]
    emails = [x for x in [c.email] + [p.email for p in c.contacts] if x]
    return render(request, db, "contractor_form.html", c=c, invoices=invoices, mails=mails, offers=offer_rows,
                  mail_count=mail_count, sms_rows=sms_rows, files=files, contract_files=contract_files,
                  mail_files=mail_files, timeline=timeline, accounts=accounts, phones=phones,
                  emails=emails, CUSTOMER_TYPES=CUSTOMER_TYPES, sms_ready=sms_mod.provider_configured(None),
                  orders=order_rows, calls=call_rows, CALL_DIRECTIONS=CALL_DIRECTIONS,
                  deliveries=deliveries, equipment=equipment, contracts=contracts, today=dt.date.today(),
                  task_rows=task_rows, purchases=purchase_rows, owed=owed, sales_total=sales_total,
                  history=history, now=dt.datetime.now())


def _contractor_timeline(c, invoices, deliveries, offers_, orders_, mails, calls, sms_rows, tasks, purchases_, history):
    """Jedna os czasu kontrahenta (najnowsze na gorze): wszystko, co sie z nim dzialo."""
    def at(d):
        if d is None:
            return dt.datetime.min
        return d if isinstance(d, dt.datetime) else dt.datetime.combine(d, dt.time())
    ev = []
    for n in c.note_rows:
        ev.append((at(n.at), "notatka", n.text[:300], "", n.author))
    for m in mails:
        ev.append((at(m.date), "e-mail wysłany" if m.direction == "out" else "e-mail odebrany",
                   m.subject or "(bez tematu)", f"/poczta/{m.id}", m.from_name or m.from_addr))
    for x in sms_rows:
        ev.append((at(x.created_at), "SMS", f"{x.body[:200]} ({x.status_label})", "", x.created_by))
    for x in calls:
        ev.append((at(x.started_at), "rozmowa", (x.note or x.status_label)[:200], "", x.user_label))
    for i in invoices:
        ev.append((at(i.issue_date), "faktura", f"{i.kind_label} {i.display_number}, {calc.format_money(i.total_gross)} zł",
                   f"/faktury/{i.id}", ""))
    for d in deliveries:
        ev.append((at(d.issue_date), d.kind_short, d.display_number, f"/wz/{d.id}", ""))
    for o in offers_:
        ev.append((at(o.issue_date), "oferta", f"{o.display_number} {o.title or ''}".strip(), f"/oferty/{o.id}", ""))
    for z in orders_:
        ev.append((at(z.issue_date), "zamówienie", f"{z.display_number} {z.title or ''}".strip(), f"/zamowienia/{z.id}", ""))
    for t in tasks:
        ev.append((at(t.due_at or t.created_at), "zadanie", t.title, f"/zadania/{t.id}", ""))
    for p in purchases_:
        ev.append((at(p.issue_date), "koszt", f"{p.invoice_number}, {calc.format_money(p.gross)} {p.currency}",
                   f"/koszty/{p.id}", ""))
    for h in history:
        if h.action.endswith(".issue") or h.action == "mail.send":
            continue  # wystawienie/wysylka sa juz wyzej jako dokument albo mail
        ev.append((at(h.at), "zmiana", h.summary[:300], "", h.actor))
    ev.sort(key=lambda e: e[0], reverse=True)
    return [dict(at=e[0], kind=e[1], text=e[2], href=e[3], who=e[4]) for e in ev[:60]]


def _contractor_mail_stmt(db: Session, c: Contractor, company_id: int, contact: Contact | None = None):
    """Maile z firma (adresy kontrahenta i jego osob) albo tylko z jedna osoba kontaktowa, w skrzynkach tej firmy."""
    from .mail.service import _emails_of

    accounts = select(MailAccount.id).where(MailAccount.company_id == company_id)
    emails = _emails_of(contact.email) if contact else \
        _emails_of(c.email) + [e for p in c.contacts for e in _emails_of(p.email)]
    conds = [MailMessage.from_addr.ilike(e) for e in emails]
    conds += [f.ilike(f"%{e}%") for e in emails for f in (MailMessage.to_addrs, MailMessage.cc_addrs)]
    if contact is None:
        conds.append(MailMessage.contractor_id == c.id)
    if not conds:
        return None
    return select(MailMessage).where(MailMessage.account_id.in_(accounts), or_(*conds))


@app.get("/kontrahenci/{cid}/maile", response_class=HTMLResponse)
def contractor_mails(cid: int, request: Request, osoba: int = 0, kierunek: str = "", q: str = "",
                     db: Session = Depends(get_db)):
    c = _own(db, request, Contractor, cid)
    if c is None:
        raise HTTPException(404)
    contact = next((p for p in c.contacts if p.id == osoba), None)
    stmt = _contractor_mail_stmt(db, c, current_company(request, db).id, contact)
    rows = []
    if stmt is not None:
        if kierunek in ("in", "out"):
            stmt = stmt.where(MailMessage.direction == kierunek)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(MailMessage.subject.ilike(like), MailMessage.body_text.ilike(like),
                                  MailMessage.from_name.ilike(like), MailMessage.from_addr.ilike(like)))
        rows = db.scalars(stmt.order_by(MailMessage.date.desc().nulls_last()).limit(500)).all()
    return render(request, db, "contractor_mails.html", c=c, contact=contact, rows=rows, osoba=osoba,
                  kierunek=kierunek, q=q)


async def _save_contractor(request: Request, db: Session, c: Contractor):
    form = await request.form()
    for f in CONTRACTOR_FIELDS:
        setattr(c, f, (form.get(f) or "").strip())
    c.nip = c.nip.replace("-", "").replace(" ", "")
    c.country = (c.country or "PL").upper()[:2]
    c.is_company = form.get("is_company") == "1"
    c.ksef_auto = form.get("ksef_auto") == "1"
    c.customer_type = form.get("customer_type") if form.get("customer_type") in CUSTOMER_TYPES else ""
    try:
        c.next_contact = dt.date.fromisoformat(form.get("next_contact") or "")
    except ValueError:
        c.next_contact = None
    if not c.name:
        if c.id:
            db.rollback()
            flash(request, "Podaj nazwę albo imię i nazwisko.")
            return redirect(f"/kontrahenci/{c.id}")
        return render(request, db, "contractor_form.html", c=c, error="Podaj nazwę albo imię i nazwisko.")
    if c.company_id is None:
        c.company_id = current_company(request, db).id
    is_new = c.id is None
    db.add(c)
    db.commit()
    flash(request, "Zapisano kontrahenta.")
    return redirect("/kontrahenci" if is_new else f"/kontrahenci/{c.id}")


@app.post("/kontrahenci/nowy")
async def contractor_create(request: Request, db: Session = Depends(get_db)):
    return await _save_contractor(request, db, Contractor())


@app.post("/kontrahenci/{cid}")
async def contractor_update(cid: int, request: Request, db: Session = Depends(get_db)):
    c = _own(db, request, Contractor, cid)
    if c is None:
        raise HTTPException(404)
    return await _save_contractor(request, db, c)


@app.post("/kontrahenci/{cid}/pliki")
async def contractor_file_add(cid: int, request: Request, db: Session = Depends(get_db)):
    """Pliki na karcie kontrahenta; kilka naraz, do 25 MB kazdy."""
    c = _own(db, request, Contractor, cid)
    if c is None:
        raise HTTPException(404)
    form = await request.form()
    title = (form.get("title") or "").strip()[:200]
    added, skipped = 0, []
    for f in form.getlist("files"):
        if not getattr(f, "filename", None):
            continue
        raw = await f.read()
        name = os.path.basename(f.filename.replace("\\", "/"))[:200]
        if not raw or len(raw) > CONTRACT_FILE_MAX:
            skipped.append(name)
            continue
        ctype = (f.content_type or "").split(";")[0].strip().lower()[:100] or "application/octet-stream"
        if raw[:4] == b"%PDF":
            ctype = "application/pdf"
        db.add(ContractorFile(company_id=c.company_id, contractor_id=c.id, title=title or name, filename=name,
                              content_type=ctype, size=len(raw), data=raw,
                              added_by=str(request.session.get("user", ""))[:60]))
        added += 1
    if added:
        audit(db, web_actor(request), "contractor.file_add", f"{c.name}: dodano {added} plik(i)",
              company_id=c.company_id, entity="contractor", entity_id=c.id)
        db.commit()
    msg = f"Dodano plików: {added}." if added else "Nie wybrano pliku."
    if skipped:
        msg += " Pominięte (puste albo ponad 25 MB): " + ", ".join(skipped)
    flash(request, msg)
    return redirect(f"/kontrahenci/{c.id}#pliki")


def _contractor_file(db: Session, request: Request, cid: int, fid: int) -> ContractorFile:
    c = _own(db, request, Contractor, cid)
    f = db.get(ContractorFile, fid)
    if c is None or f is None or f.contractor_id != c.id or f.company_id != c.company_id:
        raise HTTPException(404)
    return f


@app.get("/kontrahenci/{cid}/pliki/{fid}")
def contractor_file_get(cid: int, fid: int, request: Request, pobierz: int = 0, db: Session = Depends(get_db)):
    f = _contractor_file(db, request, cid, fid)
    inline = f.content_type in _INLINE_TYPES and not pobierz
    ctype = f.content_type if f.content_type in _INLINE_TYPES else "application/octet-stream"
    safe = "".join(ch if ch.isascii() and ch.isprintable() and ch not in '"\\' else "_" for ch in f.filename) or "plik"
    disp = f"{'inline' if inline else 'attachment'}; filename=\"{safe}\"; filename*=UTF-8''{quote(f.filename)}"
    return Response(f.data, media_type=ctype, headers={"Content-Disposition": disp, "X-Content-Type-Options": "nosniff",
                                                       "Content-Security-Policy": "sandbox"})


@app.post("/kontrahenci/{cid}/pliki/{fid}/usun")
def contractor_file_delete(cid: int, fid: int, request: Request, db: Session = Depends(get_db)):
    f = _contractor_file(db, request, cid, fid)
    audit(db, web_actor(request), "contractor.file_delete", f"Usunięto plik „{f.title}” ({f.filename})",
          company_id=f.company_id, entity="contractor", entity_id=cid)
    db.delete(f)
    db.commit()
    flash(request, "Plik usunięty.")
    return redirect(f"/kontrahenci/{cid}#pliki")


@app.post("/kontrahenci/{cid}/sms")
async def contractor_sms(cid: int, request: Request, db: Session = Depends(get_db)):
    c = _own(db, request, Contractor, cid)
    if c is None:
        raise HTTPException(404)
    form = await request.form()
    try:
        x = sms_mod.create(db, web_actor(request), c, form.get("phone") or "", form.get("body") or "")
        db.flush()
        audit(db, web_actor(request), "sms.create", f"SMS do {c.name} ({x.phone}): {x.body[:120]}",
              company_id=c.company_id, entity="contractor", entity_id=c.id)
        db.commit()
        flash(request, "SMS zapisany w historii. Wyśle się po podłączeniu bramki SMS." if x.status == "waiting"
              else "Wysłano SMS.")
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
    return redirect(f"/kontrahenci/{c.id}#sms")


@app.post("/kontrahenci/{cid}/ksef-auto")
async def contractor_ksef_toggle(cid: int, request: Request, db: Session = Depends(get_db)):
    c = _own(db, request, Contractor, cid)
    if c is None:
        raise HTTPException(404)
    form = await request.form()
    c.ksef_auto = not c.ksef_auto
    audit(db, web_actor(request), "contractor.ksef_auto",
          f"{c.name}: automatyczna wysyłka do KSeF {'włączona' if c.ksef_auto else 'wyłączona'}", entity="contractor",
          entity_id=c.id)
    db.commit()
    back = form.get("back") or "/kontrahenci"
    return redirect(back if back.startswith("/kontrahenci") else "/kontrahenci")


@app.post("/kontrahenci/{cid}/notatki")
async def contractor_note_add(cid: int, request: Request, db: Session = Depends(get_db)):
    c = _own(db, request, Contractor, cid)
    if c is None:
        raise HTTPException(404)
    form = await request.form()
    text = (form.get("text") or "").strip()
    if text:
        db.add(ContractorNote(contractor_id=c.id, text=text[:20000], author=web_actor(request)[:100],
                              at=dt.datetime.now().replace(microsecond=0)))
        db.commit()
        flash(request, "Dodano notatkę.")
    return redirect(f"/kontrahenci/{cid}#notatki")


@app.post("/kontrahenci/{cid}/notatki/{nid}/usun")
def contractor_note_delete(cid: int, nid: int, request: Request, db: Session = Depends(get_db)):
    n = db.get(ContractorNote, nid)
    if n is None or n.contractor_id != cid or _own(db, request, Contractor, cid) is None:
        raise HTTPException(404)
    db.delete(n)
    db.commit()
    flash(request, "Usunięto notatkę.")
    return redirect(f"/kontrahenci/{cid}#notatki")


@app.post("/kontrahenci/{cid}/usun")
def contractor_delete(cid: int, request: Request, db: Session = Depends(get_db)):
    c = _own(db, request, Contractor, cid)
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
    rows = prod.search(db, current_company(request, db).id, q, active_only=pokaz != "wszystkie")
    return render(request, db, "products.html", products=rows, q=q, pokaz=pokaz,
                  suppliers=_last_suppliers(db, [r.id for r in rows]))


def _last_suppliers(db: Session, product_ids: list[int]) -> dict[int, str]:
    """Dostawca produktu = odbiorca/dostawca z ostatniego wystawionego PZ z tym produktem."""
    if not product_ids:
        return {}
    q = (select(DeliveryItem.product_id, Delivery.recipient_name)
         .join(Delivery, Delivery.id == DeliveryItem.delivery_id)
         .where(Delivery.kind == "pz", Delivery.status == "issued", DeliveryItem.product_id.in_(product_ids))
         .order_by(Delivery.issue_date.desc(), Delivery.id.desc()))
    out: dict[int, str] = {}
    for pid, name in db.execute(q):
        out.setdefault(pid, name)
    return out


@app.get("/produkty/nowy", response_class=HTMLResponse)
def product_new(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "product_form.html", product=Product(name="", code="", unit="szt.", vat_rate="23",
                                                              ean="", description="", active=True))


def _get_product(db: Session, pid: int, request: Request) -> Product:
    p = _own(db, request, Product, pid)
    if p is None:
        raise HTTPException(404)
    return p


@app.get("/produkty/{pid}", response_class=HTMLResponse)
def product_edit(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_product(db, pid, request)
    used = db.scalar(select(func.count()).select_from(InvoiceItem).where(InvoiceItem.product_id == pid))
    moves = db.scalars(select(StockMovement).where(StockMovement.product_id == pid)
                       .order_by(StockMovement.date.desc(), StockMovement.id.desc()).limit(50)).all()
    return render(request, db, "product_form.html", product=p, used=used, moves=moves)


async def _save_product(request: Request, db: Session, p: Product):
    form = await request.form()
    is_new = p.id is None
    if p.company_id is None:
        p.company_id = current_company(request, db).id
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
    return await _save_product(request, db, _get_product(db, pid, request))


@app.post("/produkty/{pid}/usun")
def product_delete(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_product(db, pid, request)
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
    p = _get_product(db, pid, request)
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
    p = _get_product(db, pid, request)
    prod.set_main_photo(p, photo_id)
    db.commit()
    return redirect(f"/produkty/{pid}")


@app.get("/zdjecia/{photo_id}")
def product_photo(photo_id: int, request: Request, mini: int = 0, db: Session = Depends(get_db)):
    photo = db.get(ProductPhoto, photo_id)
    if photo is None or _own(db, request, Product, photo.product_id) is None:
        raise HTTPException(404)
    return Response(photo.thumb if mini else photo.data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


# --- ustawienia firmy -----------------------------------------------------

COMPANY_FIELDS = ["short_name", "name", "nip", "street", "postal_code", "city", "bank_name", "bank_account",
                  "email", "phone", "issue_place", "pattern_vat", "pattern_proforma", "pattern_advance", "pattern_wz",
                  "pattern_pz", "pattern_correction", "pattern_offer"]


@app.get("/ustawienia", response_class=HTMLResponse)
def settings_form(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "settings.html", KSEF_ENVS=KSEF_ENVS,
                  pattern_order=numbering.extra_pattern(db, current_company(request, db), "order"))


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


@app.post("/ustawienia/wyglad")
async def settings_look(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    tpl = form.get("invoice_template") or ""
    if tpl in INVOICE_TEMPLATES:
        company.invoice_template = tpl
    f = form.get("logo")
    if getattr(f, "filename", None):
        data = await f.read()
        if len(data) > 5_000_000:
            flash(request, "Logo jest za duże (maks. 5 MB).")
            return redirect("/ustawienia#wyglad")
        try:
            company.logo, company.logo_type = pdf.prepare_logo(data)
        except ValueError as e:
            flash(request, str(e))
            return redirect("/ustawienia#wyglad")
    elif form.get("logo_remove") == "1":
        company.logo, company.logo_type = None, ""
    db.commit()
    flash(request, "Zapisano wygląd faktury.")
    return redirect("/ustawienia#wyglad")


@app.get("/ustawienia/logo")
def settings_logo(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    if not company.logo:
        raise HTTPException(404)
    return Response(company.logo, media_type=company.logo_type or "image/png")


@app.get("/ustawienia/wzor/{tpl}")
def settings_template_preview(tpl: str, request: Request, db: Session = Depends(get_db)):
    """Podglad wzoru na ostatniej fakturze firmy (albo na przykladzie, gdy jeszcze nie ma faktur)."""
    if tpl not in INVOICE_TEMPLATES:
        raise HTTPException(404)
    company = current_company(request, db)
    inv = db.scalar(select(Invoice).where(Invoice.company_id == company.id, Invoice.kind == "vat")
                    .order_by(Invoice.issue_date.desc(), Invoice.id.desc()).limit(1))
    if inv is None:
        today = dt.date.today()
        inv = Invoice(company=company, kind="vat", status="draft", issue_date=today, sale_date=today,
                      issue_place=company.issue_place or company.city, payment_method="transfer",
                      due_date=today + dt.timedelta(days=company.default_payment_days or 7), prices_gross=False,
                      buyer_name="Przykładowy Klient Sp. z o.o.", buyer_nip="1234563218",
                      buyer_address="ul. Przykładowa 1\n00-001 Warszawa", paid=Decimal("0"))
        inv.items = [InvoiceItem(position=1, name="Przykładowa usługa", quantity=Decimal("1"), unit="szt.",
                                 unit_price=Decimal("100.00"), vat_rate="23")]
        calc.recalculate(inv)
    data = pdf.render_invoice(inv, tpl)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="wzor-{tpl}.pdf"'})


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
              company.pattern_pz, company.pattern_offer, form.get("pattern_order") or "{nr}"):
        if "{nr}" not in p and "{NNN}" not in p:
            db.rollback()
            return render(request, db, "settings.html", KSEF_ENVS=KSEF_ENVS, error="Wzór numeru musi zawierać {nr} albo {NNN}.",
                          pattern_order=form.get("pattern_order") or "")
    if form.get("pattern_order"):
        numbering.set_extra_pattern(db, company, "order", form.get("pattern_order"))
    db.commit()
    flash(request, "Zapisano ustawienia firmy.")
    return redirect("/ustawienia")


# --- dziennik i dostep dla Claude -------------------------------------------


@app.get("/dziennik", response_class=HTMLResponse)
def audit_log(request: Request, db: Session = Depends(get_db)):
    entries = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(200)).all()
    return render(request, db, "audit.html", entries=entries)


def _org_tokens(request: Request):
    """Warunek: tokeny organizacji zalogowanej osoby."""
    return ApiToken.organisation_id == request.session.get("org_id")


def _grants(db: Session, request: Request) -> list[dict]:
    """Aktywne polaczenia organizacji: zgody OAuth (po tokenie odswiezania) i klucze API."""
    rows = db.scalars(
        select(ApiToken).where(ApiToken.revoked.is_(False), ApiToken.kind.in_(["refresh", "key"]), _org_tokens(request))
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
    return render(request, db, "access.html", grants=_grants(db, request), scopes=SCOPES, new_key=new_key,
                  mcp_url=auth.base_url(request) + "/mcp")


@app.post("/dostep/klucz")
async def access_new_key(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    scopes = [s for s in form.getlist("scope") if s in SCOPES] or ["read"]
    name = (form.get("name") or "").strip() or "Klucz API"
    request.session["new_key"] = auth.create_api_key(db, name, scopes, request.session.get("org_id"))
    audit(db, web_actor(request), "access.key", f"Nowy klucz API „{name}” ({', '.join(scopes)})")
    db.commit()
    return redirect("/dostep")


@app.post("/dostep/{grant_id}/cofnij")
def access_revoke(grant_id: str, request: Request, db: Session = Depends(get_db)):
    if db.scalar(select(ApiToken.id).where(ApiToken.grant_id == grant_id, _org_tokens(request)).limit(1)) is None:
        raise HTTPException(404)
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
                  open_invoices=bank.open_invoices(db, company.id), conns=bank_connect.connections(db, company.id),
                  bank_warnings=bank_connect.warnings(db, company.id))


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


@app.get("/polityka-prywatnosci", response_class=HTMLResponse)
@app.get("/regulamin", response_class=HTMLResponse)
def legal_page(request: Request, db: Session = Depends(get_db)):
    """Publiczne strony wymagane przy rejestracji aplikacji u posrednika bankowego."""
    operator = db.scalars(select(Company).order_by(Company.id)).first()
    kind = "regulamin" if request.url.path == "/regulamin" else "prywatnosc"
    return templates.TemplateResponse(request, "legal.html", {"kind": kind, "operator": operator})


# --- polaczenie z bankiem (Enable Banking, PSD2)

def _psu_ip(request: Request) -> str:
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "")


def _bank_conn(db: Session, request: Request, cid: int) -> BankConnection:
    company = current_company(request, db)
    conn = db.get(BankConnection, cid)
    if conn is None or conn.company_id != company.id:
        raise HTTPException(404)
    return conn


@app.get("/bank/polaczenie", response_class=HTMLResponse)
def bank_connection_page(request: Request, psu: str = "business", db: Session = Depends(get_db)):
    company = current_company(request, db)
    configured = enablebanking.is_configured(db)
    app_id, _ = enablebanking.get_config(db) if configured else ("", "")
    banks, banks_error = [], ""
    psu = psu if psu in ("business", "personal") else "business"
    if configured:
        try:
            banks = bank_connect.banks(enablebanking.Client.from_db(db), "PL", psu)
        except (enablebanking.BankApiError, ValueError) as e:
            banks_error = str(e)
    return render(request, db, "bank_connection.html", configured=configured, app_id=app_id, banks=banks,
                  banks_error=banks_error, psu=psu, conns=bank_connect.connections(db, company.id),
                  redirect_url=auth.base_url(request) + "/bank/powrot", warnings=bank_connect.warnings(db, company.id))


@app.post("/bank/polaczenie/konfiguracja")
async def bank_connection_config(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    pem = ""
    f = form.get("key_file")
    if getattr(f, "filename", None):
        raw = await f.read()
        pem = raw[:20000].decode("utf-8", "replace")
    pem = pem or (form.get("key_text") or "")
    try:
        enablebanking.save_config(db, form.get("app_id") or "", pem or None)
        audit(db, web_actor(request), "bank.config", "Zapisano dane aplikacji Enable Banking (klucz zaszyfrowany)")
        db.commit()
        flash(request, "Zapisano. Teraz wybierz bank i kliknij „Podłącz”.")
    except enablebanking.BankApiError as e:
        db.rollback()
        flash(request, str(e))
    return redirect("/bank/polaczenie")


@app.post("/bank/polaczenie/nowe")
async def bank_connection_start(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    try:
        url = bank_connect.start(db, web_actor(request), enablebanking.Client.from_db(db), company,
                                 form.get("aspsp") or "", form.get("psu_type") or "business",
                                 auth.base_url(request) + "/bank/powrot", _psu_ip(request))
        db.commit()
    except (enablebanking.BankApiError, ValueError) as e:
        db.rollback()
        flash(request, str(e))
        return redirect("/bank/polaczenie")
    return RedirectResponse(url, status_code=303)


@app.get("/bank/powrot")
def bank_connection_return(request: Request, state: str = "", code: str = "", error: str = "",
                           error_description: str = "", db: Session = Depends(get_db)):
    actor = web_actor(request)
    try:
        client = enablebanking.Client.from_db(db)
        conn = bank_connect.finish(db, actor, client, state, code, error, error_description, _psu_ip(request))
        db.commit()
    except (enablebanking.BankApiError, ValueError) as e:
        db.rollback()
        flash(request, str(e))
        return redirect("/bank/polaczenie")
    if conn.state != "active":
        flash(request, conn.last_error or "Nie udało się podłączyć banku.")
        return redirect("/bank/polaczenie")
    request.session["company_id"] = conn.company_id
    for old in bank_connect.connections(db, conn.company_id):
        if old.id != conn.id and old.aspsp_name == conn.aspsp_name and old.state in ("active", "expired", "error"):
            bank_connect.disconnect(db, actor, None, old)
    msg = f"Podłączono {conn.aspsp_name} ({len(conn.accounts)} rach.). Zgoda ważna do {conn.valid_until:%d.%m.%Y}."
    try:
        res = bank_connect.sync(db, actor, client, conn)
        msg += f" Pobrano {res['added']} operacji, {res['auto']} wpłat zaksięgowanych automatycznie."
    except (enablebanking.BankApiError, ValueError) as e:
        conn.last_error = str(e)[:1000]
        msg += f" Pierwsze pobranie nie udało się: {e}"
    db.commit()
    flash(request, msg)
    return redirect("/bank")


@app.post("/bank/polaczenie/{cid}/pobierz")
def bank_connection_sync(cid: int, request: Request, db: Session = Depends(get_db)):
    conn = _bank_conn(db, request, cid)
    try:
        res = bank_connect.sync(db, web_actor(request), enablebanking.Client.from_db(db), conn)
        db.commit()
        flash(request, f"{conn.aspsp_name}: {res['added']} nowych operacji, {res['auto']} wpłat zaksięgowanych, "
                       f"{res['suggested']} propozycji." if conn.state == "active" else conn.last_error)
    except (enablebanking.BankApiError, ValueError) as e:
        db.rollback()
        conn = db.get(BankConnection, cid)
        conn.last_error = str(e)[:1000]
        db.commit()
        flash(request, str(e))
    return redirect("/bank")


@app.post("/bank/polaczenie/{cid}/odlacz")
def bank_connection_delete(cid: int, request: Request, db: Session = Depends(get_db)):
    conn = _bank_conn(db, request, cid)
    try:
        client = enablebanking.Client.from_db(db)
    except enablebanking.BankApiError:
        client = None
    bank_connect.disconnect(db, web_actor(request), client, conn)
    db.commit()
    flash(request, f"Odłączono {conn.aspsp_name}. Pobrane operacje zostają w Banku.")
    return redirect("/bank/polaczenie")


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
                 przypomnienie: int = 0, szablon: int = 0, oferta: int = 0, kontakt: int = 0,
                 db: Session = Depends(get_db)):
    company = current_company(request, db)
    accounts = [a for a in _company_accounts(db, company.id) if a.active]
    draft = {"to": do, "cc": "", "subject": "", "body": "", "in_reply_to": "", "invoice_id": 0, "account_id": 0,
             "reminder": "", "offer_id": 0}
    inv = offer = None
    tpl_kind, tpls, tpl = "general", [], None
    if faktura:
        inv = get_invoice(db, request, faktura)
        draft["invoice_id"] = inv.id
        draft["to"] = draft["to"] or (inv.contractor.email.split(",")[0].strip() if inv.contractor and inv.contractor.email else "")
        tpl_kind = "invoice"
        if przypomnienie and inv.status == "issued" and inv.to_pay > 0:
            tpl_kind = "reminder"
            draft["reminder"] = "1"
    if oferta:
        offer = _get_offer(db, request, oferta)
        draft["offer_id"] = offer.id
        draft["to"] = draft["to"] or offer.email.split(",")[0].strip() or \
            (offer.contractor.email.split(",")[0].strip() if offer.contractor and offer.contractor.email else "")
        tpl_kind = "offer"
    if faktura or szablon or oferta:
        mail_templates.ensure_defaults(db, company)
        tpls = db.scalars(select(EmailTemplate).where(EmailTemplate.company_id == company.id)
                          .order_by(EmailTemplate.kind, EmailTemplate.is_default.desc(), EmailTemplate.name)).all()
        tpl = next((t for t in tpls if t.id == szablon), None) or mail_templates.default_for(db, company, tpl_kind)
        db.commit()
        if tpl is not None:
            ctx = mail_templates.context(company, inv, offer=offer)
            draft["subject"] = mail_templates.render(tpl.subject, ctx)
            draft["body"] = mail_templates.render(tpl.body, ctx)
    if kontakt and not draft["to"]:
        p = _own(db, request, Contact, kontakt)
        if p and p.email:
            draft["to"] = p.email.split(",")[0].strip()
    if kontrahent and not draft["to"]:
        c = _own(db, request, Contractor, kontrahent)
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
    return render(request, db, "mail_compose.html", accounts=accounts, draft=draft, inv=inv, offer=offer, tpls=tpls,
                  tpl=tpl, query=dict(request.query_params))


@app.post("/poczta/nowa")
async def mail_send(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    accounts = {a.id: a for a in _company_accounts(db, company.id) if a.active}
    draft = {k: (form.get(k) or "") for k in ("to", "cc", "subject", "body", "in_reply_to", "reminder")}
    draft["account_id"] = int(form.get("account_id") or 0)
    draft["invoice_id"] = int(form.get("invoice_id") or 0)
    draft["offer_id"] = int(form.get("offer_id") or 0)
    acc = accounts.get(draft["account_id"])
    inv = get_invoice(db, request, draft["invoice_id"]) if draft["invoice_id"] else None
    offer = _get_offer(db, request, draft["offer_id"]) if draft["offer_id"] else None
    files = []
    if offer is not None and form.get("attach_offer") == "1":
        files.append(((offer.number or f"oferta-{offer.id}").replace("/", "-") + ".pdf", "application/pdf",
                      offers.render_pdf(offer)))
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
                      offer=offer, error="Załączniki są za duże (razem ponad 20 MB).")
    try:
        if acc is None:
            raise MailError("Wybierz skrzynkę, z której wysłać.")
        msg = mail.send(db, acc, draft["to"], draft["subject"], draft["body"], cc=draft["cc"], attachments=files,
                        in_reply_to=draft["in_reply_to"], invoice_id=inv.id if inv else None)
        if inv is not None and draft["reminder"] == "1":
            payments.record_reminder(inv)
        if offer is not None:
            offers.mark_sent(offer)
        audit(db, web_actor(request), "mail.send", f"E-mail z {acc.email} do {draft['to']}: {draft['subject'][:120]}",
              company_id=company.id, entity="invoice" if inv else "mail", entity_id=inv.id if inv else (msg.id if msg else None))
        db.commit()
    except MailError as e:
        db.rollback()
        return render(request, db, "mail_compose.html", accounts=list(accounts.values()), draft=draft, inv=inv,
                      offer=offer, error=str(e))
    flash(request, f"Wysłano {'przypomnienie ' if inv is not None and draft['reminder'] == '1' else ''}do {draft['to']}.")
    back = form.get("back") or ""
    if back.startswith("/kontrahenci/"):
        return redirect(back)
    return redirect(f"/faktury/{inv.id}" if inv else f"/oferty/{offer.id}" if offer else "/poczta?kierunek=out")


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
    products = prod.search(db, current_company(request, db).id, "", active_only=True)
    contractors = _contractors(db, current_company(request, db).id)
    return render(request, db, "wz_form.html", d=d, products=products, products_json=script_json(prod.picker_json(products)),
                  contractors=contractors, error=error)


@app.get("/wz", response_class=HTMLResponse)
def wz_list(request: Request, q: str = "", rodzaj: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    stmt = select(Delivery).where(Delivery.company_id == company.id).options(selectinload(Delivery.invoice))
    if rodzaj in DELIVERY_KINDS:
        stmt = stmt.where(Delivery.kind == rodzaj)
    elif rodzaj == "bez-faktury":
        stmt = stmt.where(Delivery.kind == "wz", Delivery.status == "issued", Delivery.invoice_id.is_(None))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Delivery.number.ilike(like), Delivery.recipient_name.ilike(like)))
    rows = db.scalars(stmt.order_by(Delivery.issue_date.desc(), Delivery.id.desc()).limit(200)).all()
    return render(request, db, "wz_list.html", rows=rows, q=q, rodzaj=rodzaj)


@app.post("/wz/faktura")
async def wz_many_to_invoice(request: Request, db: Session = Depends(get_db)):
    """Jeden szkic faktury z kilku zaznaczonych WZ (ten sam odbiorca, ta sama firma)."""
    company = current_company(request, db)
    form = await request.form()
    ids = {int(x) for x in form.getlist("ids") if str(x).isdigit()}
    docs = db.scalars(select(Delivery).where(Delivery.company_id == company.id, Delivery.id.in_(ids))).all() \
        if ids else []
    back = form.get("back") or "/wz"
    if not back.startswith("/wz"):
        back = "/wz"
    try:
        if len(docs) != len(ids):
            raise ValueError("Nie znaleziono części zaznaczonych WZ.")
        inv = wz.to_invoice_many(db, web_actor(request), list(docs))
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
        return redirect(back)
    audit(db, web_actor(request), "wz.invoice", f"Szkic faktury z {len(docs)} WZ: "
          + ", ".join(d.number for d in docs)[:400], company_id=company.id, entity="invoice", entity_id=inv.id)
    db.commit()
    flash(request, f"Utworzono szkic faktury z {len(docs)} WZ. Sprawdź i wystaw.")
    return redirect(f"/faktury/{inv.id}/edytuj")


@app.get("/wz/nowa", response_class=HTMLResponse)
def wz_new(request: Request, faktura: int = 0, rodzaj: str = "wz", kontrahent: int = 0, db: Session = Depends(get_db)):
    company = current_company(request, db)
    if faktura:
        d = wz.from_invoice(get_invoice(db, request, faktura))
    else:
        d = Delivery(company_id=company.id, kind=rodzaj if rodzaj in DELIVERY_KINDS else "wz", status="draft",
                     issue_date=dt.date.today(), recipient_name="", recipient_address="", notes="", signed_by="",
                     prices_gross=False)
        c = _own(db, request, Contractor, kontrahent) if kontrahent else None
        if c is not None:
            d.contractor_id, d.recipient_name, d.recipient_address = c.id, c.name, "\n".join(c.address_lines)
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
    d.contractor_id = int(cid) if cid.isdigit() and _own(db, request, Contractor, int(cid)) else None
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


# ---------------------------------------------------------------- osoby kontaktowe

CONTACT_FIELDS = ["first_name", "last_name", "position", "email", "phone", "phone2", "notes"]


def _get_contact(db: Session, pid: int, request: Request) -> Contact:
    p = _own(db, request, Contact, pid)
    if p is None:
        raise HTTPException(404)
    return p


@app.get("/kontakty", response_class=HTMLResponse)
def contact_list(request: Request, q: str = "", db: Session = Depends(get_db)):
    stmt = (select(Contact).outerjoin(Contractor, Contact.contractor_id == Contractor.id)
            .where(Contact.company_id == current_company(request, db).id))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Contact.first_name.ilike(like), Contact.last_name.ilike(like), Contact.email.ilike(like),
                              Contact.phone.ilike(like), Contact.position.ilike(like), Contractor.name.ilike(like)))
    rows = db.scalars(stmt.order_by(Contact.last_name, Contact.first_name).limit(500)).all()
    return render(request, db, "contacts.html", rows=rows, q=q)


def _contact_form(request: Request, db: Session, p: Contact, error: str | None = None):
    contractors = _contractors(db, current_company(request, db).id)
    return render(request, db, "contact_form.html", p=p, contractors=contractors, error=error)


@app.get("/kontakty/nowy", response_class=HTMLResponse)
def contact_new(request: Request, kontrahent: int = 0, db: Session = Depends(get_db)):
    return _contact_form(request, db, Contact(contractor_id=kontrahent or None))


async def _contact_save(request: Request, db: Session, p: Contact):
    form = await request.form()
    for f in CONTACT_FIELDS:
        setattr(p, f, (form.get(f) or "").strip())
    p.first_name, p.last_name, p.position = p.first_name[:100], p.last_name[:100], p.position[:150]
    p.email, p.phone, p.phone2 = p.email[:200], p.phone[:40], p.phone2[:40]
    cid = form.get("contractor_id") or ""
    p.contractor_id = int(cid) if cid.isdigit() and _own(db, request, Contractor, int(cid)) else None
    if not (p.first_name or p.last_name or p.email):
        return _contact_form(request, db, p, error="Podaj imię, nazwisko albo e-mail.")
    if p.company_id is None:
        p.company_id = current_company(request, db).id
    db.add(p)
    db.commit()
    flash(request, f"Zapisano: {p.name}.")
    return redirect(f"/kontrahenci/{p.contractor_id}" if form.get("back") == "kontrahent" and p.contractor_id
                    else "/kontakty")


@app.post("/kontakty/nowy")
async def contact_create(request: Request, db: Session = Depends(get_db)):
    return await _contact_save(request, db, Contact())


@app.get("/kontakty/{pid}", response_class=HTMLResponse)
def contact_edit(pid: int, request: Request, db: Session = Depends(get_db)):
    return _contact_form(request, db, _get_contact(db, pid, request))


@app.post("/kontakty/{pid}")
async def contact_update(pid: int, request: Request, db: Session = Depends(get_db)):
    return await _contact_save(request, db, _get_contact(db, pid, request))


@app.post("/kontakty/{pid}/usun")
def contact_delete(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_contact(db, pid, request)
    back = f"/kontrahenci/{p.contractor_id}" if p.contractor_id else "/kontakty"
    db.delete(p)
    db.commit()
    flash(request, f"Usunięto: {p.name}.")
    return redirect(back)


# ---------------------------------------------------------------- Sprzet i serwis

EQUIPMENT_TEXT = {"name": 300, "category": 100, "inventory_no": 100, "serial_no": 150, "barcode": 100,
                  "supplier": 300, "location": 300}
EQUIPMENT_DATES = ("purchase_date", "install_date", "warranty_until", "next_service")


def _get_equipment(db: Session, request: Request, eid: int) -> Equipment:
    e = db.get(Equipment, eid)
    if e is None or e.company_id != current_company(request, db).id:
        raise HTTPException(404)
    return e


@app.get("/sprzet", response_class=HTMLResponse)
def equipment_list(request: Request, q: str = "", widok: str = "", kategoria: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    today = dt.date.today()
    cond = [Equipment.company_id == company.id]
    stmt = select(Equipment).outerjoin(Contractor, Equipment.contractor_id == Contractor.id)
    if q:
        like = f"%{q.strip()}%"
        cond.append(or_(Equipment.name.ilike(like), Equipment.serial_no.ilike(like), Equipment.inventory_no.ilike(like),
                        Equipment.barcode.ilike(like), Equipment.location.ilike(like), Contractor.name.ilike(like),
                        Contractor.nip.ilike(like)))
    if kategoria:
        cond.append(Equipment.category == kategoria)
    if widok == "gwarancje":
        cond += [Equipment.active, Equipment.warranty_until.is_not(None),
                 Equipment.warranty_until <= today + dt.timedelta(days=60)]
    elif widok == "przeglady":
        cond += [Equipment.active, Equipment.next_service.is_not(None),
                 Equipment.next_service <= today + dt.timedelta(days=30)]
    elif widok == "wycofane":
        cond.append(Equipment.active.is_(False))
    else:
        cond.append(Equipment.active)
    order = Equipment.next_service if widok == "przeglady" else (
        Equipment.warranty_until if widok == "gwarancje" else Equipment.name)
    rows = db.scalars(stmt.where(*cond).order_by(order, Equipment.id).limit(500)).all()
    categories = [c for c in db.scalars(select(Equipment.category).where(Equipment.company_id == company.id,
                                                                          Equipment.category != "").distinct()
                                        .order_by(Equipment.category))]
    return render(request, db, "equipment_list.html", rows=rows, q=q, widok=widok, kategoria=kategoria,
                  categories=categories, today=today)


def _equipment_form(request: Request, db: Session, e: Equipment, error: str | None = None):
    company = current_company(request, db)
    contractors = _contractors(db, current_company(request, db).id)
    contacts = db.scalars(select(Contact).where(Contact.contractor_id == e.contractor_id)
                          .order_by(Contact.last_name)).all() if e.contractor_id else []
    categories = [c for c in db.scalars(select(Equipment.category).where(Equipment.company_id == company.id,
                                                                          Equipment.category != "").distinct())]
    products = prod.search(db, current_company(request, db).id, "", active_only=True)
    deliveries = db.scalars(select(Delivery).where(Delivery.company_id == company.id,
                                                   Delivery.contractor_id == e.contractor_id)
                            .order_by(Delivery.issue_date.desc()).limit(30)).all() if e.contractor_id else []
    task_rows = db.scalars(select(Task).where(Task.equipment_id == e.id).order_by(Task.due_at.desc())
                           .limit(30)).all() if e.id else []
    return render(request, db, "equipment_form.html", e=e, task_rows=task_rows, now=dt.datetime.now(), contractors=contractors, contacts=contacts,
                  categories=categories, products=products, deliveries=deliveries, KINDS=SERVICE_KINDS,
                  today=dt.date.today(), error=error)


@app.get("/sprzet/nowy", response_class=HTMLResponse)
def equipment_new(request: Request, kontrahent: int = 0, db: Session = Depends(get_db)):
    company = current_company(request, db)
    e = Equipment(company_id=company.id, name="", category="", inventory_no="", serial_no="", barcode="", supplier="",
                  location="", notes="", active=True, warranty_remind_days=30, service_interval_months=0,
                  contractor_id=kontrahent or None, source_ref="")
    if kontrahent:
        c = _own(db, request, Contractor, kontrahent)
        e.location = ", ".join(c.address_lines) if c else ""
    return _equipment_form(request, db, e)


async def _equipment_save(request: Request, db: Session, e: Equipment):
    form = await request.form()
    for f, n in EQUIPMENT_TEXT.items():
        setattr(e, f, (form.get(f) or "").strip()[:n])
    e.notes = (form.get("notes") or "").strip()
    cid = form.get("contractor_id") or ""
    e.contractor_id = int(cid) if cid.isdigit() and _own(db, request, Contractor, int(cid)) else None
    pid = form.get("contact_id") or ""
    contact = _own(db, request, Contact, int(pid)) if pid.isdigit() else None
    e.contact_id = contact.id if contact and contact.contractor_id == e.contractor_id else None
    label = (form.get("product") or "").strip()
    product = next((p for p in prod.search(db, current_company(request, db).id, label, active_only=False) if p.label == label), None) if label else None
    e.product_id = product.id if product else None
    if product and not e.name:
        e.name = product.name[:300]
    e.active = form.get("active", "1") == "1"
    try:
        for f in EQUIPMENT_DATES:
            setattr(e, f, dt.date.fromisoformat(form[f]) if form.get(f) else None)
        e.price = calc.parse_decimal(form.get("price") or "", None)
        e.warranty_remind_days = max(0, int(form.get("warranty_remind_days") or 30))
        e.service_interval_months = max(0, min(60, int(form.get("service_interval_months") or 0)))
    except ValueError:
        return _equipment_form(request, db, e, "Sprawdź daty i liczby.")
    if not e.name:
        return _equipment_form(request, db, e, "Podaj nazwę urządzenia albo wybierz produkt.")
    if e.service_interval_months and not e.next_service:
        start = e.install_date or e.purchase_date or dt.date.today()
        e.next_service = recurring.add_months(start, e.service_interval_months, start.day)
    is_new = e.id is None
    db.add(e)
    db.flush()
    audit(db, web_actor(request), "equipment.save", f"{'Nowy' if is_new else 'Zmieniony'} sprzęt „{e.name}”",
          company_id=e.company_id, entity="equipment", entity_id=e.id)
    db.commit()
    flash(request, f"Zapisano: {e.name}.")
    return redirect(f"/sprzet/{e.id}")


@app.post("/sprzet/nowy")
async def equipment_create(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    return await _equipment_save(request, db, Equipment(company_id=company.id, source_ref=""))


@app.get("/sprzet/{eid}", response_class=HTMLResponse)
def equipment_edit(eid: int, request: Request, db: Session = Depends(get_db)):
    return _equipment_form(request, db, _get_equipment(db, request, eid))


@app.post("/sprzet/{eid}")
async def equipment_update(eid: int, request: Request, db: Session = Depends(get_db)):
    return await _equipment_save(request, db, _get_equipment(db, request, eid))


@app.post("/sprzet/{eid}/usun")
def equipment_delete(eid: int, request: Request, db: Session = Depends(get_db)):
    e = _get_equipment(db, request, eid)
    audit(db, web_actor(request), "equipment.delete", f"Usunięto sprzęt „{e.name}”", company_id=e.company_id,
          entity="equipment")
    db.delete(e)
    db.commit()
    flash(request, f"Usunięto: {e.name}.")
    return redirect("/sprzet")


@app.post("/sprzet/{eid}/serwis")
async def equipment_service_add(eid: int, request: Request, db: Session = Depends(get_db)):
    e = _get_equipment(db, request, eid)
    form = await request.form()
    try:
        when = dt.date.fromisoformat(form.get("date") or dt.date.today().isoformat())
    except ValueError:
        when = dt.date.today()
    kind = form.get("kind") if form.get("kind") in SERVICE_KINDS else "inne"
    text = (form.get("description") or "").strip()
    did = form.get("delivery_id") or ""
    d = db.get(Delivery, int(did)) if did.isdigit() else None
    if not text and d is None:
        flash(request, "Opisz, co zostało zrobione.")
        return redirect(f"/sprzet/{eid}#serwis")
    db.add(ServiceEntry(equipment_id=e.id, date=when, kind=kind, description=text,
                        author=request.session.get("user", ""),
                        delivery_id=d.id if d and d.company_id == e.company_id else None))
    if kind == "przeglad" and e.service_interval_months:
        e.next_service = recurring.add_months(when, e.service_interval_months, when.day)
    audit(db, web_actor(request), "equipment.service", f"Serwis „{e.name}”: {SERVICE_KINDS[kind]}",
          company_id=e.company_id, entity="equipment", entity_id=e.id)
    db.commit()
    flash(request, "Dodano wpis serwisowy." + (f" Następny przegląd: {e.next_service:%d.%m.%Y}."
                                              if kind == "przeglad" and e.next_service else ""))
    return redirect(f"/sprzet/{eid}#serwis")


@app.post("/sprzet/{eid}/serwis/{sid}/usun")
def equipment_service_delete(eid: int, sid: int, request: Request, db: Session = Depends(get_db)):
    e = _get_equipment(db, request, eid)
    se = db.get(ServiceEntry, sid)
    if se is None or se.equipment_id != e.id:
        raise HTTPException(404)
    db.delete(se)
    db.commit()
    flash(request, "Usunięto wpis serwisowy.")
    return redirect(f"/sprzet/{eid}#serwis")


# ---------------------------------------------------------------- Zadania i kalendarz

def _get_task(db: Session, request: Request, tid: int) -> Task:
    t = db.get(Task, tid)
    if t is None or t.company_id != current_company(request, db).id:
        raise HTTPException(404)
    return t


def _safe_back(back: str, default: str) -> str:
    return back if back.startswith("/") and not back.startswith("//") else default


@app.get("/zadania", response_class=HTMLResponse)
def task_list(request: Request, widok: str = "dzis", q: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    now = dt.datetime.now()
    if widok == "dzis" and not q:
        rows = tasks_mod.today_tasks(db, company.id)
    else:
        cond = [Task.company_id == company.id, Task.parent_id.is_(None)]
        if widok == "zrobione":
            cond.append(Task.status.in_(("done", "cancelled")))
        elif widok != "wszystkie":
            cond.append(Task.status.in_(("todo", "doing")))
        if q:
            like = f"%{q.strip()}%"
            cond.append(or_(Task.title.ilike(like), Task.description.ilike(like), Task.address.ilike(like),
                            Contractor.name.ilike(like)))
        order = (Task.done_at.desc(),) if widok == "zrobione" else (Task.due_at.is_(None), Task.due_at, Task.start_at, Task.id)
        rows = db.scalars(select(Task).outerjoin(Contractor, Task.contractor_id == Contractor.id).where(*cond)
                          .order_by(*order).limit(300)).all()
    counts = dict(dzis=len(tasks_mod.today_tasks(db, company.id)),
                  otwarte=db.scalar(select(func.count()).select_from(Task).where(*tasks_mod.open_cond(company.id),
                                                                                 Task.parent_id.is_(None))))
    return render(request, db, "task_list.html", rows=rows, widok=widok, q=q, now=now, counts=counts,
                  today=dt.date.today())


def _task_form(request: Request, db: Session, t: Task, error: str | None = None):
    company = current_company(request, db)
    contractors = _contractors(db, current_company(request, db).id)
    contacts = db.scalars(select(Contact).where(Contact.contractor_id == t.contractor_id)
                          .order_by(Contact.last_name)).all() if t.contractor_id else []
    equipment = db.scalars(select(Equipment).where(Equipment.company_id == company.id, Equipment.active,
                                                   Equipment.contractor_id == t.contractor_id)
                           .order_by(Equipment.name)).all() if t.contractor_id else []
    if t.equipment_id and all(e.id != t.equipment_id for e in equipment) and t.equipment:
        equipment = [t.equipment, *equipment]
    categories = [c for c in db.scalars(select(Task.category).where(Task.company_id == company.id, Task.category != "")
                                        .distinct())]
    return render(request, db, "task_form.html", t=t, contractors=contractors, contacts=contacts, equipment=equipment,
                  categories=categories, STATUSES=TASK_STATUSES, PRIORITIES=TASK_PRIORITIES, now=dt.datetime.now(),
                  fmt_minutes=tasks_mod.fmt_minutes, error=error)


@app.get("/zadania/nowe", response_class=HTMLResponse)
def task_new(request: Request, kontrahent: int = 0, sprzet: int = 0, data: str = "", nadrzedne: int = 0,
             db: Session = Depends(get_db)):
    company = current_company(request, db)
    t = Task(company_id=company.id, title="", description="", status="todo", priority="normal", category="",
             address="", all_day=True, assignee=request.session.get("user", ""), minutes_spent=0, source_ref="")
    e = db.get(Equipment, sprzet) if sprzet else None
    if e is not None and e.company_id == company.id:
        t.equipment_id, t.contractor_id, t.address = e.id, e.contractor_id, e.location
        t.title, t.category = f"Serwis: {e.name}"[:300], "serwis"
    elif kontrahent and _own(db, request, Contractor, kontrahent):
        c = _own(db, request, Contractor, kontrahent)
        t.contractor_id, t.address = c.id, ", ".join(c.address_lines)
    parent = db.get(Task, nadrzedne) if nadrzedne else None
    if parent is not None and parent.company_id == company.id:
        t.parent_id, t.contractor_id, t.equipment_id = parent.id, parent.contractor_id, parent.equipment_id
    try:
        t.due_at = dt.datetime.combine(dt.date.fromisoformat(data), dt.time(0, 0)) if data else None
    except ValueError:
        t.due_at = None
    return _task_form(request, db, t)


async def _task_save(request: Request, db: Session, t: Task):
    form = await request.form()
    try:
        tasks_mod.apply_form(db, t, form)
    except ValueError as e:
        return _task_form(request, db, t, str(e))
    is_new = t.id is None
    db.add(t)
    db.flush()
    audit(db, web_actor(request), "task.save", f"{'Nowe' if is_new else 'Zmienione'} zadanie „{t.title}”",
          company_id=t.company_id, entity="task", entity_id=t.id)
    db.commit()
    flash(request, f"Zapisano zadanie: {t.title}.")
    return redirect(_safe_back(form.get("back") or "", f"/zadania/{t.id}"))


@app.post("/zadania/nowe")
async def task_create(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    pid = form.get("parent_id") or ""
    parent = db.get(Task, int(pid)) if pid.isdigit() else None
    t = Task(company_id=company.id, parent_id=parent.id if parent and parent.company_id == company.id else None,
             status="todo", minutes_spent=0, source_ref="", assignee=request.session.get("user", ""))
    return await _task_save(request, db, t)


@app.post("/zadania/szybkie")
async def task_quick(request: Request, db: Session = Depends(get_db)):
    """Szybkie dodanie z listy, kalendarza albo jako podzadanie: sam tytul i ewentualnie dzien."""
    company = current_company(request, db)
    form = await request.form()
    back = _safe_back(form.get("back") or "", "/zadania")
    title = (form.get("title") or "").strip()[:300]
    if not title:
        flash(request, "Wpisz, co jest do zrobienia.")
        return redirect(back)
    t = Task(company_id=company.id, title=title, description="", status="todo", priority="normal", category="",
             address="", all_day=True, assignee=request.session.get("user", ""), minutes_spent=0, source_ref="")
    pid = form.get("parent_id") or ""
    parent = db.get(Task, int(pid)) if pid.isdigit() else None
    if parent is not None and parent.company_id == company.id:
        t.parent_id, t.contractor_id, t.equipment_id = parent.id, parent.contractor_id, parent.equipment_id
    try:
        if form.get("due_date"):
            t.due_at = dt.datetime.combine(dt.date.fromisoformat(form["due_date"]), dt.time(0, 0))
    except ValueError:
        pass
    db.add(t)
    db.commit()
    flash(request, f"Dodano: {t.title}.")
    return redirect(back)


@app.get("/zadania/{tid}", response_class=HTMLResponse)
def task_edit(tid: int, request: Request, db: Session = Depends(get_db)):
    return _task_form(request, db, _get_task(db, request, tid))


@app.post("/zadania/{tid}")
async def task_update(tid: int, request: Request, db: Session = Depends(get_db)):
    return await _task_save(request, db, _get_task(db, request, tid))


@app.post("/zadania/{tid}/status")
async def task_status(tid: int, request: Request, db: Session = Depends(get_db)):
    t = _get_task(db, request, tid)
    form = await request.form()
    status = form.get("status") if form.get("status") in TASK_STATUSES else "done"
    tasks_mod.set_status(t, status)
    audit(db, web_actor(request), "task.status", f"Zadanie „{t.title}”: {t.status_label}",
          company_id=t.company_id, entity="task", entity_id=t.id)
    db.commit()
    return redirect(_safe_back(form.get("back") or "", f"/zadania/{t.id}"))


@app.post("/zadania/{tid}/licznik")
async def task_timer(tid: int, request: Request, db: Session = Depends(get_db)):
    t = _get_task(db, request, tid)
    form = await request.form()
    if t.timer_started_at:
        tasks_mod.stop_timer(t)
        flash(request, f"Zatrzymano. Razem: {tasks_mod.fmt_minutes(t.minutes_spent)}.")
    else:
        tasks_mod.start_timer(t)
        flash(request, "Liczę czas pracy.")
    db.commit()
    return redirect(_safe_back(form.get("back") or "", f"/zadania/{t.id}"))


@app.post("/zadania/{tid}/usun")
def task_delete(tid: int, request: Request, db: Session = Depends(get_db)):
    t = _get_task(db, request, tid)
    back = f"/zadania/{t.parent_id}" if t.parent_id else "/zadania"
    audit(db, web_actor(request), "task.delete", f"Usunięto zadanie „{t.title}”", company_id=t.company_id,
          entity="task")
    db.delete(t)
    db.commit()
    flash(request, f"Usunięto: {t.title}.")
    return redirect(back)


@app.get("/kalendarz", response_class=HTMLResponse)
def task_calendar(request: Request, widok: str = "miesiac", dzien: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    try:
        day = dt.date.fromisoformat(dzien) if dzien else dt.date.today()
    except ValueError:
        day = dt.date.today()
    cal = tasks_mod.calendar_view(db, company.id, widok, day)
    return render(request, db, "calendar.html", cal=cal, today=dt.date.today(), now=dt.datetime.now())


# ---------------------------------------------------------------- Oferty

def _get_offer(db: Session, request: Request, oid: int) -> Offer:
    o = db.get(Offer, oid)
    if o is None or o.company_id != current_company(request, db).id:
        raise HTTPException(404)
    return o


def _offer_form(request: Request, db: Session, o: Offer, error: str | None = None):
    products = prod.search(db, current_company(request, db).id, "", active_only=True)
    contractors = _contractors(db, current_company(request, db).id)
    return render(request, db, "offer_form.html", o=o, products=products,
                  STATUSES=ost.visible(db, current_company(request, db).id, o.status),
                  products_json=script_json(prod.picker_json(products)), contractors=contractors, error=error)


STATUS_KINDS = {"statusy-ofert": "offer", "statusy-umow": "contract", "statusy-zamowien": "order"}


@app.get("/ustawienia/statusy-{what}", response_class=HTMLResponse)
def offer_statuses_page(what: str, request: Request, db: Session = Depends(get_db), error: str | None = None):
    slug = f"statusy-{what}"
    kind = STATUS_KINDS.get(slug)
    if kind is None:
        raise HTTPException(404)
    company = current_company(request, db)
    return render(request, db, "offer_statuses.html", company=company, rows=ost.config(db, company.id, kind),
                  used=ost.usage(db, company.id, kind), GROUPS=ost.KINDS[kind]["groups"], COLORS=ost.COLORS,
                  error=error, kind=kind, K=ost.KINDS[kind])


@app.post("/ustawienia/statusy-{what}")
async def offer_statuses_save(what: str, request: Request, db: Session = Depends(get_db)):
    slug = f"statusy-{what}"
    kind = STATUS_KINDS.get(slug)
    if kind is None:
        raise HTTPException(404)
    company = current_company(request, db)
    form = await request.form()
    try:
        if form.get("reset"):
            ost.reset(db, company.id, kind)
        else:
            cols = {f: form.getlist(f) for f in ("k", "label", "group", "color", "hidden", "delete")}
            rows = [{"k": cols["k"][i], "label": cols["label"][i] if i < len(cols["label"]) else "",
                     "group": cols["group"][i] if i < len(cols["group"]) else "open",
                     "color": cols["color"][i] if i < len(cols["color"]) else "",
                     "hidden": (cols["hidden"][i] if i < len(cols["hidden"]) else "0") == "1",
                     "delete": (cols["delete"][i] if i < len(cols["delete"]) else "0") == "1"}
                    for i in range(len(cols["k"]))]
            ost.save(db, company.id, rows, kind)
    except ValueError as e:
        db.rollback()
        return offer_statuses_page(what, request, db, error=str(e))
    audit(db, web_actor(request), f"{kind}.statuses", f"Zmieniono: {ost.KINDS[kind]['title']}", company_id=company.id)
    db.commit()
    flash(request, f"Zapisano: {ost.KINDS[kind]['title'].lower()}.")
    return redirect(f"/ustawienia/{slug}")


@app.get("/oferty", response_class=HTMLResponse)
def offer_list(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    stmt = select(Offer).where(Offer.company_id == company.id)
    all_st = {r["k"]: r["label"] for r in ost.config(db, company.id)}
    if status == "otwarte":
        stmt = stmt.where(Offer.status.in_(ost.keys(db, company.id, "open")))
    elif status in all_st:
        stmt = stmt.where(Offer.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Offer.number.ilike(like), Offer.recipient_name.ilike(like), Offer.title.ilike(like)))
    rows = db.scalars(stmt.order_by(Offer.issue_date.desc(), Offer.id.desc()).limit(300)).all()
    open_rows = [o for o in rows if o.is_open]
    stats = dict(open_count=len(open_rows), open_gross=sum((o.total_gross for o in open_rows), Decimal("0")))
    return render(request, db, "offer_list.html", rows=rows, q=q, status=status, STATUSES=ost.visible(db, company.id),
                  ALL_STATUSES=all_st, stats=stats,
                  offer_templates=offers.templates(db, company.id))


@app.get("/oferty/nowa", response_class=HTMLResponse)
def offer_new(request: Request, kontrahent: int = 0, kopia: int = 0, wzor: int = 0, db: Session = Depends(get_db)):
    company = current_company(request, db)
    t = db.get(OfferTemplate, wzor) if wzor else None
    if kopia:
        o = offers.duplicate(_get_offer(db, request, kopia), web_actor(request))
    else:
        o = offers.from_template(t, web_actor(request)) if t and t.company_id == company.id \
            else offers.new(company.id, web_actor(request))
        c = _own(db, request, Contractor, kontrahent) if kontrahent else None
        if c is not None:
            o.contractor_id, o.recipient_name = c.id, c.name
            o.recipient_address = "\n".join(c.address_lines)
            o.email = (c.email or "").split(",")[0].strip()
    return _offer_form(request, db, o)


@app.post("/oferty/{oid}/wzor")
async def offer_save_template(oid: int, request: Request, db: Session = Depends(get_db)):
    o = _get_offer(db, request, oid)
    form = await request.form()
    try:
        t = offers.save_template(db, o, form.get("name") or "")
    except ValueError as e:
        flash(request, str(e))
        return redirect(f"/oferty/{oid}")
    flash(request, f"Zapisano wzór „{t.name}”. Nową ofertę z niego zaczniesz w Ofertach → Nowa ze wzoru.")
    return redirect(f"/oferty/{oid}")


@app.get("/oferty/wzory", response_class=HTMLResponse)
def offer_templates(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    rows = offers.templates(db, company.id)
    counts = {t.id: len(json.loads(t.items_json or "[]")) for t in rows}
    return render(request, db, "offer_templates.html", rows=rows, counts=counts)


@app.post("/oferty/wzory/{tid}/usun")
def offer_template_delete(tid: int, request: Request, db: Session = Depends(get_db)):
    t = db.get(OfferTemplate, tid)
    if t is None or t.company_id != current_company(request, db).id:
        raise HTTPException(404)
    db.delete(t)
    db.commit()
    flash(request, f"Usunięto wzór „{t.name}”.")
    return redirect("/oferty/wzory")


async def _offer_save(request: Request, db: Session, o: Offer):
    form = await request.form()
    o.title = (form.get("title") or "").strip()[:300]
    o.heading = (form.get("heading") or "").strip()[:300]
    o.recipient_name = (form.get("recipient_name") or "").strip()[:300]
    o.recipient_address = (form.get("recipient_address") or "").strip()
    o.contact_person = (form.get("contact_person") or "").strip()[:200]
    o.email = (form.get("email") or "").strip()[:200]
    o.notes = (form.get("notes") or "").strip()
    o.internal_notes = (form.get("internal_notes") or "").strip()
    o.prices_gross = form.get("prices_gross") == "1"
    cid = form.get("contractor_id") or ""
    o.contractor_id = int(cid) if cid.isdigit() and _own(db, request, Contractor, int(cid)) else None
    prob = (form.get("probability") or "").strip().rstrip("%")
    error = None
    try:
        o.probability = int(prob) if prob else None
    except ValueError:
        error = "Szansa to liczba od 0 do 100."
    try:
        o.issue_date = dt.date.fromisoformat(form.get("issue_date") or "")
    except ValueError:
        o.issue_date = dt.date.today()
    try:
        o.valid_until = dt.date.fromisoformat(form.get("valid_until") or "")
    except ValueError:
        o.valid_until = None
    if ost.find(db, o.company_id, form.get("status") or "") is not None:
        o.status = form.get("status")
    try:
        if error:
            raise ValueError(error)
        offers.set_items(db, o, form.getlist("item_name"), form.getlist("item_qty"), form.getlist("item_unit"),
                         form.getlist("item_product"), form.getlist("item_price"), form.getlist("item_rate"))
        is_new = o.id is None
        db.add(o)
        offers.save(db, o)
        if is_new:
            audit(db, web_actor(request), "offer.create", f"Oferta {o.number} dla {o.recipient_name}",
                  company_id=o.company_id, entity="offer", entity_id=o.id)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _offer_form(request, db, o, error=str(e))
    flash(request, f"Zapisano ofertę {o.number}.")
    return redirect(f"/oferty/{o.id}")


@app.post("/oferty/nowa")
async def offer_create(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    return await _offer_save(request, db, offers.new(company.id, web_actor(request)))


@app.get("/oferty/{oid}", response_class=HTMLResponse)
def offer_view(oid: int, request: Request, db: Session = Depends(get_db)):
    o = _get_offer(db, request, oid)
    return render(request, db, "offer_view.html", o=o, STATUSES=ost.visible(db, o.company_id, o.status))


@app.get("/oferty/{oid}/edytuj", response_class=HTMLResponse)
def offer_edit(oid: int, request: Request, db: Session = Depends(get_db)):
    return _offer_form(request, db, _get_offer(db, request, oid))


@app.post("/oferty/{oid}/edytuj")
async def offer_update(oid: int, request: Request, db: Session = Depends(get_db)):
    return await _offer_save(request, db, _get_offer(db, request, oid))


@app.post("/oferty/{oid}/status")
async def offer_status(oid: int, request: Request, db: Session = Depends(get_db)):
    o = _get_offer(db, request, oid)
    form = await request.form()
    try:
        offers.set_status(o, form.get("status") or "")
    except ValueError as e:
        flash(request, str(e))
        return redirect(f"/oferty/{o.id}")
    audit(db, web_actor(request), "offer.status", f"{o.number}: status {o.status_label}",
          company_id=o.company_id, entity="offer", entity_id=o.id)
    db.commit()
    back = form.get("back") or ""
    if back.startswith("/oferty"):
        return redirect(back)  # zmiana prosto z listy
    flash(request, f"Status oferty: {o.status_label}.")
    return redirect(f"/oferty/{o.id}")


@app.post("/oferty/{oid}/tytul")
async def offer_title(oid: int, request: Request, db: Session = Depends(get_db)):
    """Tytul oferty edytowany prosto z listy (informacja dla zespolu)."""
    o = _get_offer(db, request, oid)
    form = await request.form()
    title = (form.get("title") or "").strip()[:300]
    if title != o.title:
        o.title = title
        audit(db, web_actor(request), "offer.title", f"{o.number}: tytuł „{title}”",
              company_id=o.company_id, entity="offer", entity_id=o.id)
        db.commit()
    back = form.get("back") or "/oferty"
    return redirect(back if back.startswith("/oferty") else "/oferty")


@app.post("/oferty/{oid}/faktura")
def offer_to_invoice(oid: int, request: Request, db: Session = Depends(get_db)):
    o = _get_offer(db, request, oid)
    try:
        inv = offers.to_invoice(db, web_actor(request), o)
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
        return redirect(f"/oferty/{o.id}")
    flash(request, f"Utworzono szkic faktury z oferty {o.number}. Sprawdź i wystaw.")
    return redirect(f"/faktury/{inv.id}/edytuj")


@app.post("/oferty/{oid}/wz")
def offer_to_wz(oid: int, request: Request, db: Session = Depends(get_db)):
    o = _get_offer(db, request, oid)
    try:
        d = offers.to_wz(db, o)
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
        return redirect(f"/oferty/{o.id}")
    flash(request, f"Utworzono szkic WZ z oferty {o.number}. Sprawdź i wystaw.")
    return redirect(f"/wz/{d.id}/edytuj")


@app.get("/oferty/{oid}/pdf")
def offer_pdf(oid: int, request: Request, db: Session = Depends(get_db), pobierz: int = 0, wyglad: str = ""):
    o = _get_offer(db, request, oid)
    filename = (o.number or f"oferta-{o.id}").replace("/", "-") + ".pdf"
    disposition = "attachment" if pobierz else "inline"
    return Response(offers.render_pdf(o, wyglad or None), media_type="application/pdf",
                    headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})


@app.post("/oferty/{oid}/wyglad")
async def offer_set_look(oid: int, request: Request, db: Session = Depends(get_db)):
    o = _get_offer(db, request, oid)
    tpl = (await request.form()).get("pdf_template") or ""
    if tpl in OFFER_PDF_TEMPLATES:
        o.pdf_template = tpl
        db.commit()
    return redirect(f"/oferty/{oid}")


@app.post("/oferty/{oid}/usun")
def offer_delete(oid: int, request: Request, db: Session = Depends(get_db)):
    o = _get_offer(db, request, oid)
    if o.invoice_id or o.delivery_id:
        flash(request, "Z tej oferty jest już faktura albo WZ. Zmień jej status (np. odrzucona), zamiast ją usuwać.")
        return redirect(f"/oferty/{o.id}")
    audit(db, web_actor(request), "offer.delete", f"Usunięto ofertę {o.number}", company_id=o.company_id,
          entity="offer", entity_id=o.id)
    db.delete(o)
    db.commit()
    flash(request, f"Usunięto ofertę {o.number}.")
    return redirect("/oferty")


# ---------------------------------------------------------------- zamowienia

def _get_order(db: Session, request: Request, zid: int) -> Order:
    z = db.get(Order, zid)
    if z is None or z.company_id != current_company(request, db).id:
        raise HTTPException(404)
    return z


def _order_form(request: Request, db: Session, z: Order, error: str | None = None):
    company = current_company(request, db)
    products = prod.search(db, company.id, "", active_only=True)
    return render(request, db, "order_form.html", z=z, products=products,
                  STATUSES=ost.visible(db, company.id, z.status, kind="order"), DIRECTIONS=ORDER_DIRECTIONS,
                  products_json=script_json(prod.picker_json(products)), contractors=_contractors(db, company.id),
                  error=error)


@app.get("/zamowienia", response_class=HTMLResponse)
def order_list(request: Request, q: str = "", status: str = "", kierunek: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    stmt = select(Order).where(Order.company_id == company.id)
    all_st = {r["k"]: r["label"] for r in ost.config(db, company.id, "order")}
    open_keys = ost.keys(db, company.id, "open", "order")
    if status == "otwarte":
        stmt = stmt.where(Order.status.in_(open_keys))
    elif status in all_st:
        stmt = stmt.where(Order.status == status)
    if kierunek in ORDER_DIRECTIONS:
        stmt = stmt.where(Order.direction == kierunek)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Order.number.ilike(like), Order.recipient_name.ilike(like), Order.title.ilike(like),
                              Order.customer_ref.ilike(like)))
    rows = db.scalars(stmt.order_by(Order.issue_date.desc(), Order.id.desc()).limit(300)).all()
    opened = db.scalars(select(Order).where(Order.company_id == company.id, Order.status.in_(open_keys))).all()
    stats = dict(open_count=len(opened), open_gross=sum((z.total_gross for z in opened), Decimal("0")),
                 overdue=sum(1 for z in opened if z.overdue))
    return render(request, db, "order_list.html", rows=rows, q=q, status=status, kierunek=kierunek,
                  STATUSES=ost.visible(db, company.id, kind="order"), ALL_STATUSES=all_st, DIRECTIONS=ORDER_DIRECTIONS,
                  stats=stats)


@app.get("/zamowienia/nowe", response_class=HTMLResponse)
def order_new(request: Request, kontrahent: int = 0, kopia: int = 0, oferta: int = 0, kierunek: str = "",
              db: Session = Depends(get_db)):
    company = current_company(request, db)
    if kopia:
        z = orders.duplicate(_get_order(db, request, kopia), web_actor(request))
    elif oferta:
        z = orders.from_offer(_get_offer(db, request, oferta), web_actor(request))
    else:
        z = orders.new(company.id, web_actor(request))
        if kierunek in ORDER_DIRECTIONS:
            z.direction = kierunek
        c = _own(db, request, Contractor, kontrahent) if kontrahent else None
        if c is not None:
            z.contractor_id, z.recipient_name = c.id, c.name
            z.recipient_address = "\n".join(c.address_lines)
            z.email = (c.email or "").split(",")[0].strip()
            z.phone = (c.phone or "")[:40]
    return _order_form(request, db, z)


async def _order_save(request: Request, db: Session, z: Order):
    form = await request.form()
    for f, n in (("title", 300), ("recipient_name", 300), ("contact_person", 200), ("email", 200), ("phone", 40),
                 ("customer_ref", 100)):
        setattr(z, f, (form.get(f) or "").strip()[:n])
    z.recipient_address = (form.get("recipient_address") or "").strip()
    z.notes = (form.get("notes") or "").strip()
    z.internal_notes = (form.get("internal_notes") or "").strip()
    z.prices_gross = form.get("prices_gross") == "1"
    if form.get("direction") in ORDER_DIRECTIONS:
        z.direction = form.get("direction")
    cid = form.get("contractor_id") or ""
    z.contractor_id = int(cid) if cid.isdigit() and _own(db, request, Contractor, int(cid)) else None
    oid = form.get("offer_id") or ""
    if oid.isdigit() and z.offer_id is None:
        o = db.get(Offer, int(oid))
        z.offer_id = o.id if o is not None and o.company_id == z.company_id else None
    try:
        z.issue_date = dt.date.fromisoformat(form.get("issue_date") or "")
    except ValueError:
        z.issue_date = dt.date.today()
    try:
        z.due_date = dt.date.fromisoformat(form.get("due_date") or "")
    except ValueError:
        z.due_date = None
    if ost.find(db, z.company_id, form.get("status") or "", "order") is not None:
        z.status = form.get("status")
    try:
        orders.set_items(db, z, form.getlist("item_name"), form.getlist("item_qty"), form.getlist("item_unit"),
                         form.getlist("item_product"), form.getlist("item_price"), form.getlist("item_rate"))
        is_new = z.id is None
        db.add(z)
        orders.save(db, z)
        if is_new:
            audit(db, web_actor(request), "order.create", f"Zamówienie {z.number} ({z.recipient_name})",
                  company_id=z.company_id, entity="order", entity_id=z.id)
            if z.offer_id:
                o = db.get(Offer, z.offer_id)
                if o is not None and o.is_open and ost.find(db, o.company_id, "ordered") is not None:
                    o.status = "ordered"
        db.commit()
    except ValueError as e:
        db.rollback()
        return _order_form(request, db, z, error=str(e))
    flash(request, f"Zapisano zamówienie {z.number}.")
    return redirect(f"/zamowienia/{z.id}")


@app.post("/zamowienia/nowe")
async def order_create(request: Request, db: Session = Depends(get_db)):
    return await _order_save(request, db, orders.new(current_company(request, db).id, web_actor(request)))


@app.get("/zamowienia/{zid}", response_class=HTMLResponse)
def order_view(zid: int, request: Request, db: Session = Depends(get_db)):
    z = _get_order(db, request, zid)
    return render(request, db, "order_view.html", z=z, STATUSES=ost.visible(db, z.company_id, z.status, kind="order"))


@app.get("/zamowienia/{zid}/edytuj", response_class=HTMLResponse)
def order_edit(zid: int, request: Request, db: Session = Depends(get_db)):
    return _order_form(request, db, _get_order(db, request, zid))


@app.post("/zamowienia/{zid}/edytuj")
async def order_update(zid: int, request: Request, db: Session = Depends(get_db)):
    return await _order_save(request, db, _get_order(db, request, zid))


@app.post("/zamowienia/{zid}/status")
async def order_status(zid: int, request: Request, db: Session = Depends(get_db)):
    z = _get_order(db, request, zid)
    form = await request.form()
    try:
        orders.set_status(z, form.get("status") or "")
    except ValueError as e:
        flash(request, str(e))
        return redirect(f"/zamowienia/{z.id}")
    audit(db, web_actor(request), "order.status", f"{z.number}: status {z.status_label}",
          company_id=z.company_id, entity="order", entity_id=z.id)
    db.commit()
    back = form.get("back") or ""
    if back.startswith("/zamowienia"):
        return redirect(back)
    flash(request, f"Status zamówienia: {z.status_label}.")
    return redirect(f"/zamowienia/{z.id}")


@app.post("/zamowienia/{zid}/tytul")
async def order_title(zid: int, request: Request, db: Session = Depends(get_db)):
    z = _get_order(db, request, zid)
    form = await request.form()
    title = (form.get("title") or "").strip()[:300]
    if title != z.title:
        z.title = title
        db.commit()
    back = form.get("back") or "/zamowienia"
    return redirect(back if back.startswith("/zamowienia") else "/zamowienia")


@app.post("/zamowienia/{zid}/faktura")
def order_to_invoice(zid: int, request: Request, db: Session = Depends(get_db)):
    z = _get_order(db, request, zid)
    try:
        inv = orders.to_invoice(db, web_actor(request), z)
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
        return redirect(f"/zamowienia/{z.id}")
    flash(request, f"Utworzono szkic faktury z zamówienia {z.number}. Sprawdź i wystaw.")
    return redirect(f"/faktury/{inv.id}/edytuj")


@app.post("/zamowienia/{zid}/wz")
def order_to_wz(zid: int, request: Request, db: Session = Depends(get_db)):
    z = _get_order(db, request, zid)
    try:
        d = orders.to_wz(db, z)
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
        return redirect(f"/zamowienia/{z.id}")
    flash(request, f"Utworzono szkic {d.kind.upper()} z zamówienia {z.number}. Sprawdź i wystaw.")
    return redirect(f"/wz/{d.id}/edytuj")


@app.post("/zamowienia/{zid}/usun")
def order_delete(zid: int, request: Request, db: Session = Depends(get_db)):
    z = _get_order(db, request, zid)
    if z.invoice_id or z.delivery_id:
        flash(request, "Z tego zamówienia jest już faktura albo WZ. Zmień jego status (np. anulowane), zamiast usuwać.")
        return redirect(f"/zamowienia/{z.id}")
    audit(db, web_actor(request), "order.delete", f"Usunięto zamówienie {z.number}", company_id=z.company_id,
          entity="order", entity_id=z.id)
    db.delete(z)
    db.commit()
    flash(request, f"Usunięto zamówienie {z.number}.")
    return redirect("/zamowienia")


# ---------------------------------------------------------------- polaczenia

@app.get("/polaczenia", response_class=HTMLResponse)
def call_list(request: Request, q: str = "", kierunek: str = "", wynik: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    stmt = select(PhoneCall).where(PhoneCall.company_id == company.id)
    if kierunek in CALL_DIRECTIONS:
        stmt = stmt.where(PhoneCall.direction == kierunek)
    if wynik in CALL_STATUSES:
        stmt = stmt.where(PhoneCall.status == wynik)
    if q:
        like = f"%{q}%"
        key = calls.digits(q)
        stmt = stmt.outerjoin(Contractor, PhoneCall.contractor_id == Contractor.id).where(or_(
            Contractor.name.ilike(like), PhoneCall.note.ilike(like), PhoneCall.user_label.ilike(like),
            *( [PhoneCall.phone.like(f"%{key[-6:]}%")] if len(key) >= 6 else [])))
    rows = db.scalars(stmt.order_by(PhoneCall.started_at.desc()).limit(300)).all()
    return render(request, db, "call_list.html", rows=rows, q=q, kierunek=kierunek, wynik=wynik,
                  DIRECTIONS=CALL_DIRECTIONS, CALL_DIRECTIONS=CALL_DIRECTIONS, CALL_STATUSES=CALL_STATUSES, contractors=_contractors(db, company.id),
                  now=dt.datetime.now())


@app.post("/polaczenia/nowe")
async def call_add(request: Request, db: Session = Depends(get_db)):
    """Notatka z rozmowy (reczny wpis), z listy polaczen albo z karty klienta."""
    company = current_company(request, db)
    form = await request.form()
    cid = form.get("contractor_id") or ""
    contractor = _own(db, request, Contractor, int(cid)) if cid.isdigit() else None
    try:
        started = dt.datetime.fromisoformat(form["started_at"]) if form.get("started_at") else None
    except ValueError:
        started = None
    try:
        minutes = float((form.get("minutes") or "0").replace(",", "."))
    except ValueError:
        minutes = 0
    back = form.get("back") or "/polaczenia"
    try:
        c = calls.add(db, company.id, direction=form.get("direction") or "out", phone=form.get("phone") or "",
                      note=form.get("note") or "", status=form.get("status") or "answered", started_at=started,
                      seconds=int(minutes * 60), user_label=str(request.session.get("user", ""))[:100],
                      contractor_id=contractor.id if contractor else None)
    except ValueError as e:
        flash(request, str(e))
        return redirect(back if back.startswith(("/polaczenia", "/kontrahenci")) else "/polaczenia")
    db.flush()
    audit(db, web_actor(request), "call.add", f"Notatka z rozmowy ({c.direction_label})", company_id=company.id,
          entity="call", entity_id=c.id)
    db.commit()
    flash(request, "Zapisano rozmowę." + ("" if c.contractor_id else " Nie znalazłem klienta po numerze, możesz go wskazać."))
    return redirect(back if back.startswith(("/polaczenia", "/kontrahenci")) else "/polaczenia")


@app.post("/polaczenia/{call_id}")
async def call_update(call_id: int, request: Request, db: Session = Depends(get_db)):
    """Notatka i klient przy polaczeniu (prosto z listy)."""
    c = db.get(PhoneCall, call_id)
    if c is None or c.company_id != current_company(request, db).id:
        raise HTTPException(404)
    form = await request.form()
    if "note" in form:
        c.note = (form.get("note") or "").strip()
    if "contractor_id" in form:
        cid = form.get("contractor_id") or ""
        contractor = _own(db, request, Contractor, int(cid)) if cid.isdigit() else None
        c.contractor_id = contractor.id if contractor else None
    if "done" in form:
        c.done = form.get("done") == "1"
    db.commit()
    back = form.get("back") or "/polaczenia"
    return redirect(back if back.startswith(("/polaczenia", "/kontrahenci")) else "/polaczenia")


@app.post("/polaczenia/{call_id}/usun")
def call_delete(call_id: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(PhoneCall, call_id)
    if c is None or c.company_id != current_company(request, db).id:
        raise HTTPException(404)
    db.delete(c)
    db.commit()
    flash(request, "Usunięto wpis.")
    return redirect("/polaczenia")


# ---------------------------------------------------------------- import z Firmao

@app.get("/import/firmao", response_class=HTMLResponse)
def firmao_import_page(request: Request, db: Session = Depends(get_db)):
    cid = current_company(request, db).id
    counts = {"contractors": db.scalar(select(func.count(Contractor.id)).where(Contractor.company_id == cid)),
              "contractors_firmao": db.scalar(select(func.count(Contractor.id)).where(
                  Contractor.company_id == cid, Contractor.source_ref.like("firmao:%"))),
              "products": db.scalar(select(func.count(Product.id)).where(Product.company_id == cid)),
              "contacts": db.scalar(select(func.count(Contact.id)).where(Contact.company_id == cid)),
              "products_firmao": db.scalar(select(func.count(Product.id)).where(
                  Product.company_id == cid, Product.source_ref.like("firmao:%")))}
    return render(request, db, "firmao_import.html", counts=counts, result=request.session.pop("firmao_result", None))


@app.post("/import/firmao")
async def firmao_import_run(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    result = {}
    try:
        for field, label, fn in (("customers", "Klienci", firmao_import.import_customers),
                                 ("products", "Produkty", firmao_import.import_products),
                                 ("contacts", "Osoby kontaktowe", firmao_import.import_contacts)):
            f = form.get(field)
            if not getattr(f, "filename", None):
                continue
            rows = firmao_import.load(await f.read())
            result[label] = fn(db, rows, current_company(request, db).id)
        if not result:
            raise ValueError("Wybierz co najmniej jeden plik.")
        audit(db, web_actor(request), "import.firmao",
              "Import z Firmao: " + "; ".join(f"{k}: +{v['added']} nowych, {v['updated']} zaktualizowanych"
                                              for k, v in result.items()))
        db.commit()
    except ValueError as e:
        db.rollback()
        flash(request, str(e))
        return redirect("/import/firmao")
    request.session["firmao_result"] = result
    return redirect("/import/firmao")


# ---------------------------------------------------------------- aplikacja na telefon (PWA)

@app.get("/sw.js")
def service_worker():
    # Minimalny service worker: pozwala zainstalowac Sygnavo jako aplikacje; niczego nie zapisuje offline
    # (dane firmy nie zostaja w pamieci telefonu).
    js = "self.addEventListener('install', e => self.skipWaiting());\n" \
         "self.addEventListener('activate', e => self.clients.claim());\n" \
         "self.addEventListener('fetch', () => {});\n"
    return Response(js, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- Raporty (sprzedaz i koszty dla ksiegowej)


@app.get("/raporty")
def reports_index():
    return redirect("/raporty/sprzedaz")


def _report(request: Request, db: Session, kind: str, od: str | None, do: str | None, wg: str, zakres: str, fmt: str):
    company = current_company(request, db)
    start, end = reports.parse_period(od, do)
    if kind == "sprzedaz":
        basis = wg if wg in reports.DATE_BASIS else "issue"
        rep = reports.sales(db, company.id, start, end, basis)
        title, partial = "Raport sprzedaży", "_report_sales.html"
        subtitle = f"Wg: {reports.DATE_BASIS[basis]}. Bez proform i szkiców."
        extra = f"&wg={basis}" if basis != "issue" else ""
    else:
        scope = zakres if zakres in reports.COST_SCOPES else "all"
        rep = reports.costs(db, company.id, start, end, scope)
        title, partial = "Raport kosztów", "_report_costs.html"
        subtitle = f"Faktury kosztowe: {reports.COST_SCOPES[scope]}, wg daty wystawienia. Bez odrzuconych."
        extra = f"&zakres={scope}" if scope != "all" else ""
    q = f"?od={start}&do={end}{extra}"
    fname = f"raport-{kind}-{company.short_name}-{start}-{end}".replace(" ", "_")
    fname = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_") else "_" for ch in fname)
    if fmt == "csv":
        data = reports.sales_csv(rep) if kind == "sprzedaz" else reports.costs_csv(rep)
        return Response(data, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})
    ctx = dict(rep=rep, title=title, subtitle=subtitle, partial=partial, company=company, now=dt.datetime.now(),
               pdf_url=f"/raporty/{kind}/pdf{q}", back_url=f"/raporty/{kind}{q}")
    if fmt in ("druk", "pdf"):
        html = templates.get_template("report_doc.html").render(**ctx, for_pdf=fmt == "pdf", link=False)
        if fmt == "druk":
            return HTMLResponse(html)
        from weasyprint import HTML

        pdf_bytes = HTML(string=html, base_url=str(HERE)).write_pdf()
        return Response(pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{fname}.pdf"'})
    return render(request, db, "report.html", **ctx, kind=kind, link=True, presets=reports.presets(dt.date.today()),
                  BASIS=reports.DATE_BASIS, SCOPES=reports.COST_SCOPES, doc_url=f"/raporty/{kind}/druk{q}",
                  csv_url=f"/raporty/{kind}/csv{q}")


@app.get("/raporty/{kind}", response_class=HTMLResponse)
def report_page(kind: str, request: Request, od: str | None = None, do: str | None = None, wg: str = "",
                zakres: str = "", db: Session = Depends(get_db)):
    if kind not in ("sprzedaz", "koszty"):
        raise HTTPException(404)
    return _report(request, db, kind, od, do, wg, zakres, "html")


@app.get("/raporty/{kind}/{fmt}")
def report_export(kind: str, fmt: str, request: Request, od: str | None = None, do: str | None = None, wg: str = "",
                  zakres: str = "", db: Session = Depends(get_db)):
    if kind not in ("sprzedaz", "koszty") or fmt not in ("druk", "pdf", "csv"):
        raise HTTPException(404)
    return _report(request, db, kind, od, do, wg, zakres, fmt)


# ---------------------------------------------------------------- Koszty (faktury z KSeF)

# Foldery kosztow: klucz w adresie -> (etykieta, warunek). Brak parametru = Do weryfikacji (tu trafiaja nowe).
PURCHASE_FOLDERS = {
    "new": "Do weryfikacji",
    "accepted": "Zweryfikowane",
    "all": "Wszystkie",
    "nieoplacone": "Nieopłacone",
    "rejected": "Odrzucone",
}
PURCHASE_FILE_MAX = 25 * 1024 * 1024


def _get_purchase(db: Session, request: Request, pid: int) -> PurchaseInvoice:
    p = db.get(PurchaseInvoice, pid)
    if p is None or p.company_id != current_company(request, db).id:
        raise HTTPException(404)
    return p


def _purchase_folder_cond(folder: str):
    if folder in PURCHASE_STATUSES:
        return PurchaseInvoice.status == folder
    if folder == "nieoplacone":
        return and_(PurchaseInvoice.paid_at.is_(None), PurchaseInvoice.status != "rejected")
    return None


@app.get("/koszty", response_class=HTMLResponse)
def purchases_list(request: Request, status: str | None = None, q: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    folder = "new" if status is None else (status if status in PURCHASE_FOLDERS else "all")
    base = select(PurchaseInvoice).where(PurchaseInvoice.company_id == company.id)
    stmt = base
    cond = _purchase_folder_cond(folder)
    if cond is not None:
        stmt = stmt.where(cond)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(PurchaseInvoice.invoice_number.ilike(like), PurchaseInvoice.seller_name.ilike(like),
                              PurchaseInvoice.seller_nip.ilike(like), PurchaseInvoice.ksef_number.ilike(like)))
    order = (PurchaseInvoice.issue_date.asc(), PurchaseInvoice.id.asc()) if folder == "new" else \
        (PurchaseInvoice.issue_date.desc(), PurchaseInvoice.id.desc())
    rows = db.scalars(stmt.options(selectinload(PurchaseInvoice.files).defer(PurchaseFile.data))
                      .order_by(*order).limit(300)).all()
    counts = {}
    for key in PURCHASE_FOLDERS:
        c = _purchase_folder_cond(key)
        cstmt = select(func.count()).select_from(PurchaseInvoice).where(PurchaseInvoice.company_id == company.id)
        counts[key] = db.scalar(cstmt.where(c) if c is not None else cstmt)
    unpaid = db.scalars(base.where(_purchase_folder_cond("nieoplacone"))).all()
    return render(request, db, "purchases.html", rows=rows, folder=folder, q=q, FOLDERS=PURCHASE_FOLDERS,
                  counts=counts, unpaid_sum=sum((p.gross for p in unpaid if p.currency == "PLN"), Decimal("0")),
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


async def _purchase_add_files(db: Session, request: Request, p: PurchaseInvoice, files) -> tuple[int, list[str]]:
    added, skipped = 0, []
    for f in files:
        if not getattr(f, "filename", None):
            continue
        raw = await f.read()
        name = os.path.basename(f.filename.replace("\\", "/"))[:200]
        if not raw or len(raw) > PURCHASE_FILE_MAX:
            skipped.append(name)
            continue
        ctype = (f.content_type or "").split(";")[0].strip().lower()[:100] or "application/octet-stream"
        if raw[:4] == b"%PDF":
            ctype = "application/pdf"
        p.files.append(PurchaseFile(company_id=p.company_id, filename=name, content_type=ctype, size=len(raw), data=raw,
                                    added_by=str(request.session.get("user", ""))[:60]))
        added += 1
    return added, skipped


def _form_date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(v) if v else None
    except ValueError:
        return None


@app.get("/koszty/nowa", response_class=HTMLResponse)
def purchase_new_form(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "purchase_form.html", today_iso=dt.date.today().isoformat(), v={}, error="")


@app.post("/koszty/nowa")
async def purchase_new(request: Request, db: Session = Depends(get_db)):
    """Koszt spoza KSeF (paragon, faktura papierowa, zagraniczna): dane recznie + skan. Trafia do weryfikacji."""
    import uuid

    company = current_company(request, db)
    form = await request.form()
    v = {k: (form.get(k) or "").strip() for k in ("invoice_number", "seller_nip", "seller_name", "issue_date", "due_date",
                                                  "net", "vat", "gross", "bank_account", "notes", "currency")}
    net = calc.parse_decimal(v["net"], None)
    vat = calc.parse_decimal(v["vat"], None)
    gross = calc.parse_decimal(v["gross"], None)
    if gross is None and net is not None:
        gross = net + (vat or Decimal("0"))
    if net is None and gross is not None:
        net = gross - (vat or Decimal("0"))
    vat = vat if vat is not None else ((gross or Decimal("0")) - (net or Decimal("0")))
    error = ""
    if not v["invoice_number"] or not v["seller_name"]:
        error = "Podaj numer faktury i sprzedawcę."
    elif gross is None:
        error = "Podaj kwotę brutto albo netto."
    elif not _form_date(v["issue_date"]):
        error = "Podaj datę wystawienia."
    if error:
        return render(request, db, "purchase_form.html", today_iso=dt.date.today().isoformat(), v=v, error=error)
    nip = "".join(ch for ch in v["seller_nip"] if ch.isalnum())[:20]
    contractor = db.scalar(select(Contractor).where(Contractor.company_id == company.id, Contractor.nip == nip)
                           .limit(1)) if nip else None
    p = PurchaseInvoice(company_id=company.id, ksef_env="", ksef_number=f"RECZNA-{uuid.uuid4().hex[:16]}",
                        invoice_number=v["invoice_number"][:256], invoice_type="Vat", issue_date=_form_date(v["issue_date"]),
                        seller_nip=nip, seller_name=v["seller_name"][:512], contractor_id=contractor.id if contractor else None,
                        net=net, vat=vat, gross=gross, currency=(v["currency"] or "PLN").upper()[:3],
                        due_date=_form_date(v["due_date"]), bank_account=v["bank_account"].replace(" ", "")[:40],
                        xml="", status="new", notes=v["notes"])
    db.add(p)
    added, skipped = await _purchase_add_files(db, request, p, form.getlist("files"))
    db.flush()
    audit(db, web_actor(request), "purchase.create",
          f"Koszt dodany ręcznie {p.invoice_number} od {p.seller_name[:80]} ({calc.format_money(p.gross)} {p.currency}), pliki: {added}",
          company_id=p.company_id, entity="purchase", entity_id=p.id)
    db.commit()
    msg = "Dodano koszt. Jest w folderze Do weryfikacji."
    if skipped:
        msg += " Pominięte pliki (puste albo ponad 25 MB): " + ", ".join(skipped)
    flash(request, msg)
    return redirect(f"/koszty/{p.id}")


def _next_to_verify(db: Session, p: PurchaseInvoice) -> PurchaseInvoice | None:
    """Nastepna faktura w folderze Do weryfikacji (najstarsza), zeby weryfikowac jedna po drugiej."""
    return db.scalar(select(PurchaseInvoice).where(PurchaseInvoice.company_id == p.company_id,
                                                   PurchaseInvoice.status == "new", PurchaseInvoice.id != p.id)
                     .order_by(PurchaseInvoice.issue_date.asc(), PurchaseInvoice.id.asc()).limit(1))


@app.get("/koszty/{pid}", response_class=HTMLResponse)
def purchase_view(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_purchase(db, request, pid)
    try:
        data = ksef_incoming.parse(p.xml) if p.xml else {}
    except Exception:  # noqa: BLE001
        data = {}
    files = db.scalars(select(PurchaseFile).options(defer(PurchaseFile.data)).where(PurchaseFile.purchase_id == p.id)
                       .order_by(PurchaseFile.id)).all()
    nxt = _next_to_verify(db, p)
    waiting = db.scalar(select(func.count()).select_from(PurchaseInvoice).where(
        PurchaseInvoice.company_id == p.company_id, PurchaseInvoice.status == "new"))
    return render(request, db, "purchase_view.html", pi=p, data=data, files=files, next_pi=nxt, waiting=waiting,
                  STATUSES=PURCHASE_STATUSES, INLINE=_INLINE_TYPES, today_iso=dt.date.today().isoformat())


@app.post("/koszty/{pid}")
async def purchase_update(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_purchase(db, request, pid)
    form = await request.form()
    action = form.get("action") or ""
    go_next = action == "accepted_next"
    if go_next:
        action = "accepted"
    if action in PURCHASE_STATUSES:
        p.status = action
        if action == "accepted":
            p.verified_at = dt.datetime.now()
            p.verified_by = str(request.session.get("user", ""))[:60]
        elif action == "new":
            p.verified_at, p.verified_by = None, ""
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
    if go_next:
        nxt = _next_to_verify(db, p)
        flash(request, "Zweryfikowano." + ("" if nxt else " To była ostatnia faktura do weryfikacji."))
        return redirect(f"/koszty/{nxt.id}" if nxt else "/koszty?status=accepted")
    flash(request, "Zapisano.")
    return redirect(f"/koszty/{p.id}")


@app.post("/koszty/{pid}/zaplacona")
async def purchase_toggle_paid(pid: int, request: Request, db: Session = Depends(get_db)):
    """Reczne odhaczenie zaplaty z listy kosztow (np. zaplacone prywatnie, bank tego nie pokaze)."""
    p = _get_purchase(db, request, pid)
    form = await request.form()
    p.paid_at = dt.date.today() if form.get("paid") == "1" else None
    audit(db, web_actor(request), "purchase.update",
          f"Koszt {p.invoice_number} od {p.seller_name[:80]}: {'zapłacona (ręcznie)' if p.paid_at else 'cofnięto zapłatę'}",
          company_id=p.company_id, entity="purchase", entity_id=p.id)
    db.commit()
    back = form.get("back") or "/koszty"
    return redirect(back if back.startswith("/koszty") else "/koszty")


@app.get("/koszty/{pid}/xml")
def purchase_xml(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_purchase(db, request, pid)
    if not p.xml:
        raise HTTPException(404, "Ta faktura nie ma pliku XML z KSeF.")
    return Response(p.xml.encode("utf-8"), media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{p.ksef_number}.xml"'})


def _purchase_preview_html(p: PurchaseInvoice, for_pdf: bool = False) -> str:
    try:
        data = ksef_incoming.parse(p.xml) if p.xml else {}
    except Exception:  # noqa: BLE001
        data = {}
    return templates.get_template("purchase_preview.html").render(pi=p, data=data, for_pdf=for_pdf)


@app.get("/koszty/{pid}/podglad", response_class=HTMLResponse)
def purchase_preview(pid: int, request: Request, db: Session = Depends(get_db)):
    """Czytelny podglad faktury z KSeF (wizualizacja XML FA(2)/FA(3)), do weryfikacji i druku."""
    p = _get_purchase(db, request, pid)
    if not p.xml:
        raise HTTPException(404, "Ta faktura nie ma danych z KSeF.")
    return HTMLResponse(_purchase_preview_html(p))


@app.get("/koszty/{pid}/podglad.pdf")
def purchase_preview_pdf(pid: int, request: Request, db: Session = Depends(get_db)):
    from weasyprint import HTML

    p = _get_purchase(db, request, pid)
    if not p.xml:
        raise HTTPException(404, "Ta faktura nie ma danych z KSeF.")
    data = HTML(string=_purchase_preview_html(p, for_pdf=True), base_url=str(Path(__file__).parent)).write_pdf()
    name = (p.invoice_number or p.ksef_number).replace("/", "-")
    safe = "".join(ch if ch.isascii() and ch.isprintable() and ch not in '"\\' else "_" for ch in name) or "koszt"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{safe}.pdf"'})


@app.post("/koszty/{pid}/pliki")
async def purchase_file_add(pid: int, request: Request, db: Session = Depends(get_db)):
    p = _get_purchase(db, request, pid)
    form = await request.form()
    added, skipped = await _purchase_add_files(db, request, p, form.getlist("files"))
    if added:
        audit(db, web_actor(request), "purchase.file_add", f"Koszt {p.invoice_number}: dodano załącznik(i): {added}",
              company_id=p.company_id, entity="purchase", entity_id=p.id)
        db.commit()
    msg = f"Dodano załączników: {added}." if added else "Nie wybrano pliku."
    if skipped:
        msg += " Pominięte (puste albo ponad 25 MB): " + ", ".join(skipped)
    flash(request, msg)
    return redirect(f"/koszty/{pid}")


def _purchase_file(db: Session, request: Request, pid: int, fid: int) -> PurchaseFile:
    p = _get_purchase(db, request, pid)
    f = db.get(PurchaseFile, fid)
    if f is None or f.purchase_id != p.id or f.company_id != p.company_id:
        raise HTTPException(404)
    return f


@app.get("/koszty/{pid}/pliki/{fid}")
def purchase_file_get(pid: int, fid: int, request: Request, pobierz: int = 0, db: Session = Depends(get_db)):
    f = _purchase_file(db, request, pid, fid)
    inline = f.content_type in _INLINE_TYPES and not pobierz
    ctype = f.content_type if f.content_type in _INLINE_TYPES else "application/octet-stream"
    safe = "".join(ch if ch.isascii() and ch.isprintable() and ch not in '"\\' else "_" for ch in f.filename) or "plik"
    disp = f"{'inline' if inline else 'attachment'}; filename=\"{safe}\"; filename*=UTF-8''{quote(f.filename)}"
    headers = {"Content-Disposition": disp, "X-Content-Type-Options": "nosniff"}
    if ctype != "application/pdf":  # przegladarka nie pokaze PDF w ramce z "sandbox"; obrazki i reszta - tak
        headers["Content-Security-Policy"] = "sandbox"
    return Response(f.data, media_type=ctype, headers=headers)


@app.post("/koszty/{pid}/pliki/{fid}/usun")
def purchase_file_delete(pid: int, fid: int, request: Request, db: Session = Depends(get_db)):
    f = _purchase_file(db, request, pid, fid)
    audit(db, web_actor(request), "purchase.file_delete", f"Koszt: usunięto załącznik {f.filename}",
          company_id=f.company_id, entity="purchase", entity_id=pid)
    db.delete(f)
    db.commit()
    flash(request, "Załącznik usunięty.")
    return redirect(f"/koszty/{pid}")


@app.get("/nip/{nip}")
def nip_data(nip: str, request: Request):
    """Dane firmy z bialej listy VAT do uzupelnienia formularza (kontrahent, nabywca)."""
    try:
        return JSONResponse(nip_lookup.lookup(nip))
    except nip_lookup.NipLookupError as e:
        return JSONResponse({"error": str(e)}, status_code=422)


# ---------------------------------------------------------------- Szablony maili

def _get_template(db: Session, request: Request, tid: int) -> EmailTemplate:
    t = db.get(EmailTemplate, tid)
    if t is None or t.company_id != current_company(request, db).id:
        raise HTTPException(404)
    return t


@app.get("/szablony", response_class=HTMLResponse)
def templates_list(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    mail_templates.ensure_defaults(db, company)
    db.commit()
    rows = db.scalars(select(EmailTemplate).where(EmailTemplate.company_id == company.id)
                      .order_by(EmailTemplate.kind, EmailTemplate.is_default.desc(), EmailTemplate.name)).all()
    return render(request, db, "templates_list.html", rows=rows, KINDS=TEMPLATE_KINDS)


def _template_form(request: Request, db: Session, t: EmailTemplate, error: str | None = None):
    company = current_company(request, db)
    sample = mail_templates.sample_context(company)
    return render(request, db, "template_form.html", t=t, KINDS=TEMPLATE_KINDS, PLACEHOLDERS=mail_templates.PLACEHOLDERS,
                  sample_json=script_json(sample), error=error,
                  preview_subject=mail_templates.render(t.subject, sample), preview_body=mail_templates.render(t.body, sample))


@app.get("/szablony/nowy", response_class=HTMLResponse)
def template_new(request: Request, rodzaj: str = "general", db: Session = Depends(get_db)):
    company = current_company(request, db)
    return _template_form(request, db, EmailTemplate(company_id=company.id, kind=rodzaj if rodzaj in TEMPLATE_KINDS else "general",
                                                     name="", subject="", body="", is_default=False))


@app.get("/szablony/{tid}", response_class=HTMLResponse)
def template_edit(tid: int, request: Request, db: Session = Depends(get_db)):
    return _template_form(request, db, _get_template(db, request, tid))


async def _template_save(request: Request, db: Session, t: EmailTemplate):
    form = await request.form()
    t.name = (form.get("name") or "").strip()[:120]
    t.kind = form.get("kind") if form.get("kind") in TEMPLATE_KINDS else "general"
    t.subject = (form.get("subject") or "").strip()[:300]
    t.body = (form.get("body") or "").replace("\r\n", "\n").strip()
    if not t.name:
        return _template_form(request, db, t, "Podaj nazwę szablonu.")
    unknown = mail_templates.unknown_tags(t.subject + " " + t.body)
    if unknown:
        return _template_form(request, db, t, "Nieznane znaczniki: " + ", ".join("{" + u + "}" for u in unknown)
                              + ". Użyj znaczników z listy obok.")
    make_default = form.get("is_default") == "1"
    db.add(t)
    db.flush()
    if make_default or not db.scalar(select(func.count()).select_from(EmailTemplate).where(
            EmailTemplate.company_id == t.company_id, EmailTemplate.kind == t.kind, EmailTemplate.is_default)):
        db.execute(update(EmailTemplate).where(EmailTemplate.company_id == t.company_id, EmailTemplate.kind == t.kind)
                   .values(is_default=False))
        t.is_default = True
    audit(db, web_actor(request), "template.save", f"Szablon maila „{t.name}” ({t.kind_label})",
          company_id=t.company_id, entity="template", entity_id=t.id)
    db.commit()
    flash(request, f"Zapisano szablon „{t.name}”.")
    return redirect("/szablony")


@app.post("/szablony/nowy")
async def template_create(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    return await _template_save(request, db, EmailTemplate(company_id=company.id, is_default=False))


@app.post("/szablony/{tid}")
async def template_update(tid: int, request: Request, db: Session = Depends(get_db)):
    return await _template_save(request, db, _get_template(db, request, tid))


@app.post("/szablony/{tid}/usun")
def template_delete(tid: int, request: Request, db: Session = Depends(get_db)):
    t = _get_template(db, request, tid)
    others = db.scalars(select(EmailTemplate).where(EmailTemplate.company_id == t.company_id, EmailTemplate.kind == t.kind,
                                                    EmailTemplate.id != t.id)).all()
    if not others:
        flash(request, "To jedyny szablon tego rodzaju. Możesz go zmienić, ale nie usunąć.")
        return redirect(f"/szablony/{t.id}")
    if t.is_default:
        others[0].is_default = True
    db.delete(t)
    db.commit()
    flash(request, "Usunięto szablon.")
    return redirect("/szablony")


# ---------------------------------------------------------------- Faktury cykliczne

def _get_recurring(db: Session, request: Request, rid: int) -> RecurringInvoice:
    r = db.get(RecurringInvoice, rid)
    if r is None or r.company_id != current_company(request, db).id:
        raise HTTPException(404)
    return r


@app.get("/umowy", response_class=HTMLResponse)
def recurring_list(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db)):
    company = current_company(request, db)
    base = select(RecurringInvoice).where(RecurringInvoice.company_id == company.id)
    every = db.scalars(base.where(RecurringInvoice.active)).all()
    cond = []
    if status == "zakonczone":
        cond.append(RecurringInvoice.active.is_(False))
    elif status.startswith("s:"):
        cond.append(RecurringInvoice.contract_status == status[2:])
    elif status != "wszystkie":
        cond.append(RecurringInvoice.active)
    if q:
        like = f"%{q}%"
        cond.append(or_(RecurringInvoice.number.ilike(like), RecurringInvoice.name.ilike(like),
                        RecurringInvoice.buyer_name.ilike(like)))
    rows = db.scalars(base.where(*cond).order_by(RecurringInvoice.active.desc(), RecurringInvoice.next_date,
                                                 RecurringInvoice.name)).all()
    monthly = sum((recurring.totals(r)["gross"] / r.interval_months for r in every if r.invoicing), Decimal("0"))
    return render(request, db, "recurring_list.html", rows=rows, totals={r.id: recurring.totals(r) for r in rows},
                  monthly=calc.money(monthly), active_count=len(every), q=q, status=status,
                  CSTATUSES=ost.visible(db, company.id, kind="contract"))


def _recurring_form(request: Request, db: Session, r: RecurringInvoice, error: str | None = None):
    company = current_company(request, db)
    products = prod.search(db, current_company(request, db).id, "", active_only=True)
    contractors = _contractors(db, current_company(request, db).id)
    mail_templates.ensure_defaults(db, company)
    tpls = db.scalars(select(EmailTemplate).where(EmailTemplate.company_id == company.id,
                                                  EmailTemplate.kind.in_(("recurring", "invoice")))).all()
    default_tpl = mail_templates.default_for(db, company, "recurring")
    history = db.scalars(select(Invoice).where(Invoice.recurring_id == r.id).order_by(Invoice.issue_date.desc())
                         .limit(24)).all() if r.id else []
    accounts = _company_accounts(db, company.id)
    has_mail = accounts != []
    files = db.scalars(select(ContractFile).options(defer(ContractFile.data)).where(ContractFile.recurring_id == r.id)
                       .order_by(ContractFile.doc_date.desc().nulls_last(), ContractFile.id.desc())).all() if r.id else []
    return render(request, db, "recurring_form.html", r=r, accounts=accounts, files=files, FILE_KINDS=CONTRACT_FILE_KINDS,
                  default_tpl_id=default_tpl.id if default_tpl else None, rows=recurring.items(r) or [None], products=products,
                  products_json=script_json(prod.picker_json(products)), contractors=contractors, tpls=tpls,
                  INTERVALS=RECURRING_INTERVALS, MODES=RECURRING_MODES, schedule=recurring.schedule(r) if r.next_date else [],
                  totals=recurring.totals(r), history=history, has_mail=has_mail, error=error,
                  CSTATUSES=ost.visible(db, company.id, r.contract_status, kind="contract"),
                  period=recurring.period_ctx(r.next_date or dt.date.today()))


@app.get("/umowy/nowa", response_class=HTMLResponse)
def recurring_new(request: Request, faktura: int = 0, kontrahent: int = 0, db: Session = Depends(get_db)):
    company = current_company(request, db)
    if faktura:
        r = recurring.from_invoice(get_invoice(db, request, faktura))
    else:
        r = RecurringInvoice(company_id=company.id, name="", buyer_name="", buyer_nip="", buyer_address="", items_json="[]",
                             prices_gross=False, payment_method="transfer", due_days=company.default_payment_days,
                             interval_months=1, day_of_month=1, next_date=recurring.first_date(dt.date.today(), 1),
                             mode="send", email_to="", notes="", active=True, last_error="", start_date=dt.date.today())
    r.invoicing, r.number, r.contract_notes = True, "", ""
    c = _own(db, request, Contractor, kontrahent) if kontrahent and not faktura else None
    if c is not None:
        r.contractor_id, r.buyer_name, r.buyer_nip = c.id, c.name, c.nip
        r.buyer_address, r.email_to = "\n".join(c.address_lines), (c.email or "").split(",")[0].strip()
    return _recurring_form(request, db, r)


@app.get("/umowy/{rid}", response_class=HTMLResponse)
def recurring_edit(rid: int, request: Request, db: Session = Depends(get_db)):
    return _recurring_form(request, db, _get_recurring(db, request, rid))


async def _recurring_save(request: Request, db: Session, r: RecurringInvoice):
    form = await request.form()
    r.name = (form.get("name") or "").strip()[:200]
    cid = form.get("contractor_id") or ""
    c = _own(db, request, Contractor, int(cid)) if cid.isdigit() else None
    r.contractor_id = c.id if c else None
    r.buyer_name = (form.get("recipient_name") or (c.name if c else "")).strip()[:300]
    r.buyer_address = (form.get("recipient_address") or "").strip()
    r.buyer_nip = (c.nip if c else (form.get("buyer_nip") or "")).replace("-", "").replace(" ", "")[:20]
    r.prices_gross = form.get("prices_gross") == "1"
    r.payment_method = form.get("payment_method") or "transfer"
    r.mode = form.get("mode") or "draft"
    r.email_to = (form.get("email_to") or "").strip()[:300]
    tid = form.get("template_id") or ""
    r.template_id = int(tid) if tid.isdigit() else None
    r.notes = (form.get("notes") or "").strip()
    r.number = (form.get("number") or "").strip()[:60]
    r.contract_notes = (form.get("contract_notes") or "").strip()
    r.invoicing = form.get("invoicing") == "1"
    st = form.get("contract_status") or ""
    if st and ost.find(db, r.company_id or current_company(request, db).id, st, "contract") is not None:
        r.contract_status = st
    if r.active is None:
        r.active = True
    aid = form.get("mail_account_id") or ""
    r.mail_account_id = int(aid) if aid.isdigit() and any(a.id == int(aid) for a in _company_accounts(db, r.company_id)) \
        else None
    try:
        r.signed_date = dt.date.fromisoformat(form["signed_date"]) if form.get("signed_date") else None
        r.start_date = dt.date.fromisoformat(form["start_date"]) if form.get("start_date") else None
        r.interval_months = int(form.get("interval_months") or 1)
        r.day_of_month = max(0, min(28, int(form.get("day_of_month") or 1)))
        r.due_days = max(0, int(form.get("due_days") or 14))
        start = dt.date.fromisoformat(form.get("next_date") or "")
        r.next_date = recurring.day_in_month(start.year, start.month, r.day_of_month)
        r.end_date = dt.date.fromisoformat(form["end_date"]) if form.get("end_date") else None
        recurring.set_items(db, r, form.getlist("item_name"), form.getlist("item_qty"), form.getlist("item_unit"),
                            form.getlist("item_price"), form.getlist("item_rate"), form.getlist("item_product"),
                            required=r.invoicing)
        recurring.validate(r)
    except ValueError as e:
        msg = str(e)
        if "isoformat" in msg or "Invalid isoformat" in msg or "invalid literal" in msg:
            msg = "Sprawdź daty i liczby."
        return _recurring_form(request, db, r, msg)
    _recurring_mail_template(db, request, r, form)
    is_new = r.id is None
    db.add(r)
    db.flush()
    audit(db, web_actor(request), "recurring.save", f"{'Nowa' if is_new else 'Zmieniona'} umowa „{r.name}”",
          company_id=r.company_id, entity="recurring", entity_id=r.id)
    db.commit()
    flash(request, f"Zapisano umowę. Najbliższa faktura: {r.next_date:%d.%m.%Y}." if r.invoicing and r.active
          else "Zapisano umowę.")
    return redirect(f"/umowy/{r.id}")


def _recurring_mail_template(db: Session, request: Request, r: RecurringInvoice, form) -> None:
    """Tresc maila edytowana w umowie: zapis w wybranym szablonie albo jako nowy szablon."""
    subject, body = (form.get("mail_subject") or "").strip(), (form.get("mail_body") or "").strip()
    if not body:
        return
    tpl = db.get(EmailTemplate, r.template_id) if r.template_id else None
    if tpl is not None and tpl.company_id != r.company_id:
        tpl = r.template_id = None
    if form.get("tpl_save") == "new":
        name = (form.get("tpl_name") or "").strip()[:120] or f"Umowa {r.number or r.name}"[:120]
        tpl = EmailTemplate(company_id=r.company_id, kind="recurring", name=name, subject=subject[:300], body=body,
                            is_default=False)
        db.add(tpl)
        db.flush()
        r.template_id = tpl.id
        audit(db, web_actor(request), "template.save", f"Nowy szablon maila „{name}” (z umowy)", company_id=r.company_id)
    elif tpl is not None and (tpl.subject != subject[:300] or tpl.body != body):
        tpl.subject, tpl.body = subject[:300], body
        audit(db, web_actor(request), "template.save", f"Zmieniony szablon maila „{tpl.name}” (z umowy)",
              company_id=r.company_id)


@app.post("/umowy/nowa")
async def recurring_create(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    return await _recurring_save(request, db, RecurringInvoice(company_id=company.id, last_error=""))


@app.post("/umowy/{rid}")
async def recurring_update(rid: int, request: Request, db: Session = Depends(get_db)):
    return await _recurring_save(request, db, _get_recurring(db, request, rid))


@app.post("/umowy/{rid}/teraz")
def recurring_now(rid: int, request: Request, db: Session = Depends(get_db)):
    r = _get_recurring(db, request, rid)
    try:
        inv = recurring.generate(db, web_actor(request), r)
    except (ServiceError, ValueError) as e:
        db.rollback()
        flash(request, str(e))
        return redirect(f"/umowy/{rid}")
    flash(request, f"Utworzono {inv.display_number}. Następna: {r.next_date:%d.%m.%Y}.")
    return redirect(f"/faktury/{inv.id}")


@app.post("/umowy/{rid}/zakoncz")
def recurring_end(rid: int, request: Request, db: Session = Depends(get_db)):
    r = _get_recurring(db, request, rid)
    recurring.end_contract(r)
    audit(db, web_actor(request), "recurring.end", f"Zakończono umowę „{r.name}” z dniem {r.end_date:%d.%m.%Y}",
          company_id=r.company_id, entity="recurring", entity_id=r.id)
    db.commit()
    flash(request, f"Umowa zakończona z dniem {r.end_date:%d.%m.%Y}. Kolejnych faktur nie będzie.")
    return redirect(f"/umowy/{rid}")


CONTRACT_FILE_MAX = 25 * 1024 * 1024
_INLINE_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif"}


@app.post("/umowy/{rid}/pliki")
async def contract_file_add(rid: int, request: Request, db: Session = Depends(get_db)):
    """Skany umowy, aneksow itd. dodawane recznie; kilka plikow naraz."""
    r = _get_recurring(db, request, rid)
    form = await request.form()
    kind = form.get("kind") if form.get("kind") in CONTRACT_FILE_KINDS else "other"
    title = (form.get("title") or "").strip()[:200]
    try:
        doc_date = dt.date.fromisoformat(form["doc_date"]) if form.get("doc_date") else None
    except ValueError:
        doc_date = None
    added, skipped = 0, []
    for f in form.getlist("files"):
        if not getattr(f, "filename", None):
            continue
        raw = await f.read()
        name = os.path.basename(f.filename.replace("\\", "/"))[:200]
        if not raw or len(raw) > CONTRACT_FILE_MAX:
            skipped.append(name)
            continue
        ctype = (f.content_type or "").split(";")[0].strip().lower()[:100] or "application/octet-stream"
        if raw[:4] == b"%PDF":
            ctype = "application/pdf"
        db.add(ContractFile(company_id=r.company_id, recurring_id=r.id, kind=kind, title=title or name, doc_date=doc_date,
                            filename=name, content_type=ctype, size=len(raw), data=raw,
                            added_by=str(request.session.get("user", ""))[:60]))
        added += 1
    if added:
        audit(db, web_actor(request), "recurring.file_add",
              f"Umowa „{r.name}”: dodano {added} plik(i) ({CONTRACT_FILE_KINDS[kind]})",
              company_id=r.company_id, entity="recurring", entity_id=r.id)
        db.commit()
    msg = f"Dodano plików: {added}." if added else "Nie wybrano pliku."
    if skipped:
        msg += " Pominięte (puste albo ponad 25 MB): " + ", ".join(skipped)
    flash(request, msg)
    return redirect(f"/umowy/{rid}#pliki")


def _contract_file(db: Session, request: Request, rid: int, fid: int) -> ContractFile:
    r = _get_recurring(db, request, rid)
    f = db.get(ContractFile, fid)
    if f is None or f.recurring_id != r.id or f.company_id != r.company_id:
        raise HTTPException(404)
    return f


@app.get("/umowy/{rid}/pliki/{fid}")
def contract_file_get(rid: int, fid: int, request: Request, pobierz: int = 0, db: Session = Depends(get_db)):
    f = _contract_file(db, request, rid, fid)
    inline = f.content_type in _INLINE_TYPES and not pobierz
    ctype = f.content_type if f.content_type in _INLINE_TYPES else "application/octet-stream"
    safe = "".join(ch if ch.isascii() and ch.isprintable() and ch not in '"\\' else "_" for ch in f.filename) or "plik"
    disp = f"{'inline' if inline else 'attachment'}; filename=\"{safe}\"; filename*=UTF-8''{quote(f.filename)}"
    return Response(f.data, media_type=ctype, headers={"Content-Disposition": disp, "X-Content-Type-Options": "nosniff",
                                                       "Content-Security-Policy": "sandbox"})


@app.post("/umowy/{rid}/pliki/{fid}/usun")
def contract_file_delete(rid: int, fid: int, request: Request, db: Session = Depends(get_db)):
    f = _contract_file(db, request, rid, fid)
    r = f.recurring_id
    audit(db, web_actor(request), "recurring.file_delete", f"Usunięto plik umowy „{f.title}” ({f.filename})",
          company_id=f.company_id, entity="recurring", entity_id=r)
    db.delete(f)
    db.commit()
    flash(request, "Plik usunięty.")
    return redirect(f"/umowy/{rid}#pliki")


@app.post("/umowy/{rid}/status")
async def recurring_status(rid: int, request: Request, db: Session = Depends(get_db)):
    """Status umowy (informacja dla zespolu), zmieniany tez prosto z listy."""
    r = _get_recurring(db, request, rid)
    form = await request.form()
    st = form.get("status") or ""
    if ost.find(db, r.company_id, st, "contract") is None:
        raise HTTPException(400)
    if st != r.contract_status:
        r.contract_status = st
        audit(db, web_actor(request), "recurring.status", f"Umowa „{r.name}”: status {r.contract_status_label}",
              company_id=r.company_id, entity="recurring", entity_id=r.id)
        db.commit()
    back = form.get("back") or ""
    return redirect(back if back.startswith("/umowy") else f"/umowy/{rid}")


@app.post("/umowy/{rid}/wznow")
def recurring_resume(rid: int, request: Request, db: Session = Depends(get_db)):
    r = _get_recurring(db, request, rid)
    r.active, r.end_date = True, None
    if r.contract_status == "expired":
        r.contract_status = "active"
    today = dt.date.today()
    if r.next_date < today:
        r.next_date = recurring.first_date(today, r.day_of_month)
    audit(db, web_actor(request), "recurring.resume", f"Wznowiono umowę „{r.name}”",
          company_id=r.company_id, entity="recurring", entity_id=r.id)
    db.commit()
    flash(request, f"Umowa wznowiona, bezterminowo. Najbliższa faktura: {r.next_date:%d.%m.%Y}.")
    return redirect(f"/umowy/{rid}")


@app.get("/cykliczne{rest:path}")
def recurring_old_url(rest: str):
    return redirect("/umowy" + rest)


@app.post("/umowy/{rid}/usun")
def recurring_delete(rid: int, request: Request, db: Session = Depends(get_db)):
    r = _get_recurring(db, request, rid)
    db.execute(update(Invoice).where(Invoice.recurring_id == r.id).values(recurring_id=None))
    audit(db, web_actor(request), "recurring.delete", f"Usunięto fakturę cykliczną „{r.name}”",
          company_id=r.company_id, entity="recurring")
    db.delete(r)
    db.commit()
    flash(request, "Usunięto umowę. Wystawione faktury zostają.")
    return redirect("/umowy")
