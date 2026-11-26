"""Przeniesienie archiwum z Firmao: faktury sprzedazy i zakupu z pozycjami, oferty, WZ/PZ.

Wejscie: slownik list rekordow z eksportu API (tools/firmao_export.py, odchudzony przez tools/firmao_bundle.py):
customers, contacts, products, transactions, transactionentries, storagedocs, offers.
Dokumenty dostaja numery i kwoty z Firmao (bez przeliczania), source_ref="firmao:<id>", wiec ponowny import
aktualizuje zamiast dublowac. Faktury nie sa wysylane do KSeF (status "accepted" z numerem KSeF z Firmao,
a bez numeru "external"). Stany magazynowe biora sie z produktow, WZ/PZ ich nie zmieniaja.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
from zoneinfo import ZoneInfo
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import firmao_import, numbering
from .models import (VAT_RATES, Company, Contact, Contractor, ContractorNote, Delivery, DeliveryItem, Equipment, Invoice,
                     InvoiceItem, NumberSequence, Offer, OfferItem, Product, PurchaseInvoice, RecurringInvoice, Task)

INVOICE_TYPES = {"INVOICE": "vat", "VAT": "vat", "VAT_INVOICE": "vat", "CORRECTION": "correction",
                 "PROFORMA": "proforma", "PRO_FORMA": "proforma", "ADVANCE": "advance", "ADVANCE_INVOICE": "advance",
                 "PREPAYMENT": "advance", "PREPAYMENT_INVOICE": "advance", "FINAL": "final", "FINAL_INVOICE": "final"}
PAYMENT_TYPES = {"TRANSFER": "transfer", "CASH": "cash", "CARD": "card", "PAYMENT_CARD": "card",
                 "COMPENSATION": "compensation", "COD": "cash", "PREPAY": "transfer", "INTERNET": "transfer",
                 "OTHER": "transfer"}
UNITS = {"szt": "szt.", "szt.": "szt.", "sztuka": "szt.", "sztuk": "szt.", "sztuki": "szt.", "godz": "godz.",
         "godz.": "godz.", "h": "godz.", "kpl": "kpl.", "kpl.": "kpl.", "l": "l", "km": "km", "m": "m", "kg": "kg",
         "usł": "usł.", "usł.": "usł.", "usługa": "usł."}
OFFER_STATUS = {"INCOMPLETE": "incomplete", "NEW": "new", "DRAFT": "new", "PREPARED": "prepared",
                "DURING_CALCULATIONS": "calculating", "DURING_NEGOTIATIONS": "negotiating", "SENT": "sent",
                "WAITING_FOR_PAYMENT": "waiting_pay", "ACCEPTED": "accepted", "ORDERED": "ordered",
                "IN_PROGRESS": "in_progress", "EXECUTED": "done", "REJECTED": "rejected"}
STORAGE_TYPES = {"OUTSIDE_RELEASE": "wz", "OUTSIDE_INCOME": "pz", "OUTSIDE_RECEIPT": "pz", "OUTSIDE_ADMISSION": "pz"}
ZERO = Decimal("0")
WARSAW = ZoneInfo("Europe/Warsaw")


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
        self.contractors = {c.source_ref: c.id for c in db.scalars(select(Contractor).where(
            Contractor.company_id == company.id, Contractor.source_ref != ""))}
        self.products = {p.source_ref: p.id for p in db.scalars(select(Product).where(
            Product.company_id == company.id, Product.source_ref != ""))}

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


def _plain(text: str) -> str:
    """Tresc notatki bez HTML (Firmao zapisuje czasem <p>, <br>)."""
    if "<" not in text:
        return html.unescape(text).strip()
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


def _dt(v) -> dt.datetime | None:
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(WARSAW).replace(tzinfo=None)
    except ValueError:
        return None


def import_notes(db: Session, company: Company, notes: list[dict]) -> dict:
    """Notatki handlowe z Firmao przy kontrahentach (bez klienta - pomijane)."""
    firms = {c.source_ref: c.id for c in db.scalars(select(Contractor).where(Contractor.company_id == company.id, Contractor.source_ref != ""))}
    people = {c.source_ref: c.id for c in db.scalars(select(Contact).where(Contact.company_id == company.id, Contact.source_ref != ""))}
    have = {n.source_ref: n for n in db.scalars(select(ContractorNote).join(Contractor, Contractor.id == ContractorNote.contractor_id).where(Contractor.company_id == company.id, ContractorNote.source_ref != ""))}
    out = {"added": 0, "updated": 0, "skipped": 0}
    for r in notes:
        cid = firms.get(f"firmao:{_id(r.get('customer'))}") if _id(r.get("customer")) else None
        text = _plain(str(r.get("description") or ""))
        if cid is None or not text or r.get("deleted"):
            out["skipped"] += 1
            continue
        ref = f"firmao:{r['id']}"
        n = have.get(ref)
        if n is None:
            n = ContractorNote(source_ref=ref)
            db.add(n)
            have[ref] = n
            out["added"] += 1
        else:
            out["updated"] += 1
        n.contractor_id = cid
        n.contact_id = people.get(f"firmao:{_id(r.get('contact'))}") if _id(r.get("contact")) else None
        n.at = _dt(r.get("date")) or _dt(r.get("creationDate")) or dt.datetime.now().replace(microsecond=0)
        n.author = str((r.get("createdBy") or {}).get("label") or "Firmao")[:100]
        extra = [f"Zadanie: {r['task']['name']}" if isinstance(r.get("task"), dict) and r["task"].get("name") else "",
                 f"Oferta: {r['offer']['number']}" if isinstance(r.get("offer"), dict) and r["offer"].get("number") else ""]
        n.text = "\n".join([("❗ " if r.get("important") else "") + text, *[x for x in extra if x]])
    db.flush()
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


PERIODS = {"MONTH": 1, "MONTHLY": 1, "TWO_MONTHS": 2, "QUARTER": 3, "QUARTERLY": 3, "HALF_YEAR": 6, "SIX_MONTHS": 6,
           "YEAR": 12, "YEARLY": 12, "ANNUAL": 12}


def import_agreements(db: Session, company: Company, agreements: list[dict], entries: dict,
                      transactions: list[dict], today: dt.date | None = None) -> dict:
    """Umowy (faktury cykliczne) z Firmao. W okresie przejsciowym tylko szkice (mode="draft"), zeby nie dublowac
    faktur wystawianych jeszcze w Firmao. Nastepny termin liczony od ostatniej faktury z Firmao, nigdy wstecz.
    alwaysSendToKsef zaznacza u kontrahenta automatyczna wysylke do KSeF."""
    from . import recurring

    today = today or dt.date.today()
    firms = {c.source_ref: c for c in db.scalars(select(Contractor).where(Contractor.company_id == company.id, Contractor.source_ref != ""))}
    products = {p.source_ref: p.id for p in db.scalars(select(Product).where(Product.company_id == company.id, Product.source_ref != ""))}
    have = _existing(db, RecurringInvoice, company.id)
    out = {"added": 0, "updated": 0, "skipped": 0, "invoicing": 0, "ended": 0, "ksef_auto": 0, "no_items": 0,
           "linked_invoices": 0}
    by_firmao: dict[int, RecurringInvoice] = {}
    for a in agreements:
        if a.get("deleted"):
            out["skipped"] += 1
            continue
        ref = f"firmao:{a['id']}"
        r = have.get(ref)
        if r is None:
            r = RecurringInvoice(company_id=company.id, source_ref=ref, last_error="")
            db.add(r)
            out["added"] += 1
        else:
            out["updated"] += 1
        c = firms.get(f"firmao:{_id(a.get('customer'))}") if _id(a.get("customer")) else None
        r.number = str(a.get("number") or a.get("documentNumber") or "")[:60]
        r.contractor_id = c.id if c else None
        r.buyer_name = str(a.get("customerName") or (a.get("customer") or {}).get("label") or (c.name if c else ""))[:300]
        r.buyer_nip = str(a.get("nipNumber") or (c.nip if c else "") or "").replace("-", "").replace(" ", "")[:20]
        r.buyer_address = _addr(a.get("customerAddress")) or ("\n".join(c.address_lines) if c else "")
        r.name = (str(a.get("name") or "").strip() or f"Umowa {r.number or a['id']}")[:200]
        r.prices_gross = bool(a.get("calculateFromGross"))
        r.payment_method = PAYMENT_TYPES.get(str(a.get("paymentType") or "").upper(), "transfer")
        r.due_days = int(a.get("daysToDueDate") or 7)
        r.interval_months = PERIODS.get(str(a.get("issuePeriod") or "MONTH").upper(), 1)
        day = int(a.get("issueDay") or 1)
        r.day_of_month = day if 1 <= day <= 28 else 0
        r.signed_date = _date(a.get("agreementDate")) or _date(a.get("documentDate"))
        r.start_date = _date(a.get("validFrom"))
        r.end_date = _date(a.get("validTo"))
        emails = [a.get(k) for k in ("email", "email2", "email3") if a.get(k)] + list(a.get("emails") or []) \
            + list((a.get("customer") or {}).get("emails") or [])
        r.email_to = str(emails[0] if emails else (c.email.split(",")[0].strip() if c and c.email else ""))[:300]
        r.mode = "draft"  # okres przejsciowy: faktury wystawia jeszcze Firmao
        r.notes = str(a.get("annotations") or "") if a.get("copyAnnotations") else ""
        auto = str(a.get("issueType") or "")
        r.contract_notes = "\n".join(x for x in (
            str(a.get("description") or "").strip(),
            "W Firmao: wystaw i wyślij automatycznie." if auto == "AUTO_ISSUE_AND_SEND" else "",
            f"W Firmao szablon maila: {(a.get('mailTemplate') or {}).get('name')}" if (a.get("mailTemplate") or {}).get("name") else "",
        ) if x)
        rows = []
        for e in entries.get(("agreement", int(a["id"])), []):
            price = e.get("unitBruttoPrice") if r.prices_gross else e.get("unitNettoPrice")
            pid = products.get(f"firmao:{_id(e.get('product'))}") if _id(e.get("product")) else None
            rows.append(dict(name=(e.get("name") or "Pozycja")[:500], quantity=str(_q(e.get("quantity"))),
                             unit=_unit(e.get("unit")), unit_price=str(_d(price)), vat_rate=_vat(e.get("vatPercent")),
                             product_id=pid))
        if not rows:
            out["no_items"] += 1
            net = _d(a.get("nettoPrice"))
            if net:
                rows.append(dict(name=r.name[:500], quantity="1", unit="usł.", unit_price=str(net), vat_rate="23",
                                 product_id=None))
        r.items_json = json.dumps(rows, ensure_ascii=False)
        status = str(a.get("agreementStatus") or "").upper()
        ended = status == "EXPIRED" or (r.end_date is not None and r.end_date < today)
        r.active = not ended
        r.invoicing = status == "ACTIVE" and not ended and bool(rows)
        last = _date(a.get("lastInvoiceIssueDate"))
        upcoming = recurring.first_date(max(today + dt.timedelta(days=1), r.start_date or today), r.day_of_month)
        nxt = recurring.add_months(last, r.interval_months, r.day_of_month) if last else upcoming
        r.next_date = max(nxt, upcoming)
        out["ended"] += ended
        out["invoicing"] += r.invoicing
        if a.get("alwaysSendToKsef") and c is not None and r.active:
            if not c.ksef_auto:
                c.ksef_auto = True
                out["ksef_auto"] += 1
        by_firmao[int(a["id"])] = r
    db.flush()
    refs = {f"firmao:{t['id']}": t for t in transactions if t.get("agreements")}
    if refs:
        for inv in db.scalars(select(Invoice).where(Invoice.company_id == company.id,
                                                    Invoice.source_ref.in_(list(refs)))):
            for ag in refs[inv.source_ref].get("agreements") or []:
                r = by_firmao.get(int(_id(ag) or 0))
                if r is not None and not ag.get("deleted"):
                    inv.recurring_id = r.id
                    out["linked_invoices"] += 1
                    break
    db.flush()
    return out


def import_resources(db: Session, company: Company, resources: list[dict]) -> dict:
    """Sprzet z Firmao (resources): numery, klient, gwarancja, lokalizacja. Zezlomowany = wycofany."""
    firms = {c.source_ref: c.id for c in db.scalars(select(Contractor).where(Contractor.company_id == company.id, Contractor.source_ref != ""))}
    people = {c.source_ref: c for c in db.scalars(select(Contact).where(Contact.company_id == company.id, Contact.source_ref != ""))}
    products = {p.source_ref: p.id for p in db.scalars(select(Product).where(Product.company_id == company.id, Product.source_ref != ""))}
    have = _existing(db, Equipment, company.id)
    out = {"added": 0, "updated": 0, "skipped": 0, "with_client": 0, "retired": 0}
    for r in resources:
        if r.get("deleted"):
            out["skipped"] += 1
            continue
        ref = f"firmao:{r['id']}"
        e = have.get(ref)
        if e is None:
            e = Equipment(company_id=company.id, source_ref=ref, warranty_remind_days=30, service_interval_months=0)
            db.add(e)
            out["added"] += 1
        else:
            out["updated"] += 1
        e.name = str(r.get("name") or (r.get("product") or {}).get("label") or f"Sprzęt {r['id']}")[:300]
        e.category = str((r.get("resourceClass") or {}).get("name") or "")[:100]
        e.inventory_no = str(r.get("inventoryNumber") or "")[:100]
        e.serial_no = str(r.get("serialNumber") or "")[:150]
        e.barcode = str(r.get("manufacturerBarCode") or "")[:100]
        e.product_id = products.get(f"firmao:{_id(r.get('product'))}") if _id(r.get("product")) else None
        e.contractor_id = firms.get(f"firmao:{_id(r.get('customer'))}") if _id(r.get("customer")) else None
        p = people.get(f"firmao:{_id(r.get('contact'))}") if _id(r.get("contact")) else None
        e.contact_id = p.id if p else None
        if e.contractor_id is None and p is not None:
            e.contractor_id = p.contractor_id
        e.supplier = str((r.get("supplier") or {}).get("label") or "")[:300]
        e.location = str((r.get("localization") or {}).get("name") or "")[:300]
        e.purchase_date = _date(r.get("acquisitionDate"))
        e.price = _d(r.get("purchasePrice"), None) if r.get("purchasePrice") not in (None, "") else None
        e.warranty_until = _date(r.get("warrantyExpirationDate"))
        adv = r.get("reminderAdvance")
        if isinstance(adv, (int, float)) and 0 < adv <= 365:
            e.warranty_remind_days = int(adv)
        tags = ", ".join(str(t.get("label")) for t in (r.get("tags") or []) if isinstance(t, dict) and t.get("label"))
        who = (r.get("responsibleUser") or {}).get("label") if isinstance(r.get("responsibleUser"), dict) else ""
        e.notes = "\n".join(x for x in (_plain(str(r.get("description") or "")), f"Etykiety: {tags}" if tags else "",
                                        f"Odpowiada: {who}" if who else "") if x)
        e.active = not r.get("disposalDate")
        out["with_client"] += e.contractor_id is not None
        out["retired"] += not e.active
    db.flush()
    return out


def _task_dt(v) -> dt.datetime | None:
    """Data z Firmao: sam dzien (polnoc) albo data z godzina (UTC -> Warszawa)."""
    if not v:
        return None
    if len(str(v)) <= 10:
        d = _date(v)
        return dt.datetime.combine(d, dt.time(0, 0)) if d else None
    return _dt(v)


def _priority(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return "normal"
    return "low" if n < 100 else "normal" if n < 200 else "high" if n < 300 else "urgent"


def import_tasks(db: Session, company: Company, tasks: list[dict]) -> dict:
    """Zadania z Firmao: OPEN -> do zrobienia, CLOSED -> zrobione; terminy, klient, adres, czas pracy."""
    firms = {c.source_ref: c.id for c in db.scalars(select(Contractor).where(Contractor.company_id == company.id, Contractor.source_ref != ""))}
    people = {c.source_ref: c.id for c in db.scalars(select(Contact).where(Contact.company_id == company.id, Contact.source_ref != ""))}
    have = _existing(db, Task, company.id)
    out = {"added": 0, "updated": 0, "skipped": 0, "open": 0, "with_client": 0}
    for r in tasks:
        if r.get("deleted"):
            out["skipped"] += 1
            continue
        ref = f"firmao:{r['id']}"
        t = have.get(ref)
        if t is None:
            t = Task(company_id=company.id, source_ref=ref)
            db.add(t)
            out["added"] += 1
        else:
            out["updated"] += 1
        st = r.get("status") or {}
        key = str((st.get("group") or st.get("key")) if isinstance(st, dict) else st or "").upper()
        t.status = "todo" if key == "OPEN" else "done"
        t.title = str(r.get("name") or "Zadanie")[:300]
        t.description = _plain(str(r.get("description") or ""))
        t.address = str(r.get("address") or "")[:300]
        t.priority = _priority(r.get("priority"))
        cat = r.get("category") or {}
        t.category = str(cat.get("label") or "")[:100] if isinstance(cat, dict) else ""
        t.contractor_id = firms.get(f"firmao:{_id(r.get('customer'))}") if _id(r.get("customer")) else None
        contacts = r.get("contacts") or []
        t.contact_id = people.get(f"firmao:{_id(contacts[0])}") if contacts else None
        t.start_at = _task_dt(r.get("plannedStartDate"))
        t.due_at = _task_dt(r.get("plannedEndDate"))
        if t.due_at and t.start_at and t.due_at < t.start_at:
            t.due_at = None
        times = [x for x in (t.start_at, t.due_at) if x]
        t.all_day = all(x.time() == dt.time(0, 0) for x in times)
        t.done_at = (_task_dt(r.get("actualEndDate")) or _task_dt(r.get("lastUpdateDate"))) if t.status == "done" else None
        hours = r.get("actualWorkHours")
        t.minutes_spent = int(round(float(hours) * 60)) if isinstance(hours, (int, float)) and hours > 0 else 0
        users = r.get("responsibleUsers") or []
        t.assignee = str(users[0].get("label") or "")[:100] if users and isinstance(users[0], dict) else ""
        t.timer_started_at = None
        out["open"] += t.status == "todo"
        out["with_client"] += t.contractor_id is not None
    db.flush()
    return out


SECTIONS = ("klienci", "osoby", "produkty", "faktury", "zakupy", "wz", "oferty", "notatki", "umowy", "sprzet", "zadania",
            "statusy_ofert")


def update_offer_statuses(db: Session, company: Company, offers: list[dict]) -> dict:
    """Tylko statusy ofert z Firmao (1:1), bez ruszania pozycji i reszty dokumentu."""
    have = {o.source_ref: o for o in db.scalars(select(Offer).where(Offer.company_id == company.id,
                                                                    Offer.source_ref.like("firmao:%")))}
    out: dict = {"changed": 0, "same": 0, "missing": 0, "statuses": defaultdict(int)}
    for o in offers:
        of = have.get(f"firmao:{o.get('id')}")
        if of is None:
            out["missing"] += 1
            continue
        raw = o.get("offerStatus")
        key = raw.get("key") if isinstance(raw, dict) else raw
        status = OFFER_STATUS.get(str(key or "").upper(), "new")
        out["statuses"][status] += 1
        if of.status != status:
            of.status = status
            out["changed"] += 1
        else:
            out["same"] += 1
    db.flush()
    out["statuses"] = dict(out["statuses"])
    return out


def import_all(db: Session, data: dict[str, list], company: Company, log=print, only: set[str] | None = None) -> dict:
    """Pelny import w kolejnosci: klienci, osoby, produkty, faktury, zakupy, WZ/PZ, oferty, notatki, powiazania,
    numeracja. only = wybrane czesci (np. {"notatki"}), zeby nie nadpisywac dokumentow zmienionych juz w Sygnavo."""
    want = (lambda k: True) if not only else (lambda k: k in only)
    summary: dict = {}
    if want("klienci") and data.get("customers"):
        summary["klienci"] = firmao_import.import_customers(db, data["customers"], company.id)
        log(f"klienci: {summary['klienci']}")
    if want("osoby") and data.get("contacts"):
        summary["osoby"] = firmao_import.import_contacts(db, data["contacts"], company.id)
        log(f"osoby: {summary['osoby']}")
    if want("produkty") and data.get("products"):
        summary["produkty"] = firmao_import.import_products(db, data["products"], company.id)
        log(f"produkty: {summary['produkty']}")
    if want("notatki") and data.get("salesnotes"):
        summary["notatki"] = import_notes(db, company, data["salesnotes"])
        log(f"notatki: {summary['notatki']}")
    if want("zadania") and data.get("tasks"):
        summary["zadania"] = import_tasks(db, company, data["tasks"])
        log(f"zadania: {summary['zadania']}")
    if want("sprzet") and data.get("resources"):
        summary["sprzet"] = import_resources(db, company, data["resources"])
        log(f"sprzet: {summary['sprzet']}")
    if only and "statusy_ofert" in only and data.get("offers"):
        summary["statusy_ofert"] = update_offer_statuses(db, company, data["offers"])
        log(f"statusy_ofert: {summary['statusy_ofert']}")
    docs = [k for k in ("faktury", "zakupy", "wz", "oferty") if want(k)]
    if not docs:
        if want("umowy") and data.get("agreements"):
            summary["umowy"] = import_agreements(db, company, data["agreements"],
                                                 group_entries(data.get("transactionentries") or []),
                                                 data.get("transactions") or [])
            log(f"umowy: {summary['umowy']}")
        return summary
    if len(docs) < 4:
        raise ValueError("Dokumenty (faktury, zakupy, wz, oferty) importuje się razem, bo są ze sobą powiązane.")
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
    if want("umowy") and data.get("agreements"):
        summary["umowy"] = import_agreements(db, company, data["agreements"], entries, tx)
        log(f"umowy: {summary['umowy']}")
    summary["powiazania"] = link_documents(db, tx, data.get("offers") or [], inv["_map"], wz["_map"], of["_map"])
    summary["wzory"] = adopt_patterns(db, company)
    summary["numeracja"] = sync_sequences(db, company)
    for name, res in (("faktury", inv), ("wz", wz), ("oferty", of)):
        res.pop("_map", None)
        summary[name] = res
    return summary
