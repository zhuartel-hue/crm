"""REST API (JSON) dla integracji: te same operacje co narzedzia MCP. Naglowek: Authorization: Bearer <klucz>."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from . import products as prod
from . import services as svc
from .auth import Principal, authenticate, base_url, unauthorized
from .db import get_db

router = APIRouter(prefix="/api/v1")


class ApiAuthError(Exception):
    pass


async def principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    # async: ustawiony tu zakres organizacji (app/tenancy.py) obowiazuje w calym zapytaniu.
    p = authenticate(db, request)
    if p is None:
        raise ApiAuthError()
    return p


def need(p: Principal, scope: str) -> None:
    try:
        p.require(scope)
    except PermissionError as e:
        raise HTTPException(403, str(e))


def run(fn):
    try:
        return fn()
    except svc.ServiceError as e:
        raise HTTPException(422, str(e))


@router.get("/companies")
def companies(p: Principal = Depends(principal), db: Session = Depends(get_db)):
    need(p, "read")
    return {"companies": svc.list_companies(db)}


@router.get("/contractors")
def contractors(company: str = "", q: str = "", limit: int = 20, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    need(p, "read")
    return {"contractors": run(lambda: svc.search_contractors(db, company, q, limit))}


@router.post("/contractors", status_code=201)
async def contractor_create(request: Request, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    need(p, "write")
    data = await request.json()
    return {"contractor": run(lambda: svc.create_contractor(db, p.actor, data))}


@router.get("/cars")
def cars(company: str = "", q: str = "", status: str = "", limit: int = 20,
         p: Principal = Depends(principal), db: Session = Depends(get_db)):
    need(p, "read")
    return {"cars": run(lambda: svc.search_cars(db, company, q, status, limit))}


@router.post("/cars", status_code=201)
async def car_create(request: Request, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    need(p, "write")
    data = await request.json()
    return {"car": run(lambda: svc.create_car(db, p.actor, data))}


@router.get("/products")
def products(request: Request, company: str = "", q: str = "", include_inactive: bool = False, limit: int = 50,
             p: Principal = Depends(principal), db: Session = Depends(get_db)):
    need(p, "read")
    rows = run(lambda: prod.search(db, svc.resolve_company(db, company).id, q, active_only=not include_inactive, limit=limit))
    return {"products": [prod.product_dict(x, base_url(request)) for x in rows]}


@router.get("/invoices")
def invoices(request: Request, company: str = "", kind: str = "", month: str = "", status: str = "",
             unpaid_only: bool = False, q: str = "", limit: int = 30,
             p: Principal = Depends(principal), db: Session = Depends(get_db)):
    need(p, "read")
    rows = run(lambda: svc.list_invoices(db, company, kind, month, status, unpaid_only, q, limit))
    return {"invoices": [svc.invoice_dict(i, base_url(request), with_items=False) for i in rows]}


@router.get("/invoices/{invoice_id}")
def invoice(invoice_id: int, request: Request, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    need(p, "read")
    return {"invoice": svc.invoice_dict(run(lambda: svc.get_invoice(db, invoice_id)), base_url(request))}


@router.post("/invoices", status_code=201)
async def invoice_draft(request: Request, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    need(p, "write")
    data = await request.json()
    inv = run(lambda: svc.create_invoice_draft(db, p.actor, data))
    return {"invoice": svc.invoice_dict(inv, base_url(request))}


async def _json(request: Request) -> dict:
    return (await request.json()) if await request.body() else {}


@router.post("/invoices/{invoice_id}/correction", status_code=201)
async def invoice_correction(invoice_id: int, request: Request, p: Principal = Depends(principal),
                             db: Session = Depends(get_db)):
    need(p, "write")
    data = await _json(request)
    inv = run(lambda: svc.start_correction(db, p.actor, invoice_id, data.get("reason", ""), data.get("items")))
    return {"invoice": svc.invoice_dict(inv, base_url(request))}


@router.post("/invoices/{invoice_id}/from-proforma", status_code=201)
def invoice_from_proforma(invoice_id: int, request: Request, p: Principal = Depends(principal),
                          db: Session = Depends(get_db)):
    need(p, "write")
    return {"invoice": svc.invoice_dict(run(lambda: svc.start_from_proforma(db, p.actor, invoice_id)),
                                        base_url(request))}


@router.post("/invoices/{invoice_id}/final", status_code=201)
def invoice_final(invoice_id: int, request: Request, p: Principal = Depends(principal),
                  db: Session = Depends(get_db)):
    need(p, "write")
    return {"invoice": svc.invoice_dict(run(lambda: svc.start_final(db, p.actor, invoice_id)), base_url(request))}


@router.post("/invoices/{invoice_id}/issue")
def invoice_issue(invoice_id: int, request: Request, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    need(p, "issue")
    inv = run(lambda: svc.issue_invoice(db, p.actor, invoice_id))
    return {"invoice": svc.invoice_dict(inv, base_url(request))}


@router.post("/invoices/{invoice_id}/payment")
async def invoice_payment(invoice_id: int, request: Request, p: Principal = Depends(principal),
                          db: Session = Depends(get_db)):
    need(p, "write")
    body = await request.body()
    amount = (await request.json()).get("amount") if body else None
    inv = run(lambda: svc.mark_paid(db, p.actor, invoice_id, amount))
    return {"invoice": svc.invoice_dict(inv, base_url(request), with_items=False)}


def install(app) -> None:
    app.include_router(router)

    @app.exception_handler(ApiAuthError)
    async def _auth_error(request: Request, exc: ApiAuthError):
        return unauthorized(request)
