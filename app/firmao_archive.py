"""Przeniesienie archiwum z Firmao: faktury sprzedazy i zakupu z pozycjami, oferty, WZ/PZ.

Wejscie: slownik list rekordow z eksportu API (tools/firmao_export.py, odchudzony przez tools/firmao_bundle.py):
customers, contacts, products, transactions, transactionentries, storagedocs, offers.
Dokumenty dostaja numery i kwoty z Firmao (bez przeliczania), source_ref="firmao:<id>", wiec ponowny import
aktualizuje zamiast dublowac. Faktury nie sa wysylane do KSeF (status "accepted" z numerem KSeF z Firmao,
a bez numeru "external"). Stany magazynowe biora sie z produktow, WZ/PZ ich nie zmieniaja.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import firmao_import, numbering
from .models import (VAT_RATES, Company, Contractor, Delivery, DeliveryItem, Invoice, InvoiceItem, NumberSequence,
                     Offer, OfferItem, Product, PurchaseInvoice)

INVOICE_TYPES = {"INVOICE": "vat", "VAT": "vat", "VAT_INVOICE": "vat", "CORRECTION": "correction",
                 "PROFORMA": "proforma", "PRO_FORMA": "proforma", "ADVANCE": "advance", "ADVANCE_INVOICE": "advance",
                 "PREPAYMENT": "advance", "PREPAYMENT_INVOICE": "advance", "FINAL": "final", "FINAL_INVOICE": "final"}
PAYMENT_TYPES = {"TRANSFER": "transfer", "CASH": "cash", "CARD": "card", "PAYMENT_CARD": "card",
                 "COMPENSATION": "compensation", "COD": "cash", "PREPAY": "transfer", "INTERNET": "transfer",
                 "OTHER": "transfer"}
UNITS = {"szt": "szt.", "szt.": "szt.", "sztuka": "szt.", "sztuk": "szt.", "sztuki": "szt.", "godz": "godz.",
         "godz.": "godz.", "h": "godz.", "kpl": "kpl.", "kpl.": "kpl.", "l": "l", "km": "km", "m": "m", "kg": "kg",
         "usł": "usł.", "usł.": "usł.", "usługa": "usł."}
OFFER_STATUS = {"NEW": "new", "DRAFT": "new", "SENT": "sent", "ACCEPTED": "accepted", "REJECTED": "rejected",
                "IN_PROGRESS": "in_progress", "IN_REALIZATION": "in_progress", "REALIZATION": "in_progress",
                "REALIZED": "done", "EXECUTED": "done", "COMPLETED": "done", "DONE": "done", "FINISHED": "done",
                "CANCELED": "cancelled", "CANCELLED": "cancelled", "WAITING_FOR_PAYMENT": "accepted",
                "DURING_CALCULATIONS": "in_progress"}
STORAGE_TYPES = {"OUTSIDE_RELEASE": "wz", "OUTSIDE_INCOME": "pz", "OUTSIDE_RECEIPT": "pz", "OUTSIDE_ADMISSION": "pz"}
ZERO = Decimal("0")


def _d(v, default: Decimal = ZERO) -> Decimal:
    if v is None or v == "":
        return default
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return default


def _q(v) -> Decimal:
    try:
        return Decimal(str(v if v not in (None, "") else 1)).quantize(Decimal("0.001"))
    except InvalidOperation:
        return Decimal("1")


def _date(v) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _id(v):
    return v.get("id") if isinstance(v, dict) else v


def _addr(a: dict | None) -> str:
    street, postal, city, _ = firmao_import._address(a)
    return "\n".join(x for x in (street, f"{postal} {city}".strip()) if x)


def _vat(v) -> str:
    return firmao_import._vat(v)


def _unit(v) -> str:
    u = str(v or "").strip()
    return UNITS.get(u.lower(), u)[:10] or "szt."


def _paid_status(t: dict) -> bool:
    return str(t.get("paymentStatus") or "").upper().startswith("PAID")


def _neg(v) -> Decimal:
    return -_d(v)


def _invoice_items(ctx: "Ctx", rows: list[dict], gross_prices: bool, correction: bool) -> list[InvoiceItem]:
    """Pozycje faktury. W korekcie Firmao daje pary: pozycja 'przed' (baseEntryId, wartosci ujemne - odwrocenie
    stanu z faktury) i 'po' (invalidEntry wskazuje na 'przed'). W Sygnavo 'przed' to kopia stanu z faktury
    (wartosci dodatnie, is_before=True), a 'po' to nowy stan."""
    before, after = [], []
    for e in rows:
        is_before = correction and not e.get("invalidEntry") and bool(e.get("baseEntryId"))
        price = e.get("unitBruttoPrice") if gross_prices else e.get("unitNettoPrice")
        f = _neg if is_before else _d
        item = InvoiceItem(name=(e.get("name") or "Pozycja")[:500],
                           quantity=-_q(e.get("quantity")) if is_before else _q(e.get("quantity")),
                           unit=_unit(e.get("unit")), vat_rate=_vat(e.get("vatPercent")), unit_price=f(price),
                           net=f(e.get("nettoPrice")), vat=f(e.get("vatPrice")), gross=f(e.get("bruttoPrice")),
                           product_id=ctx.product(e), is_before=is_before)
        if is_before:
            item.unit_price = abs(item.unit_price)
            before.append(item)
        else:
            after.append(item)
    out = before + after
    for pos, item in enumerate(before, start=1):
        item.position = pos
    for pos, item in enumerate(after, start=1):
        item.position = pos
    return out


class Ctx:
    def __init__(self, db: Session, company: Company):
        self.db, self.company = db, company
        self.contractors = {c.source_ref: c.id for c in db.scalars(select(Contractor).where(Contractor.source_ref != ""))}
        self.products = {p.source_ref: p.id for p in db.scalars(select(Product).where(Product.source_ref != ""))}

    def contractor(self, rec: dict) -> int | None:
        cid = _id(rec.get("customer"))
        return self.contractors.get(f"firmao:{cid}") if cid else None

    def product(self, entry: dict) -> int | None:
        pid = _id(entry.get("product"))
        return self.products.get(f"firmao:{pid}") if pid else None


def group_entries(entries: list[dict]) -> dict[tuple[str, int], list[dict]]:
    """Pozycje wg dokumentu: ("transaction"|"storageDoc"|"offer"|"agreement", id)."""
    out: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for e in entries:
        for key in ("transaction", "storageDoc", "offer", "agreement"):
            ref = _id(e.get(key))
            if ref:
                out[(key, int(ref))].append(e)
                break
    for rows in out.values():
        rows.sort(key=lambda e: (e.get("entryOrder") or 0, e.get("id") or 0))
    return out


def _existing(db: Session, model, company_id: int) -> dict[str, object]:
    return {o.source_ref: o for o in db.scalars(select(model).where(model.company_id == company_id,
                                                                   model.source_ref != ""))}


def _free_number(taken: set[str], number: str) -> str:
    if number not in taken:
        return number
    k = 2
    while f"{number} ({k})" in taken:
        k += 1
    return f"{number} ({k})"


def import_invoices(ctx: Ctx, transactions: list[dict], entries: dict) -> dict:
    db, company = ctx.db, ctx.company
    have = _existing(db, Invoice, company.id)
    taken = set(db.scalars(select(Invoice.number).where(Invoice.company_id == company.id, Invoice.number.is_not(None))))
    out = {"added": 0, "updated": 0, "skipped": 0, "renamed": 0, "skipped_types": defaultdict(int)}
    by_firmao: dict[int, Invoice] = {}
    corrections: list[tuple[Invoice, dict]] = []
    for t in transactions:
        if t.get("mode") != "SALE" or t.get("deleted"):
            continue
        kind = INVOICE_TYPES.get(str(t.get("type") or "").upper())
        if kind is None:
            out["skipped_types"][str(t.get("type"))] += 1
            out["skipped"] += 1
            continue
        ref = f"firmao:{t['id']}"
        inv = have.get(ref)
        number = (t.get("transactionNumber") or t.get("documentNumber") or f"FIRMAO-{t['id']}")[:60]
        if inv is None:
            if number in taken:
                number = _free_number(taken, number)
                out["renamed"] += 1
            inv = Invoice(company_id=company.id, source_ref=ref, number=number)
            db.add(inv)
            taken.add(number)
            out["added"] += 1
        else:
            out["updated"] += 1
            inv.items.clear()
        issue = _date(t.get("invoiceDate")) or _date(t.get("documentDate")) or _date(t.get("transactionDate")) \
            or dt.date.today()
        gross, rest = _d(t.get("transactionBruttoPrice")), _d(t.get("restOfPaidValue"))
        inv.kind, inv.status = kind, "issued"
        inv.issue_date, inv.sale_date = issue, _date(t.get("transactionDate")) or issue
        inv.due_date = _date(t.get("paymentDate"))
        inv.issue_place = inv.issue_place or company.issue_place or ""
        inv.payment_method = PAYMENT_TYPES.get(str(t.get("paymentType") or "").upper(), "transfer")
        inv.prices_gross = bool(t.get("calculateFromGross"))
        inv.contractor_id = ctx.contractor(t)
        inv.buyer_name = (t.get("customerName") or (t.get("customer") or {}).get("label") or "")[:300]
        inv.buyer_nip = re.sub(r"[^0-9A-Za-z]", "", str(t.get("nipNumber") or ""))[:20]
        inv.buyer_address = _addr(t.get("customerAddress"))
        notes = [x for x in (t.get("invoiceAnnotations"), t.get("description")) if x]
        inv.notes = "\n".join(str(x) for x in notes)
        inv.correction_reason = (t.get("correctionCause") or "") if kind == "correction" else ""
        inv.total_net, inv.total_vat, inv.total_gross = _d(t.get("transactionNettoPrice")), \
            _d(t.get("transactionVatPrice")), gross
        if _paid_status(t):
            inv.paid = gross
        elif t.get("restOfPaidValue") is None:
            inv.paid = ZERO if str(t.get("paymentStatus") or "").upper() == "NOT_PAID" else gross
        else:
            inv.paid = gross - rest
        inv.split_payment = bool((t.get("additionalProperties") or {}).get("splitPayment"))
        ks = t.get("ksefUploadDetails") or {}
        if ks.get("ksefNumber"):
            inv.ksef_status, inv.ksef_number, inv.ksef_env = "accepted", ks["ksefNumber"][:40], "prod"
        else:
            inv.ksef_status = "external"
        inv.seq = None
        inv.items.extend(_invoice_items(ctx, entries.get(("transaction", int(t["id"])), []), inv.prices_gross,
                                        kind == "correction"))
        by_firmao[int(t["id"])] = inv
        if kind == "correction":
            corrections.append((inv, t))
    db.flush()
    for inv, t in corrections:
        for c in t.get("correctedDocumentsInfo") or []:
            target = by_firmao.get(int(c.get("id") or 0))
            if target is None:
                target = db.scalar(select(Invoice).where(Invoice.source_ref == f"firmao:{c.get('id')}"))
            if target is not None:
                inv.related_id = target.id
                break
    out["skipped_types"] = dict(out["skipped_types"])
    out["_map"] = {k: v.id for k, v in by_firmao.items()}
    return out


def import_purchases(ctx: Ctx, transactions: list[dict]) -> dict:
    db, company = ctx.db, ctx.company
    have = _existing(db, PurchaseInvoice, company.id)
    by_ksef = {p.ksef_number: p for p in db.scalars(select(PurchaseInvoice).where(PurchaseInvoice.company_id == company.id))}
    out = {"added": 0, "updated": 0}
    for t in transactions:
        if t.get("mode") != "PURCHASE" or t.get("deleted"):
            continue
        ref = f"firmao:{t['id']}"
        ks = (t.get("ksefUploadDetails") or {}).get("ksefNumber") or ""
        p = have.get(ref) or (by_ksef.get(ks) if ks else None)
        if p is None:
            p = PurchaseInvoice(company_id=company.id, ksef_number=(ks or f"FIRMAO-{t['id']}")[:40])
            db.add(p)
            out["added"] += 1
        else:
            out["updated"] += 1
        p.source_ref = ref
        p.ksef_env = "prod" if ks else ""
        p.invoice_number = (t.get("transactionNumber") or t.get("documentNumber") or "")[:256]
        p.invoice_type = {"CORRECTION": "Kor", "ADVANCE_INVOICE": "Zal"}.get(str(t.get("type")).upper(), "Vat")
        p.issue_date = _date(t.get("invoiceDate")) or _date(t.get("documentDate")) or _date(t.get("transactionDate"))
        p.seller_nip = re.sub(r"\D", "", str(t.get("nipNumber") or ""))[:20]
        p.seller_name = (t.get("customerName") or "")[:512]
        p.contractor_id = ctx.contractor(t)
        p.net, p.vat, p.gross = _d(t.get("transactionNettoPrice")), _d(t.get("transactionVatPrice")), \
            _d(t.get("transactionBruttoPrice"))
        p.currency = (t.get("currency") or "PLN")[:3]
        p.due_date = _date(t.get("paymentDate"))
        p.bank_account = re.sub(r"\s", "", str(t.get("sellerBankAccountNumber") or ""))[:40]
        p.status = "accepted"
        p.paid_at = (_date(t.get("actualPaymentDate")) or p.due_date or p.issue_date) \
            if _paid_status(t) else None
        p.notes = (t.get("description") or "")
        if ks:
            by_ksef[ks] = p
    db.flush()
    return out


def import_deliveries(ctx: Ctx, docs: list[dict], entries: dict, invoices: dict[int, int]) -> dict:
    db, company = ctx.db, ctx.company
    have = _existing(db, Delivery, company.id)
    taken = set(db.scalars(select(Delivery.number).where(Delivery.company_id == company.id, Delivery.number.is_not(None))))
    out = {"added": 0, "updated": 0, "skipped": 0, "renamed": 0, "skipped_types": defaultdict(int)}
    by_firmao: dict[int, Delivery] = {}
    for s in docs:
        kind = STORAGE_TYPES.get(str(s.get("type") or "").upper())
        if kind is None or s.get("deleted"):
            out["skipped_types"][str(s.get("type"))] += 1
            out["skipped"] += 1
            continue
        ref = f"firmao:{s['id']}"
        d = have.get(ref)
        number = (s.get("storageDocNumber") or s.get("documentNumber") or f"FIRMAO-{s['id']}")[:60]
        if d is None:
            if number in taken:
                number = _free_number(taken, number)
                out["renamed"] += 1
            d = Delivery(company_id=company.id, source_ref=ref, number=number, signed_by="")
            db.add(d)
            taken.add(number)
            out["added"] += 1
        else:
            out["updated"] += 1
            d.items.clear()
        d.kind, d.status = kind, "issued"
        d.issue_date = _date(s.get("storagedocDate")) or _date(s.get("documentDate")) or dt.date.today()
        d.contractor_id = ctx.contractor(s)
        d.recipient_name = (s.get("customerName") or (s.get("customer") or {}).get("label") or "")[:300]
        d.recipient_address = _addr(s.get("customerAddress"))
        d.notes = "\n".join(str(x) for x in (s.get("name"), s.get("description")) if x)
        d.prices_gross = bool(s.get("calculateFromGross"))
        d.total_net, d.total_vat, d.total_gross = _d(s.get("storageDocNettoPrice")), _d(s.get("storageDocVatPrice")), \
            _d(s.get("storageDocBruttoPrice"))
        props = s.get("additionalProperties") or {}
        person = s.get("receivingPersonObject") or {}
        d.signed_by = (s.get("receivingPerson") or " ".join(x for x in (person.get("firstName"), person.get("lastName")) if x)
                       or "")[:200] if isinstance(s.get("receivingPerson") or "", str) else ""
        signed = props.get("signatureDate")
        d.signed_at = dt.datetime.fromisoformat(str(signed)[:19]) if signed else None
        for pos, e in enumerate(entries.get(("storageDoc", int(s["id"])), []), start=1):
            price = e.get("unitBruttoPrice") if d.prices_gross else e.get("unitNettoPrice")
            d.items.append(DeliveryItem(
                position=pos, product_id=ctx.product(e), code=(e.get("productCode") or "")[:60],
                name=(e.get("name") or "Pozycja")[:500], quantity=_q(e.get("quantity")),
                unit=_unit(e.get("unit")), unit_price=_d(price) if price is not None else None,
                vat_rate=_vat(e.get("vatPercent")), net=_d(e.get("nettoPrice")), vat=_d(e.get("vatPrice")),
                gross=_d(e.get("bruttoPrice"))))
        by_firmao[int(s["id"])] = d
    db.flush()
    out["skipped_types"] = dict(out["skipped_types"])
    out["_map"] = {k: v.id for k, v in by_firmao.items()}
    return out


def import_offers(ctx: Ctx, offers: list[dict], entries: dict) -> dict:
    db, company = ctx.db, ctx.company
    have = _existing(db, Offer, company.id)
    taken = set(db.scalars(select(Offer.number).where(Offer.company_id == company.id, Offer.number.is_not(None))))
    out = {"added": 0, "updated": 0, "skipped": 0, "renamed": 0, "skipped_types": defaultdict(int)}
    by_firmao: dict[int, Offer] = {}
    for o in offers:
        if str(o.get("type") or "OFFER").upper() != "OFFER" or o.get("deleted"):
            out["skipped_types"][str(o.get("type"))] += 1
            out["skipped"] += 1
            continue
        ref = f"firmao:{o['id']}"
        of = have.get(ref)
        number = (o.get("number") or o.get("documentNumber") or f"FIRMAO-{o['id']}")[:60]
        if of is None:
            if number in taken:
                number = _free_number(taken, number)
                out["renamed"] += 1
            of = Offer(company_id=company.id, source_ref=ref, number=number, created_by="Firmao")
            db.add(of)
            taken.add(number)
            out["added"] += 1
        else:
            out["updated"] += 1
            of.items.clear()
        status = (o.get("offerStatus") or {}).get("key") if isinstance(o.get("offerStatus"), dict) else o.get("offerStatus")
        of.status = OFFER_STATUS.get(str(status or "").upper(), "new")
        of.issue_date = _date(o.get("offerDate")) or _date(o.get("documentDate")) or dt.date.today()
        of.valid_until = _date(o.get("paymentDate"))
        if of.valid_until and of.valid_until < of.issue_date:
            of.valid_until = None
        prob = o.get("acceptProbability")
        of.probability = int(prob) if isinstance(prob, (int, float)) and 0 <= prob <= 100 else None
        of.title = (o.get("name") or "")[:300]
        of.contractor_id = ctx.contractor(o)
        of.recipient_name = (o.get("customerName") or (o.get("customer") or {}).get("label") or "")[:300]
        of.recipient_address = _addr(o.get("customerAddress"))
        contact = o.get("contact") or {}
        of.contact_person = (contact.get("label") or "")[:200]
        emails = (o.get("customer") or {}).get("emails") or contact.get("emails") or []
        of.email = (o.get("recipientEmailAddress") or (emails[0] if emails else "") or "")[:200]
        of.prices_gross = bool(o.get("calculateFromGross"))
        of.total_net, of.total_vat, of.total_gross = _d(o.get("nettoPrice") or o.get("baseNettoPrice")), \
            _d(o.get("vatPrice") or o.get("baseVatPrice")), _d(o.get("bruttoPrice") or o.get("baseBruttoPrice"))
        of.notes = "\n".join(str(x) for x in (o.get("introductoryNote"), o.get("annotations")) if x)
        of.internal_notes = str(o.get("description") or "")
        mail = o.get("mail") or {}
        if mail.get("status") == "SEND_SUCCEED" and not of.sent_at:
            of.sent_at = dt.datetime.combine(of.issue_date, dt.time(12))
        pos = 0
        for e in entries.get(("offer", int(o["id"])), []):
            if e.get("variant") not in (None, 1, o.get("selectedVariant") or 1):
                continue
            pos += 1
            price = e.get("unitBruttoPrice") if of.prices_gross else e.get("unitNettoPrice")
            of.items.append(OfferItem(
                position=pos, product_id=ctx.product(e), code=(e.get("productCode") or "")[:60],
                name=(e.get("name") or "Pozycja")[:500], quantity=_q(e.get("quantity")),
                unit=_unit(e.get("unit")), unit_price=_d(price), vat_rate=_vat(e.get("vatPercent")),
                net=_d(e.get("nettoPrice")), vat=_d(e.get("vatPrice")), gross=_d(e.get("bruttoPrice"))))
        by_firmao[int(o["id"])] = of
    db.flush()
    out["skipped_types"] = dict(out["skipped_types"])
    out["_map"] = {k: v.id for k, v in by_firmao.items()}
    return out


def link_documents(db: Session, transactions: list[dict], offers: list[dict], inv_map: dict, wz_map: dict,
                   offer_map: dict) -> int:
    """Powiazania z Firmao: faktura <-> WZ, oferta -> faktura, oferta -> WZ."""
    links = 0
    for t in transactions:
        inv_id = inv_map.get(int(t["id"]))
        if not inv_id:
            continue
        for s in t.get("storageDocs") or []:
            if isinstance(s, dict) and s.get("deleted"):
                continue
            d = db.get(Delivery, wz_map.get(int(_id(s) or 0), 0) or 0)
            if d is not None and d.invoice_id is None:
                d.invoice_id = inv_id
                links += 1
        for o in t.get("offers") or []:
            of = db.get(Offer, offer_map.get(int(_id(o) or 0), 0) or 0)
            if of is not None and of.invoice_id is None:
                of.invoice_id = inv_id
                links += 1
    for o in offers:
        of = db.get(Offer, offer_map.get(int(o["id"]), 0) or 0)
        if of is None:
            continue
        for s in o.get("storageDocs") or []:
            wid = wz_map.get(int(_id(s) or 0))
            if wid and of.delivery_id is None:
                of.delivery_id = wid
                links += 1
    db.flush()
    return links


SERIES = [("vat", Invoice, lambda: Invoice.kind.in_(("vat", "final"))),
          ("proforma", Invoice, lambda: Invoice.kind == "proforma"),
          ("advance", Invoice, lambda: Invoice.kind == "advance"),
          ("correction", Invoice, lambda: Invoice.kind == "correction"),
          ("wz", Delivery, lambda: Delivery.kind == "wz"), ("pz", Delivery, lambda: Delivery.kind == "pz"),
          ("offer", Offer, None)]
FIRMAO_SHAPE = re.compile(r"^\d{4}/\d{2}/\d{2,4}(?P<suffix>(/[A-Za-z]+)*)$")


def _numbers(db: Session, company: Company, model, cond, imported_only: bool = False):
    stmt = select(model.number, model.issue_date).where(model.company_id == company.id, model.number.is_not(None))
    if cond is not None:
        stmt = stmt.where(cond())
    if imported_only:
        stmt = stmt.where(model.source_ref.like("firmao:%"))
    return list(db.execute(stmt))


def adopt_patterns(db: Session, company: Company) -> dict:
    """Przejmuje wzor numeracji z Firmao (RRRR/MM/NNN + koncowka, np. /KOR, /WZ), gdy pasuje do wiekszosci
    zaimportowanych numerow danej serii. Numeracja zaczyna sie wtedy od nowa co miesiac."""
    out = {}
    for kind, model, cond in SERIES:
        rows = _numbers(db, company, model, cond, imported_only=True)
        suffixes: dict[str, int] = defaultdict(int)
        for number, _ in rows:
            m = FIRMAO_SHAPE.match(number or "")
            if m:
                suffixes[m.group("suffix")] += 1
        if not suffixes:
            continue
        suffix, hits = max(suffixes.items(), key=lambda kv: kv[1])
        if hits * 10 < len(rows) * 8:
            continue
        pattern = "{RRRR}/{MM}/{NNN}" + suffix
        setattr(company, numbering.PATTERNS[kind], pattern)
        out[kind] = pattern
    if out:
        company.numbering_reset = "month"
    db.flush()
    return out


def sync_sequences(db: Session, company: Company) -> dict:
    """Ustawia liczniki numeracji tak, by nowe dokumenty szly dalej po istniejacych numerach w biezacym wzorze.
    Okres (miesiac/rok) bierze sie z numeru, gdy go zawiera, a inaczej z daty dokumentu."""
    out = {}
    for kind, model, cond in SERIES:
        pattern = getattr(company, numbering.PATTERNS[kind])
        rx = re.escape(pattern)
        for tag, expr in (("{nr}", r"(?P<nr>\d+)"), ("{NNN}", r"(?P<nr>\d+)"), ("{MM}", r"(?P<mm>\d{2})"),
                          ("{RRRR}", r"(?P<y>\d{4})"), ("{RR}", r"(?P<yy>\d{2})")):
            rx = rx.replace(re.escape(tag), expr, 1).replace(re.escape(tag), r"\d+")
        if "(?P<nr>" not in rx:
            continue
        rxc = re.compile(f"^{rx}$")
        best: dict[str, int] = {}
        for number, date in _numbers(db, company, model, cond):
            m = rxc.match(number or "")
            if not m:
                continue
            g = m.groupdict()
            year = int(g["y"]) if g.get("y") else (2000 + int(g["yy"]) if g.get("yy") else date.year)
            month = int(g["mm"]) if g.get("mm") else date.month
            try:
                period = numbering.period_for(company, dt.date(year, month, 1))
            except ValueError:
                continue
            best[period] = max(best.get(period, 0), int(m.group("nr")))
        for period, last in best.items():
            row = db.scalar(select(NumberSequence).where(NumberSequence.company_id == company.id,
                                                         NumberSequence.kind == kind, NumberSequence.period == period))
            if row is None:
                db.add(NumberSequence(company_id=company.id, kind=kind, period=period, last=last))
            elif row.last < last:
                row.last = last
        if best:
            out[kind] = len(best)
    db.flush()
    return out


def import_all(db: Session, data: dict[str, list], company: Company, log=print) -> dict:
    """Pelny import w kolejnosci: klienci, osoby, produkty, faktury, zakupy, WZ/PZ, oferty, powiazania, numeracja."""
    summary: dict = {}
    if data.get("customers"):
        summary["klienci"] = firmao_import.import_customers(db, data["customers"])
        log(f"klienci: {summary['klienci']}")
    if data.get("contacts"):
        summary["osoby"] = firmao_import.import_contacts(db, data["contacts"])
        log(f"osoby: {summary['osoby']}")
    if data.get("products"):
        summary["produkty"] = firmao_import.import_products(db, data["products"])
        log(f"produkty: {summary['produkty']}")
    ctx = Ctx(db, company)
    entries = group_entries(data.get("transactionentries") or [])
    tx = data.get("transactions") or []
    inv = import_invoices(ctx, tx, entries)
    log(f"faktury: { {k: v for k, v in inv.items() if k != '_map'} }")
    summary["zakupy"] = import_purchases(ctx, tx)
    log(f"zakupy: {summary['zakupy']}")
    wz = import_deliveries(ctx, data.get("storagedocs") or [], entries, inv["_map"])
    log(f"wz/pz: { {k: v for k, v in wz.items() if k != '_map'} }")
    of = import_offers(ctx, data.get("offers") or [], entries)
    log(f"oferty: { {k: v for k, v in of.items() if k != '_map'} }")
    summary["powiazania"] = link_documents(db, tx, data.get("offers") or [], inv["_map"], wz["_map"], of["_map"])
    summary["wzory"] = adopt_patterns(db, company)
    summary["numeracja"] = sync_sequences(db, company)
    for name, res in (("faktury", inv), ("wz", wz), ("oferty", of)):
        res.pop("_map", None)
        summary[name] = res
    return summary
