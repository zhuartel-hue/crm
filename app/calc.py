"""Wyliczenia kwot na fakturach VAT."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_decimal(text: str | None, default: Decimal | None = None) -> Decimal | None:
    """Liczba wpisana po polsku: "12 345,67" albo "12345.67"."""
    if text is None:
        return default
    cleaned = str(text).replace(" ", "").replace(" ", "").replace(",", ".")
    if not cleaned:
        return default
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Niepoprawna liczba: {text!r}")


def rate_percent(rate: str) -> Decimal:
    """Stawka jako liczba; "zw" (zwolnione) i "np" (nie podlega) to 0."""
    return Decimal(rate) if rate.isdigit() else Decimal("0")


@dataclass
class Line:
    net: Decimal
    vat: Decimal
    gross: Decimal


def vat_line(quantity: Decimal, unit_price: Decimal, rate: str, prices_gross: bool) -> Line:
    """Pozycja zwyklej faktury VAT. Wartosc liczona z ceny razy ilosc, VAT od wartosci pozycji."""
    value = money(quantity * unit_price)
    pct = rate_percent(rate)
    if prices_gross:
        gross = value
        vat = money(gross * pct / (Decimal("100") + pct))
        net = gross - vat
    else:
        net = value
        vat = money(net * pct / Decimal("100"))
        gross = net + vat
    return Line(net=net, vat=vat, gross=gross)


RATE_ORDER = ["23", "8", "5", "0", "zw", "np"]


def _sort_rates(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda s: RATE_ORDER.index(s["rate"]) if s["rate"] in RATE_ORDER else 99)


def vat_summary(items) -> list[dict]:
    """Zestawienie wg stawek (do tabelki pod pozycjami)."""
    sums: dict[str, dict] = {}
    for it in items:
        s = sums.setdefault(it.vat_rate, {"rate": it.vat_rate, "net": Decimal("0"), "vat": Decimal("0"), "gross": Decimal("0")})
        s["net"] += it.net
        s["vat"] += it.vat
        s["gross"] += it.gross
    return _sort_rates(list(sums.values()))


def _diff(a: list[dict], b: list[dict]) -> list[dict]:
    """a - b wg stawek."""
    out = {r["rate"]: dict(r) for r in a}
    for r in b:
        s = out.setdefault(r["rate"], {"rate": r["rate"], "net": Decimal("0"), "vat": Decimal("0"), "gross": Decimal("0")})
        for k in ("net", "vat", "gross"):
            s[k] -= r[k]
    return _sort_rates(list(out.values()))


def advance_split(order_rows: list[dict], advance_gross: Decimal) -> list[dict]:
    """Zaliczka rozdzielona na stawki proporcjonalnie do wartosci brutto zamowienia (art. 106f ust. 1 pkt 3)."""
    order_gross = sum((r["gross"] for r in order_rows), Decimal("0"))
    if not order_rows or order_gross <= 0:
        return []
    rows, left = [], money(advance_gross)
    for n, r in enumerate(order_rows):
        gross = left if n == len(order_rows) - 1 else money(advance_gross * r["gross"] / order_gross)
        left -= gross
        pct = rate_percent(r["rate"])
        vat = money(gross * pct / (Decimal("100") + pct))
        rows.append({"rate": r["rate"], "net": gross - vat, "vat": vat, "gross": gross})
    return rows


def invoice_summary(inv) -> list[dict]:
    """Zestawienie wg stawek dla kazdego rodzaju faktury.

    korekta: roznica po - przed; zaliczkowa: otrzymana zaliczka; koncowa: zamowienie minus zaliczki.
    """
    if inv.kind == "correction":
        return _diff(vat_summary(inv.after_items), vat_summary(inv.before_items))
    if inv.kind == "advance":
        return advance_split(vat_summary(inv.items), inv.advance_gross or Decimal("0"))
    if inv.kind == "final":
        rows = vat_summary(inv.items)
        for adv in inv.advances:
            rows = _diff(rows, advance_split(vat_summary(adv.items), adv.advance_gross or Decimal("0")))
        return rows
    return vat_summary(inv.items)


def recalculate(invoice) -> None:
    """Przelicza pozycje i sumy faktury na miejscu (sumy z invoice_summary)."""
    after = before = 0
    for it in invoice.items:
        if it.is_before:
            before += 1
            it.position = before
        else:
            after += 1
            it.position = after
        line = vat_line(it.quantity, it.unit_price, it.vat_rate, invoice.prices_gross)
        it.purchase_price = None
        it.margin = None
        it.net, it.vat, it.gross = line.net, line.vat, line.gross
    if invoice.kind in ("advance", "final"):
        invoice.order_total = sum((i.gross for i in invoice.items), Decimal("0"))
    rows = invoice_summary(invoice)
    invoice.total_net = sum((r["net"] for r in rows), Decimal("0"))
    invoice.total_vat = sum((r["vat"] for r in rows), Decimal("0"))
    invoice.total_gross = sum((r["gross"] for r in rows), Decimal("0"))
    invoice.margin_total = Decimal("0")


# --- Kwota slownie -------------------------------------------------------

_ONES = ["", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć"]
_TEENS = ["dziesięć", "jedenaście", "dwanaście", "trzynaście", "czternaście", "piętnaście",
          "szesnaście", "siedemnaście", "osiemnaście", "dziewiętnaście"]
_TENS = ["", "", "dwadzieścia", "trzydzieści", "czterdzieści", "pięćdziesiąt", "sześćdziesiąt",
         "siedemdziesiąt", "osiemdziesiąt", "dziewięćdziesiąt"]
_HUNDREDS = ["", "sto", "dwieście", "trzysta", "czterysta", "pięćset", "sześćset", "siedemset",
             "osiemset", "dziewięćset"]
_GROUPS = [("", "", ""), ("tysiąc", "tysiące", "tysięcy"), ("milion", "miliony", "milionów"),
           ("miliard", "miliardy", "miliardów")]


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    one, few, many = forms
    if n == 1:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def _three(n: int) -> list[str]:
    words = [_HUNDREDS[n // 100]]
    rest = n % 100
    if 10 <= rest < 20:
        words.append(_TEENS[rest - 10])
    else:
        words += [_TENS[rest // 10], _ONES[rest % 10]]
    return [w for w in words if w]


def number_words(n: int) -> str:
    if n == 0:
        return "zero"
    parts: list[str] = []
    group = 0
    while n > 0:
        chunk = n % 1000
        if chunk:
            words = _three(chunk)
            if group > 0:
                if chunk == 1:
                    words = []
                words.append(_plural(chunk, _GROUPS[group]))
            parts = words + parts
        n //= 1000
        group += 1
    return " ".join(parts)


def amount_in_words(amount: Decimal, currency: str = "PLN") -> str:
    """Np. 12 345,67 -> "dwanaście tysięcy trzysta czterdzieści pięć złotych 67/100"."""
    amount = abs(money(amount))
    zl = int(amount)
    gr = int((amount - zl) * 100)
    if currency == "PLN":
        unit = _plural(zl, ("złoty", "złote", "złotych"))
    else:
        unit = currency
    return f"{number_words(zl)} {unit} {gr:02d}/100"


def format_money(value) -> str:
    """12345.6 -> "12 345,60"."""
    if value is None:
        return ""
    v = money(value)
    sign = "-" if v < 0 else ""
    whole, frac = f"{abs(v):.2f}".split(".")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return f"{sign}{' '.join(groups)},{frac}"


def format_qty(value) -> str:
    v = Decimal(value).normalize()
    s = f"{v:f}"
    return s.replace(".", ",")
