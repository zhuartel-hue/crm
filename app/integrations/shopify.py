"""Sklep Shopify: pobieranie zamowien (tylko odczyt) do Zamowien w Sygnavo.

Podlaczenie (od 2026 aplikacje tworzy sie w Shopify Dev Dashboard):
- Client ID + Client secret aplikacji zainstalowanej w sklepie -> token na 24 h (client credentials grant),
  odnawiany sam przed wygasnieciem; albo
- stary token Admin API (shpat_...) z aplikacji utworzonej kiedys w panelu sklepu.
Uprawnienia aplikacji: read_orders, read_products (i read_customers dla danych kupujacego).
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import Integration
from . import http
from .common import (ExtItem, ExtOrder, IntegrationError, dec, iso, link_product, now, parse_time, secrets_of,
                     since, store_secrets, upsert_order)

API_VERSION = "2026-07"
PAGE = 50
MAX_PAGES = 40

ORDERS_QUERY = """
query($q: String!, $after: String) {
  orders(first: %d, after: $after, query: $q, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name createdAt updatedAt cancelledAt displayFinancialStatus displayFulfillmentStatus
      email phone note taxesIncluded
      customer { displayName email phone }
      billingAddress { name company address1 address2 zip city countryCodeV2 phone }
      shippingAddress { name company address1 address2 zip city countryCodeV2 phone }
      shippingLine { title code discountedPriceSet { shopMoney { amount } } }
      lineItems(first: 100) {
        nodes {
          name sku quantity
          discountedUnitPriceAfterAllDiscountsSet { shopMoney { amount } }
          taxLines { rate }
          variant { id }
        }
      }
    }
  }
}
""" % PAGE

PRODUCTS_QUERY = """
query($after: String) {
  productVariants(first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { id sku title price inventoryQuantity product { title status } }
  }
}
"""

FINANCIAL = {"PAID": "paid", "PENDING": "unpaid", "AUTHORIZED": "unpaid", "PARTIALLY_PAID": "partial",
             "PARTIALLY_REFUNDED": "partial", "REFUNDED": "refunded", "VOIDED": "unpaid", "EXPIRED": "unpaid"}


def normalize_shop(shop: str) -> str:
    s = (shop or "").strip().lower()
    s = re.sub(r"^https?://", "", s).split("/")[0]
    if s and "." not in s:
        s += ".myshopify.com"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", s):
        raise IntegrationError("Podaj adres sklepu w postaci nazwa.myshopify.com (jest w panelu Shopify: "
                               "Ustawienia → Domeny).")
    return s


def _token(db: Session, integ: Integration) -> str:
    sec = secrets_of(integ)
    if sec.get("admin_token"):
        return sec["admin_token"]
    if not (sec.get("client_id") and sec.get("client_secret")):
        raise IntegrationError("Brak kluczy sklepu. Wpisz Client ID i Client secret.")
    exp = sec.get("expires_at") or ""
    if sec.get("access_token") and exp and exp > iso(now() + dt.timedelta(minutes=5)):
        return sec["access_token"]
    shop = integ.settings.get("shop") or ""
    r = http.request("POST", f"https://{shop}/admin/oauth/access_token",
                     data={"grant_type": "client_credentials", "client_id": sec["client_id"],
                           "client_secret": sec["client_secret"]},
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code != 200:
        txt = r.text[:300]
        if "shop_not_permitted" in txt:
            raise IntegrationError("Shopify nie pozwala na to połączenie: aplikacja i sklep muszą być w tej samej "
                                   "organizacji w Dev Dashboard. Zobacz instrukcję.")
        raise IntegrationError(f"Shopify odrzucił klucze (HTTP {r.status_code}). Sprawdź Client ID i secret.")
    body = r.json()
    sec["access_token"] = body.get("access_token") or ""
    sec["expires_at"] = iso(now() + dt.timedelta(seconds=int(body.get("expires_in") or 86399)))
    store_secrets(integ, sec)
    db.flush()
    return sec["access_token"]


def gql(db: Session, integ: Integration, query: str, variables: dict) -> dict:
    shop = integ.settings.get("shop") or ""
    r = http.request("POST", f"https://{shop}/admin/api/{API_VERSION}/graphql.json",
                     json={"query": query, "variables": variables},
                     headers={"X-Shopify-Access-Token": _token(db, integ), "Content-Type": "application/json"})
    if r.status_code == 401:
        raise IntegrationError("Shopify: token nieważny albo aplikacja odinstalowana ze sklepu.")
    if r.status_code != 200:
        raise IntegrationError(f"Shopify: błąd HTTP {r.status_code}.")
    body = r.json()
    if body.get("errors"):
        msg = "; ".join(str(e.get("message", e)) for e in body["errors"])[:300]
        if "access" in msg.lower() and "denied" in msg.lower():
            msg += " (dodaj w aplikacji uprawnienia read_orders i read_products, potem zainstaluj ponownie)"
        raise IntegrationError(f"Shopify: {msg}")
    return body.get("data") or {}


def _addr(a: dict | None) -> list[str]:
    if not a:
        return []
    city = f"{a.get('zip') or ''} {a.get('city') or ''}".strip()
    country = a.get("countryCodeV2") or ""
    return [x for x in (a.get("company") or "", a.get("name") or "",
                        " ".join(x for x in (a.get("address1"), a.get("address2")) if x),
                        city + (f", {country}" if country and country != "PL" else "")) if x]


def _rate(tax_lines: list[dict]) -> str:
    total = sum((dec(t.get("rate")) for t in tax_lines or []), Decimal("0"))
    if total <= 0:
        return ""
    pct = (total * 100).quantize(Decimal("1"))
    return str(pct) if str(pct) in ("23", "8", "5", "0") else ""


def parse_order(n: dict) -> ExtOrder:
    cust = n.get("customer") or {}
    bill, ship = n.get("billingAddress") or {}, n.get("shippingAddress") or {}
    items = []
    for li in (n.get("lineItems") or {}).get("nodes") or []:
        price = dec(((li.get("discountedUnitPriceAfterAllDiscountsSet") or {}).get("shopMoney") or {}).get("amount"))
        items.append(ExtItem(name=li.get("name") or "Pozycja", quantity=dec(li.get("quantity"), Decimal("1")),
                             unit_gross=price, sku=li.get("sku") or "",
                             external_id=((li.get("variant") or {}).get("id") or ""), vat_rate=_rate(li.get("taxLines"))))
    sl = n.get("shippingLine") or {}
    cancelled = bool(n.get("cancelledAt"))
    status = (n.get("displayFulfillmentStatus") or "").lower()
    return ExtOrder(
        external_id=n["id"], number=n.get("name") or "", created=parse_time(n.get("createdAt")) or now(),
        updated=parse_time(n.get("updatedAt")), cancelled=cancelled,
        external_status="anulowane" if cancelled else status,
        payment_status=FINANCIAL.get(n.get("displayFinancialStatus") or "", ""),
        buyer_name=bill.get("name") or cust.get("displayName") or ship.get("name") or "",
        buyer_company=bill.get("company") or "", email=n.get("email") or cust.get("email") or "",
        phone=n.get("phone") or bill.get("phone") or ship.get("phone") or cust.get("phone") or "",
        billing_lines=_addr(bill), delivery_lines=_addr(ship), shipping_method=sl.get("title") or "",
        shipping_gross=dec(((sl.get("discountedPriceSet") or {}).get("shopMoney") or {}).get("amount")),
        note=n.get("note") or "", items=items)


def sync(db: Session, integ: Integration) -> dict:
    """Pobiera zamowienia zmienione od ostatniego razu. Zwraca liczniki."""
    start = since(integ)
    q = f"updated_at:>='{iso(start)}'"
    after, newest, created, updated = None, None, 0, 0
    for _ in range(MAX_PAGES):
        data = gql(db, integ, ORDERS_QUERY, {"q": q, "after": after})
        conn = data.get("orders") or {}
        for n in conn.get("nodes") or []:
            e = parse_order(n)
            _, is_new = upsert_order(db, integ, e)
            created += is_new
            updated += not is_new
            if e.updated and (newest is None or e.updated > newest):
                newest = e.updated
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
    if newest is not None:
        integ.cursor = iso(newest)
    elif not integ.cursor:
        integ.cursor = iso(start)
    return {"new": created, "updated": updated}


def sync_products(db: Session, integ: Integration) -> int:
    """Warianty ze sklepu -> powiazania z produktami w magazynie (po SKU = kod produktu)."""
    from sqlalchemy import select

    from ..models import Product
    after, n = None, 0
    for _ in range(MAX_PAGES):
        data = gql(db, integ, PRODUCTS_QUERY, {"after": after})
        conn = data.get("productVariants") or {}
        for v in conn.get("nodes") or []:
            sku = (v.get("sku") or "").strip()
            title = (v.get("product") or {}).get("title") or ""
            if v.get("title") and v["title"] != "Default Title":
                title = f"{title} · {v['title']}"
            p = db.scalar(select(Product).where(Product.company_id == integ.company_id, Product.code == sku)
                          .limit(1)) if sku else None
            link_product(db, integ, v["id"], p, sku=sku, name=title, stock=v.get("inventoryQuantity"),
                         price=v.get("price"), status=((v.get("product") or {}).get("status") or "").lower())
            n += 1
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
    return n
