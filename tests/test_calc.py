from decimal import Decimal as D

from app import calc


def test_vat_from_gross():
    line = calc.vat_line(D("1"), D("123.00"), "23", prices_gross=True)
    assert (line.net, line.vat, line.gross) == (D("100.00"), D("23.00"), D("123.00"))


def test_vat_from_net():
    line = calc.vat_line(D("2"), D("49.99"), "23", prices_gross=False)
    assert (line.net, line.vat, line.gross) == (D("99.98"), D("23.00"), D("122.98"))


def test_exempt_rate():
    line = calc.vat_line(D("1"), D("500"), "zw", prices_gross=False)
    assert line.vat == 0 and line.gross == D("500.00")


def test_parse_polish_number():
    assert calc.parse_decimal("12 345,67") == D("12345.67")
    assert calc.parse_decimal("") is None


def test_format_money():
    assert calc.format_money(D("1234567.5")) == "1 234 567,50"


def test_amount_in_words():
    assert calc.amount_in_words(D("1")) == "jeden złoty 00/100"
    assert calc.amount_in_words(D("2.05")) == "dwa złote 05/100"
    assert calc.amount_in_words(D("1000")) == "tysiąc złotych 00/100"
    assert calc.amount_in_words(D("36150.00")) == "trzydzieści sześć tysięcy sto pięćdziesiąt złotych 00/100"
    assert calc.amount_in_words(D("212")) == "dwieście dwanaście złotych 00/100"
    assert calc.amount_in_words(D("1222014.99")) == "milion dwieście dwadzieścia dwa tysiące czternaście złotych 99/100"
