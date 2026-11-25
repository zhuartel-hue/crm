"""WZ (wydanie zewnetrzne): dokument wydania towaru odbiorcy, z podpisem na telefonie."""
from __future__ import annotations

import base64
import datetime as dt
import io
from decimal import Decimal
from zoneinfo import ZoneInfo

from PIL import Image
from sqlalchemy.orm import Session

from . import calc, numbering
from .models import Delivery, DeliveryItem, Invoice, Product

MAX_SIGNATURE = 2 * 1024 * 1024


def from_invoice(inv: Invoice) -> Delivery:
    d = Delivery(company_id=inv.company_id, status="draft", issue_date=dt.date.today(), contractor_id=inv.contractor_id,
                 recipient_name=inv.buyer_name, recipient_address=inv.buyer_address, invoice_id=inv.id, notes="",
                 signed_by="")
    for i, it in enumerate(inv.after_items, start=1):
        d.items.append(DeliveryItem(position=i, product_id=it.product_id, code=it.product.code if it.product else "",
                                    name=it.name, quantity=it.quantity, unit=it.unit))
    return d


def set_items(db: Session, d: Delivery, names, qtys, units, product_ids) -> None:
    d.items.clear()
    pos = 0
    for k, name in enumerate(names):
        name = (name or "").strip()
        if not name:
            continue
        pos += 1
        qty = calc.parse_decimal(qtys[k] if k < len(qtys) else "1", Decimal("1"))
        if qty is None or qty <= 0:
            raise ValueError(f"Pozycja {pos}: ilość musi być większa od zera.")
        pid = product_ids[k] if k < len(product_ids) else ""
        product = db.get(Product, int(pid)) if str(pid).isdigit() else None
        if product is not None and product.label == name:
            name = product.name
        d.items.append(DeliveryItem(position=pos, product_id=product.id if product else None,
                                    code=product.code if product else "", name=name[:500], quantity=qty,
                                    unit=((units[k] if k < len(units) else "") or "szt.")[:10]))


def issue(db: Session, d: Delivery) -> None:
    if d.status == "issued":
        return
    if not d.items:
        raise ValueError("WZ nie ma pozycji.")
    if not d.recipient_name.strip():
        raise ValueError("Podaj odbiorcę.")
    seq, number = numbering.next_number(db, d.company, "wz", d.issue_date)
    d.seq, d.number, d.status = seq, number, "issued"


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
