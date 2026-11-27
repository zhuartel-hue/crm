"""Faktury kosztowe z KSeF: odczyt najwazniejszych pol z XML FA(2)/FA(3) do widoku i listy (bez walidacji schematu)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from lxml import etree

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_comments=True)


def _root(xml: bytes | str):
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    return etree.fromstring(data, _PARSER)


def _first(node, path: str) -> str:
    """path w postaci "A/B/C" po nazwach lokalnych (bez przestrzeni nazw)."""
    expr = "./" + "/".join(f"*[local-name()='{p}']" for p in path.split("/"))
    hits = node.xpath(expr) if node is not None else []
    return (hits[0].text or "").strip() if hits else ""


def _all(node, path: str) -> list:
    expr = ".//" + "/".join(f"*[local-name()='{p}']" for p in path.split("/"))
    return node.xpath(expr)


def _dec(text: str) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _date(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


# Pola P_13_x (netto) i P_14_x (VAT) w FA(2)/FA(3) wg stawek, do zestawienia na podgladzie.
_RATE_FIELDS = [("23", "P_13_1", "P_14_1"), ("8", "P_13_2", "P_14_2"), ("5", "P_13_3", "P_14_3"),
                ("4", "P_13_4", "P_14_4"), ("ryczałt taxi", "P_13_5", "P_14_5"), ("0", "P_13_6_1", ""),
                ("0 WDT", "P_13_6_2", ""), ("0 eksport", "P_13_6_3", ""), ("zw", "P_13_7", ""),
                ("np", "P_13_8", ""), ("np (art. 100)", "P_13_9", ""), ("oo", "P_13_10", ""), ("marża", "P_13_11", "")]
PAYMENT_FORMS = {"1": "gotówka", "2": "karta", "3": "bon", "4": "czek", "5": "kredyt", "6": "przelew", "7": "mobilna"}


def _party(node) -> dict:
    if node is None:
        return {}
    addr = node.xpath("./*[local-name()='Adres']")
    address = [x for x in (_first(addr[0], "AdresL1"), _first(addr[0], "AdresL2")) if x] if addr else []
    return dict(nip=_first(node, "DaneIdentyfikacyjne/NIP"), name=_first(node, "DaneIdentyfikacyjne/Nazwa"),
                address=address)


def parse(xml: bytes | str) -> dict:
    root = _root(xml)
    seller = (_all(root, "Podmiot1") or [None])[0]
    buyer = (_all(root, "Podmiot2") or [None])[0]
    fa = (_all(root, "Fa") or [None])[0]
    address = _party(seller).get("address", [])
    rates = []
    for label, net_f, vat_f in _RATE_FIELDS:
        net = _dec(_first(fa, net_f)) if fa is not None else None
        if net is None:
            continue
        vat = (_dec(_first(fa, vat_f)) if vat_f else None) or Decimal("0")
        rates.append(dict(rate=label, net=net, vat=vat, gross=net + vat))
    pay_form = _first(fa, "Platnosc/FormaPlatnosci") if fa is not None else ""
    items = []
    for w in (_all(fa, "FaWiersz") if fa is not None else []):
        items.append(dict(
            name=_first(w, "P_7"), unit=_first(w, "P_8A"), quantity=_dec(_first(w, "P_8B")),
            unit_price=_dec(_first(w, "P_9A")) or _dec(_first(w, "P_9B")),
            net=_dec(_first(w, "P_11")), gross=_dec(_first(w, "P_11A")), rate=_first(w, "P_12"),
        ))
        items[-1]["value"] = items[-1]["net"] if items[-1]["net"] is not None else items[-1]["gross"]
    terms = _all(fa, "Platnosc/TerminPlatnosci") if fa is not None else []
    due = _date(_first(terms[0], "Termin")) if terms else None
    accounts = _all(fa, "Platnosc/RachunekBankowy") if fa is not None else []
    return dict(
        number=_first(fa, "P_2"), issue_date=_date(_first(fa, "P_1")), sale_date=_date(_first(fa, "P_6")),
        currency=_first(fa, "KodWaluty") or "PLN", kind=_first(fa, "RodzajFaktury"),
        seller_nip=_first(seller, "DaneIdentyfikacyjne/NIP"), seller_name=_first(seller, "DaneIdentyfikacyjne/Nazwa"),
        seller_address=address, gross=_dec(_first(fa, "P_15")), due_date=due,
        bank_account=_first(accounts[0], "NrRB") if accounts else "",
        paid=_first(fa, "Platnosc/Zaplacono") == "1", items=items,
        buyer=_party(buyer), rates=rates, payment_form=PAYMENT_FORMS.get(pay_form, pay_form),
        system=_first(root, "Naglowek/SystemInfo"), created=_first(root, "Naglowek/DataWytworzeniaFa")[:10],
        place=_first(fa, "P_1M") if fa is not None else "",
    )
