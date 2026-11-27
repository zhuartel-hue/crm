"""Dokumenty magazynowe: WZ (wydanie towaru odbiorcy, z podpisem na telefonie) i PZ (przyjecie od dostawcy).

Pozycje moga miec ceny i VAT (jak w Firmao: towar, dojazd w km, roboczogodziny), z WZ mozna wystawic fakture.
Wystawienie dokumentu zmienia stan magazynowy produktow (WZ minus, PZ plus), uslug nie liczy.
"""
from __future__ import annotations

import base64
import datetime as dt
import io
from decimal import Decimal
from zoneinfo import ZoneInfo

from PIL import Image
from sqlalchemy.orm import Session

from . import calc, numbering
from .models import DELIVERY_KINDS, VAT_RATES, Delivery, DeliveryItem, Invoice, Product, StockMovement

MAX_SIGNATURE = 2 * 1024 * 1024


def from_invoice(inv: Invoice) -> Delivery:
    d = Delivery(company_id=inv.company_id, kind="wz", status="draft", issue_date=dt.date.today(),
                 contractor_id=inv.contractor_id, recipient_name=inv.buyer_name, recipient_address=inv.buyer_address,
                 invoice_id=inv.id, notes="", signed_by="", prices_gross=inv.prices_gross)
    for i, it in enumerate(inv.after_items, start=1):
        d.items.append(DeliveryItem(position=i, product_id=it.product_id, code=it.product.code if it.product else "",
                                    name=it.name, quantity=it.quantity, unit=it.unit, unit_price=it.unit_price,
                                    vat_rate=it.vat_rate))
    recalculate(d)
    return d


def _at(values, k, default=""):
    return values[k] if values is not None and k < len(values) else default


def set_items(db: Session, d, names, qtys, units, product_ids, prices=None, rates=None, item_cls=DeliveryItem,
              require_price: bool = False) -> None:
    """Pozycje z formularza. Cena jest opcjonalna (WZ bez cen tez jest poprawna), VAT domyslnie z produktu albo 23."""
    d.items.clear()
    pos = 0
    for k, name in enumerate(names):
        name = (name or "").strip()
        if not name:
            continue
        pos += 1
        qty = calc.parse_decimal(_at(qtys, k, "1"), Decimal("1"))
        if qty is None or qty <= 0:
            raise ValueError(f"Pozycja {pos}: ilość musi być większa od zera.")
        pid = _at(product_ids, k)
        product = db.get(Product, int(pid)) if str(pid).isdigit() else None
        if product is not None and product.company_id != d.company_id:
            product = None
        if product is not None and product.label == name:
            name = product.name
        try:
            price = calc.parse_decimal(_at(prices, k), None)
        except ValueError:
            raise ValueError(f"Pozycja {pos}: niepoprawna cena.") from None
        if price is None and require_price:
            raise ValueError(f"Pozycja {pos}: podaj cenę.")
        if price is not None and price < 0:
            raise ValueError(f"Pozycja {pos}: cena nie może być ujemna.")
        rate = _at(rates, k) or (product.vat_rate if product else "23")
        if rate not in VAT_RATES:
            raise ValueError(f"Pozycja {pos}: stawka VAT jedna z {', '.join(VAT_RATES)}.")
        d.items.append(item_cls(position=pos, product_id=product.id if product else None,
                                code=product.code if product else "", name=name[:500], quantity=qty,
                                unit=(_at(units, k) or "szt.")[:10], unit_price=price, vat_rate=rate))
    recalculate(d)


def recalculate(d) -> None:
    net = vat = gross = Decimal("0")
    for it in d.items:
        if it.unit_price is None:
            it.net = it.vat = it.gross = Decimal("0")
            continue
        line = calc.vat_line(it.quantity, it.unit_price, it.vat_rate, bool(d.prices_gross))
        it.net, it.vat, it.gross = line.net, line.vat, line.gross
        net, vat, gross = net + line.net, vat + line.vat, gross + line.gross
    d.total_net, d.total_vat, d.total_gross = net, vat, gross


def has_prices(d: Delivery) -> bool:
    return any(it.unit_price is not None for it in d.items)


def issue(db: Session, d: Delivery) -> None:
    if d.status == "issued":
        return
    if d.kind not in DELIVERY_KINDS:
        raise ValueError("Nieznany rodzaj dokumentu.")
    if not d.items:
        raise ValueError(f"{d.kind_short} nie ma pozycji.")
    if not d.recipient_name.strip():
        raise ValueError("Podaj odbiorcę." if d.kind == "wz" else "Podaj dostawcę.")
    seq, number = numbering.next_number(db, d.company, d.kind, d.issue_date)
    d.seq, d.number, d.status = seq, number, "issued"
    sign_ = -1 if d.kind == "wz" else 1
    for it in d.items:
        product = it.product if it.product is not None else (db.get(Product, it.product_id) if it.product_id else None)
        if product is None or product.is_service:
            continue
        product.stock = (product.stock or Decimal("0")) + sign_ * it.quantity
        db.add(StockMovement(product_id=product.id, delivery=d, date=d.issue_date, quantity=sign_ * it.quantity,
                             note=number[:200]))


def adjust_stock(db: Session, product: Product, new_stock: Decimal, note: str = "") -> None:
    """Reczna korekta stanu (inwentaryzacja): zapisuje roznice jako ruch."""
    diff = new_stock - (product.stock or Decimal("0"))
    if diff == 0:
        return
    product.stock = new_stock
    db.add(StockMovement(product_id=product.id, date=dt.date.today(), quantity=diff,
                         note=(note or "Korekta stanu")[:200]))


