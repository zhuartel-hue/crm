"""Strony integracji: /ustawienia/integracje (konfiguracja) i przesylki przy zamowieniu (/zamowienia/.../paczka)."""
from __future__ import annotations

import json
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import INTEGRATION_KINDS, Integration, Order, Product, ProductLink, Shipment
from ..services import audit
from . import allegro, apaczka, shopify
from .common import IntegrationError, secrets_of, store_secrets, update_settings
from .service import sync_one

router = APIRouter()
BASE = "/ustawienia/integracje"


def _m():
    from .. import main
    return main


def _get(db: Session, request: Request, iid: int) -> Integration:
    integ = db.get(Integration, iid)
    if integ is None or integ.company_id != _m().current_company(request, db).id:
        raise HTTPException(404)
    return integ


def _redirect_allegro(request: Request) -> str:
    from .. import auth
    return auth.base_url(request) + BASE + "/allegro/powrot"


def mask(v: str) -> str:
    return (v[:4] + "…" + v[-3:]) if v and len(v) > 10 else ("•••" if v else "")


@router.get(BASE, response_class=HTMLResponse)
def integrations_list(request: Request, db: Session = Depends(get_db)):
    m = _m()
    company = m.current_company(request, db)
    rows = db.scalars(select(Integration).where(Integration.company_id == company.id).order_by(Integration.id)).all()
    return m.render(request, db, "integrations.html", rows=rows, KINDS=INTEGRATION_KINDS)


@router.post(BASE + "/nowa")
async def integration_new(request: Request, db: Session = Depends(get_db)):
    m = _m()
    form = await request.form()
    kind = form.get("kind") or ""
    if kind not in INTEGRATION_KINDS:
        return m.redirect(BASE)
    company = m.current_company(request, db)
    integ = Integration(company_id=company.id, kind=kind, name=INTEGRATION_KINDS[kind], state="new",
                        created_by=m.web_actor(request)[:100])
    if kind == "apaczka":
        integ.settings = {"sender": {"name": company.name[:50], "line1": company.street, "postal_code":
                                     company.postal_code, "city": company.city, "email": company.email,
                                     "phone": company.phone, "contact_person": ""},
                          "pickup_type": "SELF", "parcel": {"l": 30, "w": 20, "h": 10, "kg": 1}}
    db.add(integ)
    db.flush()
    audit(db, m.web_actor(request), "integration.create", f"Dodano integrację {integ.kind_label}",
          company_id=company.id, entity="integration", entity_id=integ.id)
    db.commit()
    return m.redirect(f"{BASE}/{integ.id}")


