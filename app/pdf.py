"""PDF faktury (WeasyPrint, szablon HTML)."""
from __future__ import annotations

import base64
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import calc

HERE = Path(__file__).parent
_env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape())
_env.filters["money"] = calc.format_money
_env.filters["qty"] = calc.format_qty
_env.filters["plnwords"] = calc.amount_in_words
_env.filters["date"] = lambda d: d.strftime("%d.%m.%Y") if d else ""


def logo_uri(company) -> str:
    if not company.logo:
        return ""
    return f"data:{company.logo_type or 'image/png'};base64,{base64.b64encode(company.logo).decode()}"


def render_invoice_html(invoice, template: str | None = None) -> str:
    from .ksef import qr, service as ksef_service

    url = ksef_service.qr_url(invoice)
    return _env.get_template("invoice_pdf.html").render(
        inv=invoice, summary=calc.invoice_summary(invoice), ksef_url=url, ksef_qr=qr.svg(url) if url else "",
        tpl=template or invoice.company.invoice_template or "klasyczny", logo_uri=logo_uri(invoice.company),
    )


def render_invoice(invoice, template: str | None = None) -> bytes:
    from weasyprint import HTML  # import tutaj: ciezka biblioteka, nie potrzebna przy starcie

    return HTML(string=render_invoice_html(invoice, template), base_url=str(HERE)).write_pdf()


def prepare_logo(data: bytes) -> tuple[bytes, str]:
    """Logo do faktury: przyciete z bialego tla, max 1200 px szerokosci, PNG."""
    import io

    from PIL import Image, ImageChops

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise ValueError("To nie jest obrazek (PNG albo JPG).")
    im = im.convert("RGBA")
    flat = Image.new("RGB", im.size, (255, 255, 255))
    flat.paste(im, mask=im.split()[3])
    mask = ImageChops.difference(flat, Image.new("RGB", im.size, (255, 255, 255))).convert("L").point(
        lambda p: 255 if p > 40 else 0)
    box = mask.getbbox()
    if box:
        pad = max(4, (box[3] - box[1]) // 20)
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad))
        flat = flat.crop(box)
    if flat.width > 1200:
        flat = flat.resize((1200, round(flat.height * 1200 / flat.width)))
    out = io.BytesIO()
    flat.save(out, "PNG", optimize=True)
    return out.getvalue(), "image/png"
