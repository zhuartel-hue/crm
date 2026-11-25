"""Serwer MCP (Streamable HTTP, odpowiedzi JSON, bez sesji) dla Claude.

Konektor w Claude: adres https://faktury.<serwer>/mcp, logowanie przez OAuth (app/auth.py).
Kazde narzedzie sprawdza uprawnienie tokenu (read / write / issue) i zapisuje zmiany w dzienniku.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from . import services as svc
from .auth import Principal, authenticate, base_url, unauthorized
from .db import get_db
from .models import CAR_STATUSES, PAYMENT_METHODS, VAT_RATES

PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]
SERVER_INFO = {"name": "sygnavo", "title": "Sygnavo: faktury i CRM (Margot, ARI ERDMANN WORLDS)", "version": "0.2.1"}
INSTRUCTIONS = (
    "Sygnavo: program do faktur i CRM autokomisu, dwie firmy: Margot (JDG) i ARI ERDMANN WORLDS (spółka). "
    "Kwoty w PLN jako tekst z kropką (np. \"36150.00\"). Faktura 'margin' to VAT marża na auta używane: "
    "cena brutto, VAT liczony od marży, na fakturze dla klienta nie ma kwoty VAT. "
    "Zasady: najpierw create_invoice_draft, pokaż użytkownikowi nabywcę, pozycje, kwotę brutto i marżę, "
    "a issue_invoice wywołaj dopiero po wyraźnym potwierdzeniu użytkownika w tej rozmowie. "
    "Gdy nie wiesz, której firmy dotyczy prośba, zapytaj."
)

router = APIRouter()


@dataclass
class Tool:
    name: str
    title: str
    description: str
    scope: str
    schema: dict
    handler: Callable[[Session, Principal, dict, str], Any]
    read_only: bool = True
    destructive: bool = False


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


COMPANY = {"type": "string", "description": "Firma: id albo nazwa, np. \"Margot\" albo \"ARI\"."}
MONEY = {"type": ["string", "number"], "description": "Kwota w PLN, np. \"36150.00\"."}


def _t_companies(db, p, a, base):
    return {"companies": svc.list_companies(db)}


def _t_search_contractors(db, p, a, base):
    return {"contractors": svc.search_contractors(db, a.get("query", ""), a.get("limit", 20))}


def _t_create_contractor(db, p, a, base):
    return {"contractor": svc.create_contractor(db, p.actor, a)}


def _t_search_cars(db, p, a, base):
    return {"cars": svc.search_cars(db, a.get("company"), a.get("query", ""), a.get("status", ""), a.get("limit", 20))}


def _t_create_car(db, p, a, base):
    return {"car": svc.create_car(db, p.actor, a)}


def _t_list_invoices(db, p, a, base):
    rows = svc.list_invoices(db, a.get("company"), a.get("kind", ""), a.get("month", ""), a.get("status", ""),
                             bool(a.get("unpaid_only")), a.get("query", ""), a.get("limit", 30))
    return {"invoices": [svc.invoice_dict(i, base, with_items=False) for i in rows]}


def _t_get_invoice(db, p, a, base):
    return {"invoice": svc.invoice_dict(svc.get_invoice(db, a["invoice_id"]), base)}


def _t_create_draft(db, p, a, base):
    inv = svc.create_invoice_draft(db, p.actor, a)
    return {"invoice": svc.invoice_dict(inv, base),
            "next_step": "Pokaż szkic użytkownikowi. Wystaw (issue_invoice) tylko po jego potwierdzeniu."}


def _t_issue(db, p, a, base):
    return {"invoice": svc.invoice_dict(svc.issue_invoice(db, p.actor, a["invoice_id"]), base)}


def _t_paid(db, p, a, base):
    return {"invoice": svc.invoice_dict(svc.mark_paid(db, p.actor, a["invoice_id"], a.get("amount")), base, with_items=False)}


def _t_summary(db, p, a, base):
    return svc.sales_summary(db, a.get("company"), a["month"])


ITEM = _obj({
    "name": {"type": "string"},
    "quantity": {"type": ["string", "number"], "description": "Domyślnie 1."},
    "unit": {"type": "string", "description": "Domyślnie szt."},
    "unit_price": {**MONEY, "description": "Cena jednostkowa: brutto (marża zawsze brutto) albo netto, gdy prices_gross=false."},
    "vat_rate": {"type": "string", "enum": VAT_RATES, "description": "Tylko faktura vat. Domyślnie 23."},
    "purchase_price": {**MONEY, "description": "Tylko marża: łączna cena nabycia pozycji."},
}, ["name", "unit_price"])

TOOLS = [
    Tool("list_companies", "Firmy", "Lista firm, w imieniu których wystawiamy faktury.", "read", _obj({}), _t_companies),
    Tool("search_contractors", "Szukaj kontrahentów",
         "Szuka kontrahentów (wspólnych dla obu firm) po nazwie, NIP, mieście, telefonie lub e-mailu.", "read",
         _obj({"query": {"type": "string"}, "limit": {"type": "integer", "default": 20}}), _t_search_contractors),
    Tool("create_contractor", "Nowy kontrahent", "Dodaje kontrahenta. Dla osoby prywatnej bez NIP.", "write",
         _obj({"name": {"type": "string"}, "nip": {"type": "string"}, "is_company": {"type": "boolean"},
               "street": {"type": "string"}, "postal_code": {"type": "string"}, "city": {"type": "string"},
               "email": {"type": "string"}, "phone": {"type": "string"}, "notes": {"type": "string"}}, ["name"]),
         _t_create_contractor, read_only=False),
    Tool("search_cars", "Szukaj aut",
         "Szuka aut po marce, modelu, VIN lub numerze rejestracyjnym (kilka słów = wszystkie muszą pasować).", "read",
         _obj({"company": COMPANY, "query": {"type": "string"},
               "status": {"type": "string", "enum": list(CAR_STATUSES)}, "limit": {"type": "integer", "default": 20}}),
         _t_search_cars),
    Tool("create_car", "Nowe auto", "Dodaje auto na plac. Cena nabycia jest podstawą marży.", "write",
         _obj({"company": COMPANY, "make": {"type": "string"}, "model": {"type": "string"}, "vin": {"type": "string"},
               "year": {"type": "integer"}, "mileage_km": {"type": "integer"}, "registration": {"type": "string"},
               "purchase_date": {"type": "string", "format": "date"}, "purchase_price": MONEY,
               "purchase_document": {"type": "string"}, "asking_price": MONEY, "notes": {"type": "string"}},
              ["company", "make"]), _t_create_car, read_only=False),
    Tool("list_invoices", "Lista faktur", "Faktury z filtrami. Bez pozycji; szczegóły w get_invoice.", "read",
         _obj({"company": COMPANY, "kind": {"type": "string", "enum": ["vat", "margin"]},
               "month": {"type": "string", "description": "RRRR-MM"},
               "status": {"type": "string", "enum": ["draft", "issued"]}, "unpaid_only": {"type": "boolean"},
               "query": {"type": "string", "description": "Numer, nabywca albo NIP."},
               "limit": {"type": "integer", "default": 30}}), _t_list_invoices),
    Tool("get_invoice", "Faktura", "Szczegóły faktury z pozycjami, marżą i linkiem do PDF.", "read",
         _obj({"invoice_id": {"type": "integer"}}, ["invoice_id"]), _t_get_invoice),
    Tool("create_invoice_draft", "Szkic faktury",
         "Tworzy SZKIC faktury (bez numeru). Nabywca: buyer.contractor_id albo buyer.name/nip/address. "
         "Sprzedaż auta: podaj car_id; bez items pozycja powstaje z danych auta, cena = price albo cena z karty auta, "
         "cena nabycia z karty auta. kind='margin' dla aut używanych w procedurze marży.", "write",
         _obj({
             "company": COMPANY,
             "kind": {"type": "string", "enum": ["vat", "margin"]},
             "buyer": _obj({"contractor_id": {"type": "integer"}, "name": {"type": "string"}, "nip": {"type": "string"},
                            "address": {"type": "string", "description": "Ulica, w drugiej linii kod i miasto."},
                            "save_as_contractor": {"type": "boolean"}}),
             "car_id": {"type": "integer"},
             "price": {**MONEY, "description": "Cena brutto auta, gdy nie podajesz items."},
             "items": {"type": "array", "items": ITEM},
             "prices_gross": {"type": "boolean", "default": True},
             "issue_date": {"type": "string", "format": "date"},
             "sale_date": {"type": "string", "format": "date"},
             "payment_method": {"type": "string", "enum": list(PAYMENT_METHODS)},
             "due_days": {"type": "integer"},
             "notes": {"type": "string"},
         }, ["company", "kind", "buyer"]), _t_create_draft, read_only=False),
    Tool("issue_invoice", "Wystaw fakturę",
         "Wystawia szkic: nadaje kolejny numer i blokuje zmiany; sprzedane auto dostaje status 'sold'. "
         "Nieodwracalne. Wywołuj tylko po wyraźnym potwierdzeniu użytkownika.", "issue",
         _obj({"invoice_id": {"type": "integer"}}, ["invoice_id"]), _t_issue, read_only=False, destructive=True),
    Tool("mark_invoice_paid", "Zapisz płatność",
         "Zapisuje zapłaconą kwotę na wystawionej fakturze (bez amount = cała kwota).", "write",
         _obj({"invoice_id": {"type": "integer"}, "amount": MONEY}, ["invoice_id"]), _t_paid, read_only=False),
    Tool("sales_summary", "Podsumowanie miesiąca",
         "Liczba faktur, sprzedaż netto/VAT/brutto, suma marży i VAT od marży w miesiącu.", "read",
         _obj({"company": COMPANY, "month": {"type": "string", "description": "RRRR-MM"}}, ["company", "month"]),
         _t_summary),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def tool_listing(principal: Principal) -> list[dict]:
    out = []
    for t in TOOLS:
        if t.scope not in principal.scopes:
            continue
        out.append({
            "name": t.name, "title": t.title, "description": t.description, "inputSchema": t.schema,
            "annotations": {"title": t.title, "readOnlyHint": t.read_only, "destructiveHint": t.destructive,
                            "idempotentHint": t.read_only, "openWorldHint": False},
        })
    return out


def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def call_tool(db: Session, principal: Principal, name: str, args: dict, base: str) -> dict:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return {"content": [{"type": "text", "text": f"Nieznane narzędzie {name}."}], "isError": True}
    try:
        principal.require(tool.scope)
        data = tool.handler(db, principal, args or {}, base)
    except (svc.ServiceError, PermissionError) as e:
        db.rollback()
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except (KeyError, TypeError, ValueError) as e:
        db.rollback()
        return {"content": [{"type": "text", "text": f"Błędne argumenty: {e}"}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}],
            "structuredContent": data, "isError": False}


def handle_message(db: Session, principal: Principal, msg: dict, base: str) -> dict | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method, id_ = msg["method"], msg.get("id")
    params = msg.get("params") or {}
    if id_ is None:  # powiadomienie, bez odpowiedzi
        return None
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(id_, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": tool_listing(principal)})
    if method == "tools/call":
        return _result(id_, call_tool(db, principal, params.get("name", ""), params.get("arguments") or {}, base))
    if method in ("resources/list", "prompts/list"):
        return _result(id_, {method.split("/")[0]: []})
    return _error(id_, -32601, f"Method not found: {method}")


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True  # serwery Claude nie wysylaja Origin
    return origin.rstrip("/") == base_url(request)


@router.post("/mcp")
async def mcp_post(request: Request, db: Session = Depends(get_db)):
    if not _origin_ok(request):
        return JSONResponse({"error": "forbidden origin"}, status_code=403)
    principal = authenticate(db, request)
    if principal is None:
        return unauthorized(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    base = base_url(request)
    if isinstance(body, list):
        replies = [r for r in (handle_message(db, principal, m, base) for m in body) if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = handle_message(db, principal, body, base)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)


@router.get("/mcp")
@router.delete("/mcp")
def mcp_other():
    # Bez strumienia SSE i bez sesji: wszystko idzie przez POST.
    return Response(status_code=405, headers={"Allow": "POST"})
