"""Faktura w strukturze FA(3) (schemat 1-0E, przestrzen nazw http://crd.gov.pl/wzor/2025/06/25/13775/).

Obslugiwane rodzaje: VAT, KOR (i KOR_ROZ), ZAL, ROZ. Proforma nie jest faktura i nie idzie do KSeF.
Kolejnosc elementow jak w XSD; testy waliduja wynik schematem MF (tests/ksef_xsd).
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from lxml import etree

from .. import calc

NS = "http://crd.gov.pl/wzor/2025/06/25/13775/"
E = "{%s}" % NS

# stawka w programie -> (P_12, pole netto P_13_x, pole VAT P_14_x albo None)
RATES = {
    "23": ("23", "P_13_1", "P_14_1"),
    "8": ("8", "P_13_2", "P_14_2"),
    "5": ("5", "P_13_3", "P_14_3"),
    "0": ("0 KR", "P_13_6_1", None),
    "zw": ("zw", "P_13_7", None),
    "np": ("np I", "P_13_8", None),
}
# kolejnosc pol P_13/P_14 w XSD
SUM_ORDER = ["P_13_1", "P_14_1", "P_13_2", "P_14_2", "P_13_3", "P_14_3", "P_13_6_1", "P_13_7", "P_13_8"]
PAYMENT = {"cash": 1, "card": 2, "transfer": 6}
EU = {"AT", "BE", "BG", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "EL", "HR", "HU", "IE", "IT", "LV", "LT",
      "LU", "MT", "NL", "PT", "RO", "SK", "SI", "ES", "SE", "XI"}


class Fa3Error(ValueError):
    pass


def amount(v: Decimal) -> str:
    return f"{calc.money(v):.2f}"


def plain(v: Decimal, max_frac: int) -> str:
    q = Decimal(1).scaleb(-max_frac)
    s = f"{Decimal(v).quantize(q):f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _sub(parent, tag, text=None, **attrs):
    el = etree.SubElement(parent, E + tag, **attrs)
    if text is not None:
        el.text = str(text)
    return el


def _clean(text: str, limit: int = 256) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


def kod_rodzaju(inv) -> str:
    if inv.kind == "vat":
        return "VAT"
    if inv.kind == "advance":
        return "ZAL"
    if inv.kind == "final":
        return "ROZ"
    if inv.kind == "correction":
        rel = inv.related
        return "KOR_ROZ" if rel is not None and rel.kind == "final" else "KOR"
    raise Fa3Error(f"Rodzaj „{inv.kind_label}” nie jest wysyłany do KSeF.")


def _address(parent, tag, lines: list[str], country: str = "PL"):
    lines = [_clean(x, 512) for x in lines if x and x.strip()]
    if not lines:
        return
    a = _sub(parent, tag)
    _sub(a, "KodKraju", country or "PL")
    _sub(a, "AdresL1", lines[0])
    if len(lines) > 1:
        _sub(a, "AdresL2", ", ".join(lines[1:]))


def _buyer_id(parent, inv):
    nip = re.sub(r"[\s-]", "", inv.buyer_nip or "")
    country = (inv.contractor.country if inv.contractor is not None else "PL") or "PL"
    di = _sub(parent, "DaneIdentyfikacyjne")
    if re.fullmatch(r"\d{10}", nip):
        _sub(di, "NIP", nip)
    elif re.fullmatch(r"[A-Z]{2}[0-9A-Z+*]{2,13}", nip) and nip[:2] in EU:
        _sub(di, "KodUE", nip[:2])
        _sub(di, "NrVatUE", nip[2:])
    elif nip:
        if country and country != "PL":
            _sub(di, "KodKraju", country)
        _sub(di, "NrID", nip[:50])
    else:
        _sub(di, "BrakID", "1")
    _sub(di, "Nazwa", _clean(inv.buyer_name, 512))
    return country


def _sums(fa, rows: list[dict]):
    vals: dict[str, Decimal] = {}
    for r in rows:
        if r["rate"] not in RATES:
            raise Fa3Error(f"Stawka VAT „{r['rate']}” nie jest obsługiwana w KSeF.")
        _, p13, p14 = RATES[r["rate"]]
        vals[p13] = vals.get(p13, Decimal("0")) + r["net"]
        if p14:
            vals[p14] = vals.get(p14, Decimal("0")) + r["vat"]
    # P_13_x i P_14_x wystepuja parami (P_14_x jest wymagane, gdy jest P_13_x).
    for p13, p14 in (("P_13_1", "P_14_1"), ("P_13_2", "P_14_2"), ("P_13_3", "P_14_3")):
        if p13 in vals:
            vals.setdefault(p14, Decimal("0"))
    for key in SUM_ORDER:
        if key in vals:
            _sub(fa, key, amount(vals[key]))


def _adnotacje(fa, inv, items):
    a = _sub(fa, "Adnotacje")
    _sub(a, "P_16", "2")  # metoda kasowa: nie
    _sub(a, "P_17", "2")  # samofakturowanie: nie
    _sub(a, "P_18", "2")  # odwrotne obciazenie: nie
    _sub(a, "P_18A", "1" if getattr(inv, "split_payment", False) else "2")
    z = _sub(a, "Zwolnienie")
    if any(i.vat_rate == "zw" for i in items):
        basis = _clean(getattr(inv, "exemption_basis", "") or "")
        if not basis:
            raise Fa3Error("Pozycja ze stawką „zw”: wpisz podstawę zwolnienia z VAT (pole na fakturze).")
        _sub(z, "P_19", "1")
        _sub(z, "P_19A", basis)
    else:
        _sub(z, "P_19N", "1")
    nst = _sub(a, "NoweSrodkiTransportu")
    _sub(nst, "P_22N", "1")  # brak wewnatrzwspolnotowej dostawy nowych srodkow transportu
    _sub(a, "P_23", "2")  # procedura trojstronna: nie
    pm = _sub(a, "PMarzy")
    _sub(pm, "P_PMarzyN", "1")  # bez procedury marzy


def _row(fa, n: int, it, prices_gross: bool, before: bool = False):
    w = _sub(fa, "FaWiersz")
    _sub(w, "NrWierszaFa", n)
    _sub(w, "P_7", _clean(it.name, 512))
    if getattr(it, "product", None) is not None and it.product.code:
        _sub(w, "Indeks", _clean(it.product.code, 50))
    _sub(w, "P_8A", _clean(it.unit or "szt.", 256))
    _sub(w, "P_8B", plain(it.quantity, 6))
    if prices_gross:
        _sub(w, "P_9B", plain(it.unit_price, 8))
        _sub(w, "P_11A", amount(it.gross))
    else:
        _sub(w, "P_9A", plain(it.unit_price, 8))
        _sub(w, "P_11", amount(it.net))
    _sub(w, "P_12", RATES[it.vat_rate][0])
    if before:
        _sub(w, "StanPrzed", "1")


def build(inv, *, now: dt.datetime | None = None, ksef_number_of=None) -> bytes:
    """XML FA(3) faktury. ksef_number_of(inv) -> numer KSeF faktury powiazanej albo "" (gdy nie byla w KSeF)."""
    if inv.status != "issued" or not inv.number:
        raise Fa3Error("Do KSeF wysyła się tylko wystawioną fakturę z numerem.")
    rodzaj = kod_rodzaju(inv)
    c = inv.company
    if not re.fullmatch(r"\d{10}", c.nip or ""):
        raise Fa3Error(f"Firma {c.short_name}: brak poprawnego NIP w Ustawieniach.")
    ksef_number_of = ksef_number_of or (lambda i: getattr(i, "ksef_number", "") or "")
    now = now or dt.datetime.now(dt.timezone.utc)

    root = etree.Element(E + "Faktura", nsmap={None: NS})
    nag = _sub(root, "Naglowek")
    _sub(nag, "KodFormularza", "FA", kodSystemowy="FA (3)", wersjaSchemy="1-0E")
    _sub(nag, "WariantFormularza", "3")
    _sub(nag, "DataWytworzeniaFa", now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _sub(nag, "SystemInfo", "Sygnavo")

    p1 = _sub(root, "Podmiot1")
    di = _sub(p1, "DaneIdentyfikacyjne")
    _sub(di, "NIP", c.nip)
    _sub(di, "Nazwa", _clean(c.name, 512))
    _address(p1, "Adres", c.address_lines or [c.name])
    if c.email or c.phone:
        dk = _sub(p1, "DaneKontaktowe")
        if c.email:
            _sub(dk, "Email", c.email.strip())
        if c.phone:
            _sub(dk, "Telefon", re.sub(r"[^\d+ ]", "", c.phone)[:16])

    p2 = _sub(root, "Podmiot2")
    country = _buyer_id(p2, inv)
    _address(p2, "Adres", (inv.buyer_address or "").splitlines(), country)
    _sub(p2, "JST", "2")
    _sub(p2, "GV", "2")

    fa = _sub(root, "Fa")
    _sub(fa, "KodWaluty", "PLN")
    _sub(fa, "P_1", inv.issue_date.isoformat())
    if inv.issue_place:
        _sub(fa, "P_1M", _clean(inv.issue_place))
    _sub(fa, "P_2", _clean(inv.number))
    # P_6: data sprzedazy, gdy rozni sie od daty wystawienia (zaliczkowa: data otrzymania zaplaty)
    if inv.sale_date and (inv.sale_date != inv.issue_date or rodzaj == "ZAL"):
        _sub(fa, "P_6", inv.sale_date.isoformat())

    rows = calc.invoice_summary(inv)
    _sums(fa, rows)
    _sub(fa, "P_15", amount(inv.total_gross))
    items = inv.after_items
    _adnotacje(fa, inv, list(inv.items))
    _sub(fa, "RodzajFaktury", rodzaj)

    if rodzaj in ("KOR", "KOR_ROZ"):
        rel = inv.related
        if rel is None:
            raise Fa3Error("Korekta bez faktury korygowanej.")
        _sub(fa, "PrzyczynaKorekty", _clean(inv.correction_reason))
        _sub(fa, "TypKorekty", "2")  # skutek w dacie wystawienia faktury korygujacej
        d = _sub(fa, "DaneFaKorygowanej")
        _sub(d, "DataWystFaKorygowanej", rel.issue_date.isoformat())
        _sub(d, "NrFaKorygowanej", _clean(rel.number))
        knum = ksef_number_of(rel)
        if knum:
            _sub(d, "NrKSeF", "1")
            _sub(d, "NrKSeFFaKorygowanej", knum)
        else:
            _sub(d, "NrKSeFN", "1")

    if rodzaj == "ROZ":
        for adv in inv.advances:
            fz = _sub(fa, "FakturaZaliczkowa")
            knum = ksef_number_of(adv)
            if knum:
                _sub(fz, "NrKSeFFaZaliczkowej", knum)
            else:
                _sub(fz, "NrKSeFZN", "1")
                _sub(fz, "NrFaZaliczkowej", _clean(adv.number))

    if rodzaj == "ZAL":
        pass  # pozycje zamowienia ida do Zamowienie (nizej), FaWiersz nie wystepuje
    elif rodzaj in ("KOR", "KOR_ROZ"):
        for n, it in enumerate(inv.before_items, start=1):
            _row(fa, n, it, inv.prices_gross, before=True)
        for n, it in enumerate(items, start=1):
            _row(fa, n, it, inv.prices_gross)
    else:
        for n, it in enumerate(items, start=1):
            _row(fa, n, it, inv.prices_gross)

    _platnosc(fa, inv)

    if rodzaj == "ZAL":
        z = _sub(fa, "Zamowienie")
        _sub(z, "WartoscZamowienia", amount(inv.order_total or sum((i.gross for i in items), Decimal("0"))))
        for n, it in enumerate(items, start=1):
            w = _sub(z, "ZamowienieWiersz")
            _sub(w, "NrWierszaZam", n)
            _sub(w, "P_7Z", _clean(it.name, 512))
            _sub(w, "P_8AZ", _clean(it.unit or "szt.", 256))
            _sub(w, "P_8BZ", plain(it.quantity, 6))
            _sub(w, "P_9AZ", plain(calc.money(it.net / it.quantity) if it.quantity else it.net, 8))
            _sub(w, "P_11NettoZ", amount(it.net))
            _sub(w, "P_11VatZ", amount(it.vat))
            _sub(w, "P_12Z", RATES[it.vat_rate][0])

    if inv.notes:
        st = _sub(root, "Stopka")
        info = _sub(st, "Informacje")
        _sub(info, "StopkaFaktury", _clean(inv.notes, 3500))

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _platnosc(fa, inv):
    if inv.kind == "correction":
        return
    p = _sub(fa, "Platnosc")
    if inv.kind == "advance":
        # faktura zaliczkowa dokumentuje otrzymana zaplate (data = P_6, jak w przykladzie MF nr 10)
        _sub(p, "Zaplacono", "1")
        _sub(p, "DataZaplaty", inv.sale_date.isoformat())
    elif inv.due_date:
        t = _sub(p, "TerminPlatnosci")
        _sub(t, "Termin", inv.due_date.isoformat())
    if inv.payment_method in PAYMENT:
        _sub(p, "FormaPlatnosci", PAYMENT[inv.payment_method])
    else:
        _sub(p, "PlatnoscInna", "1")
        _sub(p, "OpisPlatnosci", inv.payment_label)
    nrb = re.sub(r"[\s-]", "", inv.company.bank_account or "")
    if inv.payment_method == "transfer" and 10 <= len(nrb) <= 34:
        rb = _sub(p, "RachunekBankowy")
        _sub(rb, "NrRB", nrb[2:] if nrb[:2] == "PL" else nrb)
        if inv.company.bank_name:
            _sub(rb, "NazwaBanku", _clean(inv.company.bank_name))