@router.get(BASE + "/allegro/powrot")
def allegro_back(request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    m = _m()
    company = m.current_company(request, db)
    integ = db.scalar(select(Integration).where(Integration.company_id == company.id, Integration.kind == "allegro",
                                                Integration.auth_state == state)) if state else None
    if integ is None:
        m.flash(request, "Nie rozpoznano powrotu z Allegro. Kliknij „Połącz z Allegro” jeszcze raz.")
        return m.redirect(BASE)
    if error or not code:
        integ.auth_state = ""
        db.commit()
        m.flash(request, "Allegro: nie udzielono zgody.")
        return m.redirect(f"{BASE}/{integ.id}")
    try:
        allegro.finish(db, integ, code, _redirect_allegro(request))
        audit(db, m.web_actor(request), "integration.connect", f"Połączono Allegro ({integ.name})",
              company_id=integ.company_id, entity="integration", entity_id=integ.id)
        db.commit()
        m.flash(request, f"Połączono z Allegro: {integ.name}. Zamówienia pobiorą się w ciągu kilku minut.")
    except IntegrationError as e:
        db.rollback()
        m.flash(request, str(e))
    return m.redirect(f"{BASE}/{integ.id}")


@router.get(BASE + "/{iid}", response_class=HTMLResponse)
def integration_view(iid: int, request: Request, db: Session = Depends(get_db)):
    m = _m()
    integ = _get(db, request, iid)
    sec = secrets_of(integ)
    links = db.scalars(select(ProductLink).where(ProductLink.integration_id == integ.id)).all()
    stats = {"links": len(links), "linked": sum(1 for x in links if x.product_id),
             "orders": db.scalar(select(func.count(Order.id))
                                 .where(Order.integration_id == integ.id)) or 0}
    return m.render(request, db, "integration.html", i=integ, s=integ.settings,
                    masked={k: mask(v) for k, v in sec.items() if k in ("client_id", "client_secret", "admin_token",
                                                                         "app_id", "app_secret")},
                    connected=bool(sec.get("refresh_token") or sec.get("admin_token") or sec.get("access_token")),
                    stats=stats, redirect_url=_redirect_allegro(request))


@router.post(BASE + "/{iid}/klucze")
async def integration_keys(iid: int, request: Request, db: Session = Depends(get_db)):
    m = _m()
    integ = _get(db, request, iid)
    form = await request.form()
    sec = secrets_of(integ)
    for k in ("client_id", "client_secret", "admin_token", "app_id", "app_secret"):
        v = (form.get(k) or "").strip()
        if v:
            sec[k] = v
            if k in ("client_id", "client_secret"):
                sec.pop("access_token", None)
                sec.pop("expires_at", None)
    try:
        if integ.kind == "shopify":
            update_settings(integ, shop=shopify.normalize_shop(form.get("shop") or integ.settings.get("shop") or ""),
                            first_days=int(form.get("first_days") or 30))
            store_secrets(integ, sec)
            data = shopify.gql(db, integ, "{ shop { name } }", {})
            integ.name = ((data.get("shop") or {}).get("name") or "Sklep")[:120]
            integ.state = "active"
        elif integ.kind == "allegro":
            update_settings(integ, env="sandbox" if form.get("env") == "sandbox" else "prod",
                            first_days=int(form.get("first_days") or 30))
            store_secrets(integ, sec)
        elif integ.kind == "apaczka":
            sender = {f: (form.get(f"sender_{f}") or "").strip() for f in
                      ("name", "line1", "line2", "postal_code", "city", "contact_person", "email", "phone")}
            parcel = {f: (form.get(f"parcel_{f}") or "").strip() for f in ("l", "w", "h", "kg")}
            update_settings(integ, sender=sender, parcel=parcel,
                            pickup_type="COURIER" if form.get("pickup_type") == "COURIER" else "SELF",
                            cod_account=(form.get("cod_account") or "").strip())
            store_secrets(integ, sec)
            n = apaczka.test(integ)
            integ.state = "active"
            m.flash(request, f"Apaczka działa: {n} usług kurierskich dostępnych.")
        integ.last_error = ""
        audit(db, m.web_actor(request), "integration.keys", f"Zapisano ustawienia {integ.kind_label}",
              company_id=integ.company_id, entity="integration", entity_id=integ.id)
        db.commit()
        if integ.kind == "shopify":
            m.flash(request, f"Połączono ze sklepem {integ.name}. Kliknij „Pobierz teraz” albo poczekaj kilka minut.")
        elif integ.kind == "allegro":
            m.flash(request, "Zapisano. Teraz kliknij „Połącz z Allegro”.")
    except (IntegrationError, ValueError) as e:
        db.rollback()
        integ = _get(db, request, iid)
        integ.last_error = str(e)[:500]
        db.commit()
        m.flash(request, str(e))
    return m.redirect(f"{BASE}/{iid}")


@router.post(BASE + "/{iid}/allegro")
def allegro_start(iid: int, request: Request, db: Session = Depends(get_db)):
    m = _m()
    integ = _get(db, request, iid)
    try:
        url = allegro.authorize_url(db, integ, _redirect_allegro(request))
        db.commit()
    except IntegrationError as e:
        m.flash(request, str(e))
        return m.redirect(f"{BASE}/{iid}")
    return m.redirect(url)


@router.post(BASE + "/{iid}/pobierz")
def integration_sync(iid: int, request: Request, db: Session = Depends(get_db)):
    m = _m()
    integ = _get(db, request, iid)
    try:
        res = sync_one(db, integ, m.web_actor(request))
        m.flash(request, f"Pobrano: {res.get('new', 0)} nowych, {res.get('updated', 0)} zaktualizowanych zamówień.")
    except IntegrationError as e:
        m.flash(request, str(e))
    return m.redirect(f"{BASE}/{iid}")


@router.post(BASE + "/{iid}/stan")
async def integration_state(iid: int, request: Request, db: Session = Depends(get_db)):
    m = _m()
    integ = _get(db, request, iid)
    form = await request.form()
    action = form.get("action")
    if action == "off":
        integ.state = "off"
    elif action == "on" and integ.state == "off":
        integ.state = "active"
    elif action == "delete":
        name = integ.kind_label
        for s in db.scalars(select(Shipment).where(Shipment.integration_id == integ.id)):
            s.integration_id = None
        for o in db.scalars(select(Order).where(Order.integration_id == integ.id)):
            o.integration_id = None
        db.delete(integ)
        audit(db, m.web_actor(request), "integration.delete", f"Usunięto integrację {name}",
              company_id=integ.company_id, entity="integration")
        db.commit()
        m.flash(request, f"Usunięto integrację {name}. Pobrane zamówienia zostały w Zamówieniach.")
        return m.redirect(BASE)
    db.commit()
    return m.redirect(f"{BASE}/{iid}")


@router.get(BASE + "/{iid}/produkty", response_class=HTMLResponse)
def integration_products(iid: int, request: Request, f: str = "", db: Session = Depends(get_db)):
    m = _m()
    integ = _get(db, request, iid)
    stmt = select(ProductLink).where(ProductLink.integration_id == integ.id)
    if f == "brak":
        stmt = stmt.where(ProductLink.product_id.is_(None))
    rows = db.scalars(stmt.order_by(ProductLink.external_name).limit(2000)).all()
    products = db.scalars(select(Product).where(Product.company_id == integ.company_id, Product.active.is_(True))
                          .order_by(Product.name)).all()
    return m.render(request, db, "integration_products.html", i=integ, rows=rows, products=products, f=f)


@router.post(BASE + "/{iid}/produkty")
async def integration_products_post(iid: int, request: Request, db: Session = Depends(get_db)):
    m = _m()
    integ = _get(db, request, iid)
    form = await request.form()
    action = form.get("action") or "sync"
    try:
        if action == "sync":
            if integ.kind == "allegro":
                res = allegro.sync_offers(db, integ)
                m.flash(request, f"Oferty Allegro: {res['offers']}, powiązane z produktami: {res['linked']}.")
            elif integ.kind == "shopify":
                n = shopify.sync_products(db, integ)
                m.flash(request, f"Pobrano {n} wariantów produktów ze sklepu.")
        elif action == "link":
            link = db.get(ProductLink, int(form.get("link_id") or 0))
            if link is None or link.integration_id != integ.id:
                raise HTTPException(404)
            pid = form.get("product_id") or ""
            p = db.get(Product, int(pid)) if pid.isdigit() else None
            link.product_id = p.id if p is not None and p.company_id == integ.company_id else None
        elif action == "create":
            n = 0
            for link in db.scalars(select(ProductLink).where(ProductLink.integration_id == integ.id,
                                                             ProductLink.product_id.is_(None))):
                code = (link.external_sku or "")[:60]
                gross = link.external_price
                net = (gross / Decimal("1.23")).quantize(Decimal("0.01")) if gross is not None else None
                p = Product(company_id=integ.company_id, code=code, name=link.external_name or code or "Produkt",
                            price_net=net, vat_rate="23", source_ref=f"{integ.kind}:{link.external_id}"[:120])
                db.add(p)
                db.flush()
                link.product_id = p.id
                n += 1
            m.flash(request, f"Dodano {n} produktów do magazynu (cena netto z ceny brutto, VAT 23%: sprawdź).")
        elif action == "stock" and integ.kind == "allegro":
            link = db.get(ProductLink, int(form.get("link_id") or 0))
            if link is None or link.integration_id != integ.id:
                raise HTTPException(404)
            allegro.push_stock(db, integ, link)
            audit(db, m.web_actor(request), "allegro.stock", f"Stan {link.external_stock} → oferta {link.external_id}",
                  company_id=integ.company_id, entity="integration", entity_id=integ.id)
            m.flash(request, f"Wysłano stan {link.external_stock:.0f} szt. do oferty Allegro.")
        db.commit()
    except IntegrationError as e:
        db.rollback()
        m.flash(request, str(e))
    back = f"{BASE}/{iid}/produkty" + (f"?f={form.get('f')}" if form.get("f") else "")
    return m.redirect(back)


# ------------------------------------------------------------------ przesylki


def _order(db: Session, request: Request, zid: int) -> Order:
    z = db.get(Order, zid)
    if z is None or z.company_id != _m().current_company(request, db).id:
        raise HTTPException(404)
    return z


def _courier(db: Session, company_id: int) -> Integration | None:
    return db.scalar(select(Integration).where(Integration.company_id == company_id, Integration.kind == "apaczka",
                                               Integration.state == "active").order_by(Integration.id).limit(1))


def _receiver_from_order(z: Order) -> dict:
    lines = [x for x in (z.delivery_address or z.recipient_address or "").splitlines() if x.strip()]
    postal, city, street = "", "", ""
    for ln in lines:
        import re
        mt = re.match(r"^\s*(\d{2}-\d{3})\s+(.+?)(?:,\s*[A-Z]{2})?\s*$", ln)
        if mt:
            postal, city = mt.group(1), mt.group(2)
        elif not street and any(ch.isdigit() for ch in ln):
            street = ln
    point = ""
    if z.pickup_point:
        point = z.pickup_point.split()[0]
    return {"name": (z.recipient_name or "")[:50], "contact_person": z.contact_person or z.recipient_name,
            "line1": street, "line2": "", "postal_code": postal, "city": city, "email": z.email, "phone": z.phone,
            "is_residential": not z.buyer_tax_id, "point": point}


def _shipment_from_form(form, z: Order, integ: Integration) -> Shipment:
    rec = {f: (form.get(f"r_{f}") or "").strip() for f in
           ("name", "contact_person", "line1", "line2", "postal_code", "city", "email", "phone", "point")}
    rec["is_residential"] = form.get("r_is_residential") == "1"
    parcel = {f: (form.get(f"p_{f}") or "").strip() for f in ("l", "w", "h", "kg", "content", "comment")}
    from ..calc import parse_decimal
    try:
        cod = parse_decimal(form.get("cod") or "", None)
    except ValueError:
        cod = None
    return Shipment(company_id=z.company_id, order_id=z.id, integration_id=integ.id, provider="apaczka",
                    service_id=(form.get("service_id") or "")[:20], receiver_json=json.dumps(rec, ensure_ascii=False),
                    parcel_json=json.dumps(parcel, ensure_ascii=False), cod_amount=cod)


@router.get("/zamowienia/{zid}/paczka", response_class=HTMLResponse)
def shipment_form(zid: int, request: Request, db: Session = Depends(get_db)):
    m = _m()
    z = _order(db, request, zid)
    integ = _courier(db, z.company_id)
    parcel = dict((integ.settings.get("parcel") if integ else None) or {"l": 30, "w": 20, "h": 10, "kg": 1})
    parcel.setdefault("content", "Towar")
    cod = z.total_gross if z.payment_status == "cod" else None
    return m.render(request, db, "shipment_form.html", z=z, integ=integ, rcv=_receiver_from_order(z), pcl=parcel,
                    cod=cod, prices=None, service_id="")


@router.post("/zamowienia/{zid}/paczka", response_class=HTMLResponse)
async def shipment_post(zid: int, request: Request, db: Session = Depends(get_db)):
    m = _m()
    z = _order(db, request, zid)
    integ = _courier(db, z.company_id)
    if integ is None:
        m.flash(request, "Najpierw podłącz Apaczkę w Ustawieniach → Integracje.")
        return m.redirect(f"/zamowienia/{zid}")
    form = await request.form()
    s = _shipment_from_form(form, z, integ)
    r, p = json.loads(s.receiver_json), json.loads(s.parcel_json)
    if form.get("action") != "send":
        prices, error = None, None
        try:
            prices = apaczka.valuation(integ, s)
        except IntegrationError as e:
            error = str(e)
        return m.render(request, db, "shipment_form.html", z=z, integ=integ, rcv=r, pcl=p, cod=s.cod_amount,
                        prices=prices, service_id=s.service_id, error=error)
    if not s.service_id:
        return m.render(request, db, "shipment_form.html", z=z, integ=integ, rcv=r, pcl=p, cod=s.cod_amount,
                        prices=None, service_id="", error="Wybierz kuriera z wyceny.")
    s.service_name = (form.get("service_name") or "")[:120]
    s.created_by = m.web_actor(request)[:100]
    db.add(s)
    try:
        apaczka.send(integ, s)
        try:
            s.label = apaczka.label(integ, s)
        except IntegrationError as e:
            s.last_error = f"Etykieta: {e}"
        audit(db, m.web_actor(request), "shipment.create",
              f"Przesyłka {s.service_name} {s.waybill} do zamówienia {z.display_number}",
              company_id=z.company_id, entity="order", entity_id=z.id)
        db.commit()
        m.flash(request, f"Zamówiono kuriera: {s.service_name}, nr {s.waybill or '(nadawany)'}.")
    except IntegrationError as e:
        db.rollback()
        return m.render(request, db, "shipment_form.html", z=z, integ=integ, rcv=r, pcl=p, cod=s.cod_amount,
                        prices=None, service_id="", error=str(e))
    return m.redirect(f"/zamowienia/{zid}")


def _shipment(db: Session, request: Request, sid: int) -> Shipment:
    s = db.get(Shipment, sid)
    if s is None or s.company_id != _m().current_company(request, db).id:
        raise HTTPException(404)
    return s


@router.get("/zamowienia/przesylki/{sid}/etykieta")
def shipment_label(sid: int, request: Request, db: Session = Depends(get_db)):
    s = _shipment(db, request, sid)
    if not s.label:
        integ = db.get(Integration, s.integration_id or 0)
        if integ is None:
            raise HTTPException(404)
        try:
            s.label = apaczka.label(integ, s)
            db.commit()
        except IntegrationError as e:
            _m().flash(request, str(e))
            return _m().redirect(f"/zamowienia/{s.order_id}")
    return Response(s.label, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="etykieta-{s.waybill or s.id}.pdf"'})


@router.post("/zamowienia/przesylki/{sid}/{action}")
def shipment_action(sid: int, action: str, request: Request, db: Session = Depends(get_db)):
    m = _m()
    s = _shipment(db, request, sid)
    integ = db.get(Integration, s.integration_id or 0)
    try:
        if integ is None:
            raise IntegrationError("Integracja kuriera została usunięta.")
        if action == "sledz":
            apaczka.track(integ, s)
        elif action == "anuluj":
            apaczka.cancel(integ, s)
            audit(db, m.web_actor(request), "shipment.cancel", f"Anulowano przesyłkę {s.waybill}",
                  company_id=s.company_id, entity="order", entity_id=s.order_id)
        db.commit()
    except IntegrationError as e:
        db.rollback()
        m.flash(request, str(e))
    return m.redirect(f"/zamowienia/{s.order_id}")
