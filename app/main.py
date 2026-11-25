"""Aplikacja www: faktury, kontrahenci, auta, ustawienia firm."""
from __future__ import annotations

import datetime as dt
import hmac
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import api, auth, calc, config, mcp_server, numbering, pdf
from .services import audit, car_item_name
from .db import Base, SessionLocal, engine, get_db
from .models import (
    CAR_STATUSES,
    SCOPES,
    ApiToken,
    AuditLog,
    INVOICE_KINDS,
    PAYMENT_METHODS,
    VAT_RATES,
    Car,
    Company,
    Contractor,
    Invoice,
    InvoiceItem,
)
from .seed import seed

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["money"] = calc.format_money
templates.env.filters["qty"] = calc.format_qty
templates.env.filters["plnwords"] = calc.amount_in_words
templates.env.filters["date"] = lambda d: d.strftime("%d.%m.%Y") if d else ""
templates.env.globals.update(
    INVOICE_KINDS=INVOICE_KINDS, PAYMENT_METHODS=PAYMENT_METHODS, VAT_RATES=VAT_RATES, CAR_STATUSES=CAR_STATUSES
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    yield


app = FastAPI(title="Sygnavo", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

PUBLIC_PATHS = ("/login", "/static", "/health", "/api/", "/mcp", "/oauth/", "/.well-known/")


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
def login(request: Request, login: str = Form(...), password: str = Form(...)):
    ok = hmac.compare_digest(login, config.ADMIN_LOGIN) and hmac.compare_digest(password, config.ADMIN_PASSWORD)
    if not ok:
        return templates.TemplateResponse(request, "login.html", {"error": "Zły login lub hasło."}, status_code=401)
    nxt = request.session.pop("next", None)
    request.session["user"] = login
    return redirect(nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else "/")


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
    total = sum((i.total_gross for i in invoices if i.status == "issued"), Decimal("0"))
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
    return dict(contractors=contractors, cars=cars, contractors_json=contractors_json)


@app.get("/faktury/nowa", response_class=HTMLResponse)
def invoice_new(request: Request, rodzaj: str = "vat", auto: int | None = None, db: Session = Depends(get_db)):
    company = current_company(request, db)
    kind = rodzaj if rodzaj in INVOICE_KINDS else "vat"
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

    kind = form.get("kind", inv.kind or "vat")
    inv.kind = kind if kind in INVOICE_KINDS else "vat"
    inv.issue_date = date("issue_date")
    inv.sale_date = date("sale_date")
    inv.due_date = date("due_date", required=False)
    inv.issue_place = form.get("issue_place", "").strip()
    inv.payment_method = form.get("payment_method", "transfer")
    inv.prices_gross = form.get("prices_gross") == "1"
    inv.paid = calc.parse_decimal(form.get("paid"), Decimal("0"))
    inv.notes = form.get("notes", "").strip()
    inv.buyer_name = form.get("buyer_name", "").strip()
    inv.buyer_nip = form.get("buyer_nip", "").replace("-", "").replace(" ", "").strip()
    inv.buyer_address = form.get("buyer_address", "").strip()

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
    inv.items.clear()
    for i, name in enumerate(names):
        name = name.strip()
        if not name:
            continue
        price = calc.parse_decimal(prices[i] if i < len(prices) else "", None)
        if price is None:
            raise ValueError(f"Brak ceny w pozycji „{name}”.")
        rate = rates[i] if i < len(rates) and rates[i] in VAT_RATES else "23"
        inv.items.append(
            InvoiceItem(
                name=name,
                quantity=calc.parse_decimal(qtys[i] if i < len(qtys) else "", Decimal("1")),
                unit=(units[i] if i < len(units) else "").strip() or "szt.",
                unit_price=price,
                vat_rate=rate,
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
    inv = Invoice(company_id=company.id, kind="vat")
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
    return render(request, db, "invoice_view.html", inv=inv, summary=calc.vat_summary(inv.items))


@app.get("/faktury/{invoice_id}/edytuj", response_class=HTMLResponse)
def invoice_edit(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    if inv.status != "draft":
        flash(request, "Wystawionej faktury nie można zmieniać. Zmiany robi się korektą (w następnym etapie).")
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
    inv.paid = inv.total_gross
    audit(db, web_actor(request), "invoice.paid", f"{inv.number}: zapłacono {calc.format_money(inv.paid)} zł",
          company_id=inv.company_id, entity="invoice", entity_id=inv.id)
    db.commit()
    flash(request, "Oznaczono jako zapłaconą.")
    return redirect(f"/faktury/{inv.id}")


@app.post("/faktury/{invoice_id}/usun")
def invoice_delete(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = get_invoice(db, request, invoice_id)
    if inv.status != "draft":
        flash(request, "Wystawionej faktury nie można usunąć.")
        return redirect(f"/faktury/{inv.id}")
    audit(db, web_actor(request), "invoice.delete", f"Usunięto szkic dla {inv.buyer_name}",
          company_id=inv.company_id, entity="invoice")
    db.delete(inv)
    db.commit()
    flash(request, "Usunięto szkic.")
    return redirect("/faktury")


@app.post("/faktury/{invoice_id}/kopiuj")
def invoice_copy(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    src = get_invoice(db, request, invoice_id)
    today = dt.date.today()
    inv = Invoice(
        company_id=src.company_id, kind=src.kind, issue_date=today, sale_date=today,
        issue_place=src.issue_place, due_date=today + dt.timedelta(days=src.company.default_payment_days),
        payment_method=src.payment_method, prices_gross=src.prices_gross, contractor_id=src.contractor_id,
        buyer_name=src.buyer_name, buyer_nip=src.buyer_nip, buyer_address=src.buyer_address, notes=src.notes,
        paid=Decimal("0"),
    )
    inv.items = [
        InvoiceItem(name=i.name, quantity=i.quantity, unit=i.unit, unit_price=i.unit_price, vat_rate=i.vat_rate,
                    purchase_price=i.purchase_price)
        for i in src.items
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
    return render(request, db, "contractor_form.html", c=c, invoices=invoices)


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


# --- ustawienia firmy -----------------------------------------------------

COMPANY_FIELDS = ["short_name", "name", "nip", "street", "postal_code", "city", "bank_name", "bank_account",
                  "email", "phone", "issue_place", "pattern_vat"]


@app.get("/ustawienia", response_class=HTMLResponse)
def settings_form(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "settings.html")


@app.post("/ustawienia")
async def settings_save(request: Request, db: Session = Depends(get_db)):
    company = current_company(request, db)
    form = await request.form()
    for f in COMPANY_FIELDS:
        setattr(company, f, (form.get(f) or "").strip())
    company.nip = company.nip.replace("-", "").replace(" ", "")
    company.numbering_reset = "year" if form.get("numbering_reset") == "year" else "month"
    days = form.get("default_payment_days") or "7"
    company.default_payment_days = int(days) if days.isdigit() else 7
    for p in (company.pattern_vat,):
        if "{nr}" not in p:
            db.rollback()
            return render(request, db, "settings.html", error="Wzór numeru musi zawierać {nr}.")
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
