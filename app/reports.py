"""Raporty dla ksiegowej: sprzedaz i koszty za okres (miesiac, kwartal, dowolny zakres), per firma.

Sprzedaz: wystawione faktury bez proform (proforma nie jest faktura VAT), korekty osobno. Zestawienie wg stawek
liczy calc.invoice_summary, czyli tak samo jak na fakturze: korekta = roznica po - przed, koncowa = zamowienie minus
zaliczki (wiec zaliczki i koncowa sie nie dubluja). Koszty: faktury kosztowe bez odrzuconych.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import calc
from .ksef import incoming
from .models import PURCHASE_STATUSES, Invoice, PurchaseInvoice

ZERO = Decimal("0")
KIND_SHORT = {"vat": "VAT", "advance": "Zaliczkowa", "final": "Końcowa", "correction": "Korekta"}
DATE_BASIS = {"issue": "data wystawienia", "sale": "data sprzedaży"}
COST_SCOPES = {"all": "zweryfikowane i do weryfikacji", "accepted": "tylko zweryfikowane", "new": "tylko do weryfikacji"}

MONTHS = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień", "październik",
          "listopad", "grudzień"]


def month_range(d: dt.date) -> tuple[dt.date, dt.date]:
    start = d.replace(day=1)
    nxt = dt.date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, nxt - dt.timedelta(days=1)


def presets(today: dt.date) -> list[tuple[str, dt.date, dt.date]]:
    this_m = month_range(today)
    prev_m = month_range(this_m[0] - dt.timedelta(days=1))
    q = (today.month - 1) // 3
    q_start = dt.date(today.year, q * 3 + 1, 1)
    q_end = month_range(dt.date(today.year, q * 3 + 3, 1))[1]
    pq_end = q_start - dt.timedelta(days=1)
    pq_start = dt.date(pq_end.year, (pq_end.month - 1) // 3 * 3 + 1, 1)
    return [("Ten miesiąc", *this_m), ("Poprzedni miesiąc", *prev_m), (f"{q + 1}. kwartał", q_start, q_end),
            ("Poprzedni kwartał", pq_start, pq_end), (f"Rok {today.year}", dt.date(today.year, 1, 1), dt.date(today.year, 12, 31))]


def parse_period(od: str | None, do: str | None, today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """Domyslnie biezacy miesiac; zamienia kolejnosc, gdy od > do."""
    today = today or dt.date.today()
    try:
        start = dt.date.fromisoformat(od) if od else None
        end = dt.date.fromisoformat(do) if do else None
    except ValueError:
        start = end = None
    if start is None and end is None:
        return month_range(today)
    start = start or (end.replace(day=1) if end else today)
    end = end or month_range(start)[1]
    return (end, start) if start > end else (start, end)


def period_label(start: dt.date, end: dt.date) -> str:
    if start.day == 1 and month_range(start) == (start, end):
        return f"{MONTHS[start.month - 1]} {start.year}"
    return f"{start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}"


def _add(target: dict, rate: str, net, vat, gross) -> None:
    s = target.setdefault(rate, {"rate": rate, "net": ZERO, "vat": ZERO, "gross": ZERO})
    s["net"] += net
    s["vat"] += vat
    s["gross"] += gross


def _rates_sorted(d: dict) -> list[dict]:
    return calc._sort_rates(list(d.values()))


def _paid_state(inv: Invoice) -> str:
    if inv.total_gross <= 0:
        return "—"
    if inv.paid >= inv.total_gross:
        return "zapłacona"
    return "częściowo" if inv.paid > 0 else "nie"


def sales(db: Session, company_id: int, start: dt.date, end: dt.date, basis: str = "issue") -> dict:
    col = Invoice.sale_date if basis == "sale" else Invoice.issue_date
    rows = db.scalars(
        select(Invoice).options(selectinload(Invoice.items), selectinload(Invoice.advances).selectinload(Invoice.items))
        .where(Invoice.company_id == company_id, Invoice.status == "issued", Invoice.kind != "proforma",
               col >= start, col <= end)
        .order_by(col, Invoice.seq.nulls_last(), Invoice.id)).all()
    sections = {"invoices": [], "corrections": []}
    rates = {"invoices": {}, "corrections": {}, "total": {}}
    totals = {k: {"net": ZERO, "vat": ZERO, "gross": ZERO, "paid": ZERO, "unpaid": ZERO, "count": 0} for k in ("invoices", "corrections", "total")}
    for inv in rows:
        key = "corrections" if inv.kind == "correction" else "invoices"
        summary = calc.invoice_summary(inv)
        if not summary and (inv.total_gross or inv.total_net):  # np. stare faktury bez pozycji
            summary = [{"rate": "—", "net": inv.total_net, "vat": inv.total_vat, "gross": inv.total_gross}]
        for r in summary:
            _add(rates[key], r["rate"], r["net"], r["vat"], r["gross"])
            _add(rates["total"], r["rate"], r["net"], r["vat"], r["gross"])
        sections[key].append(dict(
            inv=inv, number=inv.number or "", kind=KIND_SHORT.get(inv.kind, inv.kind), issue_date=inv.issue_date,
            sale_date=inv.sale_date, buyer=inv.buyer_name, nip=inv.buyer_nip, net=inv.total_net, vat=inv.total_vat,
            gross=inv.total_gross, paid=inv.paid, paid_state=_paid_state(inv), ksef=inv.ksef_number,
            related=inv.related.number if inv.kind == "correction" and inv.related else "",
        ))
        for k in (key, "total"):
            t = totals[k]
            t["net"] += inv.total_net
            t["vat"] += inv.total_vat
            t["gross"] += inv.total_gross
            t["paid"] += min(inv.paid, inv.total_gross) if inv.total_gross > 0 else ZERO
            t["unpaid"] += max(inv.total_gross - inv.paid, ZERO)
            t["count"] += 1
    return dict(sections=sections, rates={k: _rates_sorted(v) for k, v in rates.items()}, totals=totals,
                start=start, end=end, basis=basis, label=period_label(start, end))


def costs(db: Session, company_id: int, start: dt.date, end: dt.date, scope: str = "all") -> dict:
    stmt = select(PurchaseInvoice).where(PurchaseInvoice.company_id == company_id, PurchaseInvoice.issue_date >= start,
                                         PurchaseInvoice.issue_date <= end)
    if scope in PURCHASE_STATUSES:
        stmt = stmt.where(PurchaseInvoice.status == scope)
    else:
        stmt = stmt.where(PurchaseInvoice.status != "rejected")
    rows = db.scalars(stmt.order_by(PurchaseInvoice.issue_date, PurchaseInvoice.id)).all()
    by_cur: dict[str, dict] = {}
    rates: dict[str, dict] = {}
    items = []
    for p in rows:
        t = by_cur.setdefault(p.currency, {"currency": p.currency, "net": ZERO, "vat": ZERO, "gross": ZERO, "count": 0})
        t["net"] += p.net
        t["vat"] += p.vat
        t["gross"] += p.gross
        t["count"] += 1
        if p.currency == "PLN":
            parsed = []
            if p.xml:
                try:
                    parsed = incoming.parse(p.xml).get("rates") or []
                except Exception:  # noqa: BLE001
                    parsed = []
            for r in parsed or [{"rate": "—", "net": p.net, "vat": p.vat, "gross": p.gross}]:
                _add(rates, r["rate"], r["net"], r["vat"], r["gross"])
        items.append(dict(pi=p, number=p.invoice_number or p.ksef_number, issue_date=p.issue_date, seller=p.seller_name,
                          nip=p.seller_nip, net=p.net, vat=p.vat, gross=p.gross, currency=p.currency,
                          status=p.status_label, paid_at=p.paid_at, ksef=p.ksef_number if p.in_ksef else ""))
    return dict(rows=items, totals=sorted(by_cur.values(), key=lambda t: (t["currency"] != "PLN", t["currency"])),
                rates=calc._sort_rates(list(rates.values())), start=start, end=end, scope=scope,
                label=period_label(start, end))


def _num(v) -> str:
    return f"{calc.money(v):.2f}".replace(".", ",") if v is not None else ""


def _d(v) -> str:
    return v.strftime("%Y-%m-%d") if v else ""


def _csv(header: list[str], lines: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(header)
    w.writerows(lines)
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM: Excel otwiera polskie znaki poprawnie


def sales_csv(rep: dict) -> bytes:
    lines = []
    for key, label in (("invoices", "faktura"), ("corrections", "korekta")):
        for r in rep["sections"][key]:
            lines.append([r["number"], r["kind"], _d(r["issue_date"]), _d(r["sale_date"]), r["buyer"], r["nip"],
                          _num(r["net"]), _num(r["vat"]), _num(r["gross"]), _num(r["paid"]), r["paid_state"], r["ksef"],
                          r["related"]])
    return _csv(["Numer", "Rodzaj", "Data wystawienia", "Data sprzedaży", "Nabywca", "NIP", "Netto", "VAT", "Brutto",
                 "Zapłacono", "Status zapłaty", "Numer KSeF", "Korygowana faktura"], lines)


def costs_csv(rep: dict) -> bytes:
    lines = [[r["number"], _d(r["issue_date"]), r["seller"], r["nip"], _num(r["net"]), _num(r["vat"]), _num(r["gross"]),
              r["currency"], r["status"], _d(r["paid_at"]), r["ksef"]] for r in rep["rows"]]
    return _csv(["Numer", "Data wystawienia", "Sprzedawca", "NIP", "Netto", "VAT", "Brutto", "Waluta", "Status",
                 "Zapłacona", "Numer KSeF"], lines)
