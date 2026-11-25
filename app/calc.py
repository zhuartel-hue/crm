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


def vat_summary(items) -> list[dict]:
    """Zestawienie wg stawek (do tabelki pod pozycjami)."""
    order = ["23", "8", "5", "0", "zw", "np"]
    sums: dict[str, dict] = {}
    for it in items:
        s = sums.setdefault(it.vat_rate, {"rate": it.vat_rate, "net": Decimal("0"), "vat": Decimal("0"), "gross": Decimal("0")})
        s["net"] += it.net
        s["vat"] += it.vat
        s["gross"] += it.gross
    return sorted(sums.values(), key=lambda s: order.index(s["rate"]) if s["rate"] in order else 99)


def recalculate(invoice) -> None:
    """Przelicza pozycje i sumy faktury na miejscu."""
    total_net = total_vat = total_gross = Decimal("0")
    for pos, it in enumerate(invoice.items, start=1):
        it.position = pos
        line = vat_line(it.quantity, it.unit_price, it.vat_rate, invoice.prices_gross)
        it.purchase_price = None
        it.margin = None
        it.net, it.vat, it.gross = line.net, line.vat, line.gross
        total_net += line.net
        total_vat += line.vat
        total_gross += line.gross
    invoice.total_net = total_net
    invoice.total_vat = total_vat
    invoice.total_gross = total_gross
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
    amount = money(amount)
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