def to_invoice(db: Session, actor: str, d: Delivery) -> Invoice:
    """Szkic faktury VAT z pozycji jednej WZ."""
    return to_invoice_many(db, actor, [d])


def _buyer_key(d: Delivery):
    if d.contractor_id:
        return ("c", d.contractor_id)
    return ("n", " ".join(d.recipient_name.lower().split()))


def to_invoice_many(db: Session, actor: str, docs: list[Delivery]) -> Invoice:
    """Jeden szkic faktury VAT z kilku WZ tego samego odbiorcy i tej samej firmy.

    Ceny z WZ, a gdy brak, z katalogu produktow. Na fakturze sama nazwa pozycji (bez kodu produktu).
    Po utworzeniu szkicu kazda WZ wskazuje te fakture (jest „zafakturowana”); usuniecie szkicu to zwalnia.
    """
    from .services import ServiceError, create_invoice_draft

    if not docs:
        raise ValueError("Zaznacz co najmniej jedną WZ.")
    docs = sorted({d.id: d for d in docs}.values(), key=lambda d: (d.issue_date, d.id))
    for d in docs:
        if d.kind != "wz":
            raise ValueError(f"{d.display_number}: fakturę wystawia się tylko z WZ.")
        if d.status != "issued":
            raise ValueError(f"{d.display_number}: najpierw wystaw WZ.")
        if d.invoice_id:
            raise ValueError(f"{d.display_number} ma już fakturę.")
    if len({d.company_id for d in docs}) > 1:
        raise ValueError("Zaznaczone WZ są z różnych firm.")
    if len({_buyer_key(d) for d in docs}) > 1:
        raise ValueError("Zaznaczone WZ mają różnych odbiorców. Jedna faktura może objąć WZ tylko jednego kontrahenta.")
    if len({bool(d.prices_gross) for d in docs}) > 1:
        raise ValueError("Zaznaczone WZ mają ceny raz netto, raz brutto. Wystaw je osobnymi fakturami.")
    items = []
    for d in docs:
        for it in d.items:
            row = dict(name=it.name, quantity=str(it.quantity), unit=it.unit, vat_rate=it.vat_rate)
            if it.product_id:
                row["product_id"] = it.product_id
            if it.unit_price is not None:
                row["unit_price"] = str(it.unit_price)
            elif not it.product_id:
                raise ValueError(f"{d.display_number}, pozycja {it.position} ({it.name}) nie ma ceny: uzupełnij ją "
                                 "na fakturze ręcznie albo dopisz cenę w produkcie.")
            items.append(row)
    first = docs[0]
    buyer = {"contractor_id": first.contractor_id} if first.contractor_id else \
        {"name": first.recipient_name, "address": first.recipient_address}
    numbers = ", ".join(d.number for d in docs)
    try:
        inv = create_invoice_draft(db, actor, dict(company=first.company_id, kind="vat", buyer=buyer, items=items,
                                                   prices_gross=bool(first.prices_gross),
                                                   notes=f"Do {numbers}"[:2000]))
    except ServiceError as e:
        raise ValueError(str(e)) from None
    for d in docs:
        d.invoice_id = inv.id
    db.commit()
    return inv


def payment_state(d: Delivery) -> str:
    """Stan zaplaty WZ wg powiazanej faktury: paid / partial / unpaid / draft (szkic faktury) / none (bez faktury)."""
    inv = d.invoice
    if inv is None:
        return "none"
    if inv.status != "issued":
        return "draft"
    if inv.to_pay <= 0:
        return "paid"
    return "partial" if (inv.paid or 0) > 0 else "unpaid"


def sign(d: Delivery, data_url: str, name: str) -> None:
    """Zapisuje podpis z kanwy (data:image/png;base64,...) jako PNG na bialym tle."""
    if d.status != "issued":
        raise ValueError("Najpierw wystaw WZ.")
    if not data_url.startswith("data:image/png;base64,"):
        raise ValueError("Brak podpisu.")
    raw = base64.b64decode(data_url.split(",", 1)[1], validate=False)
    if len(raw) > MAX_SIGNATURE:
        raise ValueError("Podpis jest za duży.")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:  # noqa: BLE001
        raise ValueError("Nie można odczytać podpisu.") from e
    img = img.convert("RGBA")
    if img.getbbox() is None or img.getchannel("A").getbbox() is None:
        raise ValueError("Podpis jest pusty.")
    img.thumbnail((1200, 400))
    bg = Image.new("RGB", img.size, (255, 255, 255))
    bg.paste(img, mask=img.getchannel("A"))
    out = io.BytesIO()
    bg.save(out, "PNG", optimize=True)
    d.signature_png = out.getvalue()
    d.signed_by = (name or "").strip()[:200]
    d.signed_at = dt.datetime.now(ZoneInfo("Europe/Warsaw")).replace(tzinfo=None)


def signature_data_url(d: Delivery) -> str:
    return "data:image/png;base64," + base64.b64encode(d.signature_png).decode() if d.signature_png else ""


def render_pdf(d: Delivery) -> bytes:
    from weasyprint import HTML

    from .pdf import HERE, _env

    html = _env.get_template("wz_pdf.html").render(d=d, signature=signature_data_url(d))
    return HTML(string=html, base_url=str(HERE)).write_pdf()
