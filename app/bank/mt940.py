"""Parser wyciagow MT940 z iPKO / iPKO biznes (PKO BP).

Zrodlo formatu: PKO BP, "Struktura pliku wyjsciowego - Raport MT940"
(https://www.pkobp.pl/firmy/wyciagi-mt940/, "Dokumenty do pobrania"):
- kodowanie CP852, jedna wiadomosc = :20: ... :62F: :64: i linia "-";
- :25: /PL + 26 cyfr NRB;
- :61: RRMMDD MMDD C|D kwota(przecinek) N kod(3) NONREF// albo wlasna-ref// nr-operacji;
- :86: 020~00<kod>, potem podpola ~NN (separator tylda): ~20-~25 tytul, ~30 bank,
  ~31 rachunek, ~32-~33 nazwa i adres kontrahenta, ~38 IBAN, ~60 data, ~63 SWRK;
  puste podpole = znak 255 (w CP852 "˙").
Parser toleruje tez UTF-8 / CP1250 i separator "^" (inne banki), zeby dalo sie wczytac plik
przekonwertowany przez inny program.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal

EMPTY_CHARS = "\xff\xa0˙"  # znak 255: w CP852 to twarda spacja (U+00A0), w Latin-1 "ÿ"; w przykladach PKO "˙"


class MT940Error(ValueError):
    pass


@dataclass
class Transaction:
    value_date: dt.date
    booking_date: dt.date
    amount: Decimal  # dodatnia = wplyw (C), ujemna = wyplata (D)
    op_code: str
    reference: str
    bank_ref: str
    title: str = ""
    counterparty: str = ""
    counterparty_account: str = ""
    raw: str = ""

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.raw.encode("utf-8")).hexdigest()


@dataclass
class Statement:
    account: str  # same cyfry NRB (26)
    number: str = ""
    currency: str = "PLN"
    opening: Decimal | None = None
    closing: Decimal | None = None
    transactions: list[Transaction] = field(default_factory=list)


def decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # CP852 wg specyfikacji PKO; jezeli plik ma polskie litery z CP1250, CP852 da w ich miejscu
    # znaki ramek, wiec wybieramy kodowanie, ktore daje wiecej polskich liter.
    pl = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
    best = max(("cp852", "cp1250"), key=lambda enc: sum(ch in pl for ch in data.decode(enc, "replace")))
    return data.decode(best, "replace")


def _amount(s: str) -> Decimal:
    try:
        return Decimal(s.replace(",", "."))
    except Exception as e:  # noqa: BLE001
        raise MT940Error(f"Niepoprawna kwota: {s!r}") from e


def _date6(s: str) -> dt.date:
    return dt.date(2000 + int(s[:2]), int(s[2:4]), int(s[4:6]))


def _balance(v: str) -> tuple[Decimal, str]:
    m = re.match(r"([CD])(\d{6})([A-Z]{3})([\d,]+)", v)
    if not m:
        raise MT940Error(f"Niepoprawne saldo: {v!r}")
    amt = _amount(m.group(4))
    return (-amt if m.group(1) == "D" else amt), m.group(3)


_R61 = re.compile(
    r"(?P<vd>\d{6})(?P<bd>\d{4})?(?P<mark>R?[CD])(?P<fund>[A-Z])?(?P<amt>[\d,]+)"
    r"N(?P<code>[A-Z0-9]{3})(?P<rest>.*)",
    re.S,
)


def _clean(s: str) -> str:
    s = s.strip(" \t\r\n" + EMPTY_CHARS)
    return "" if not s or set(s) <= set(EMPTY_CHARS + " ") else s


def _parse_86(text: str) -> dict[str, str]:
    text = text.replace("\r", "").replace("\n", "")
    sep = "~" if "~" in text else "^"
    out: dict[str, str] = {}
    for part in text.split(sep)[1:]:
        if len(part) < 2 or not part[:2].isdigit():
            continue
        out.setdefault(part[:2], "")
        out[part[:2]] += part[2:]
    return out


def _join(sub: dict[str, str], keys) -> str:
    parts = []
    for k in keys:
        v = _clean(sub.get(k, ""))
        if v:
            parts.append(re.sub(r"\s+", " ", v))
    return " ".join(parts)


def _fields(text: str) -> list[tuple[str, str]]:
    """Dzieli tresc na pola :TAG:wartosc (wartosc moze miec wiele linii)."""
    out: list[tuple[str, str]] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = re.match(r"^:(\d{2}[A-Z]?):(.*)$", line)
        if m:
            out.append((m.group(1), m.group(2)))
        elif line.strip() == "-":
            out.append(("-", ""))
        elif out:
            tag, val = out[-1]
            out[-1] = (tag, val + "\n" + line)
    return out


def parse(data: bytes | str) -> list[Statement]:
    text = decode(data) if isinstance(data, bytes) else data
    statements: list[Statement] = []
    st: Statement | None = None
    tx: Transaction | None = None
    raw61 = ""
    for tag, val in _fields(text):
        if tag == "20":
            st = None
        elif tag == "25":
            digits = re.sub(r"\D", "", val)
            st = Statement(account=digits[-26:] if len(digits) >= 26 else digits)
            statements.append(st)
        elif st is None:
            continue
        elif tag == "28C":
            st.number = val.strip()
        elif tag in ("60F", "60M"):
            st.opening, st.currency = _balance(val.strip())
        elif tag == "61":
            first, _, second = val.partition("\n")
            m = _R61.match(first.strip())
            if not m:
                raise MT940Error(f"Niepoprawna linia :61: {first!r}")
            vd = _date6(m.group("vd"))
            bd = vd
            if m.group("bd"):
                bd = dt.date(vd.year, int(m.group("bd")[:2]), int(m.group("bd")[2:]))
                if (bd - vd).days > 200:
                    bd = bd.replace(year=vd.year - 1)
                elif (vd - bd).days > 200:
                    bd = bd.replace(year=vd.year + 1)
            amt = _amount(m.group("amt"))
            if m.group("mark").endswith("D"):
                amt = -amt
            rest = m.group("rest")
            ref, _, bank_ref = rest.partition("//")
            tx = Transaction(value_date=vd, booking_date=bd, amount=amt, op_code=m.group("code"),
                             reference="" if ref == "NONREF" else ref.strip(), bank_ref=bank_ref.strip())
            raw61 = f"{st.account}|{val}"
            tx.raw = raw61
            st.transactions.append(tx)
        elif tag == "86" and tx is not None:
            sub = _parse_86(val)
            if sub:
                tx.title = _join(sub, ("20", "21", "22", "23", "24", "25", "26", "27", "28", "29"))
                tx.counterparty = _join(sub, ("32", "33"))
                acc = _clean(sub.get("38", "")) or ""
                acc = re.sub(r"\s", "", acc)
                if not acc:
                    bank = re.sub(r"\D", "", sub.get("30", ""))
                    rest = re.sub(r"\D", "", sub.get("31", ""))
                    acc = bank + rest if bank and rest else ""
                tx.counterparty_account = acc
            else:
                tx.title = re.sub(r"\s+", " ", _clean(val))
            tx.raw = raw61 + "|" + val
            tx = None
        elif tag == "62F":
            st.closing, _ = _balance(val.strip())
            tx = None
        elif tag == "-":
            tx = None
    if not statements:
        raise MT940Error("To nie jest plik MT940 (brak pola :25: z numerem rachunku).")
    return statements
