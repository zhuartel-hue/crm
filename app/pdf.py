"""PDF faktury (WeasyPrint, szablon HTML)."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import calc

HERE = Path(__file__).parent
_env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape())
_env.filters["money"] = calc.format_money
_env.filters["qty"] = calc.format_qty
_env.filters["plnwords"] = calc.amount_in_words
_env.filters["date"] = lambda d: d.strftime("%d.%m.%Y") if d else ""


def render_invoice_html(invoice) -> str:
    return _env.get_template("invoice_pdf.html").render(inv=invoice, summary=calc.vat_summary(invoice.items))


def render_invoice(invoice) -> bytes:
    from weasyprint import HTML  # import tutaj: ciezka biblioteka, nie potrzebna przy starcie

    return HTML(string=render_invoice_html(invoice), base_url=str(HERE)).write_pdf()
