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


def test_margin_vat_inside_margin():
    # Kupione za 30 000, sprzedane za 36 150: marza 6 150, VAT = 6150*23/123 = 1150
    line = calc.margin_line(D("1"), D("36150"), D("30000"))
    assert line.margin == D("6150.00")
    assert line.vat == D("1150.00")
    assert line.gross == D("36150.00")


def test_negative_margin_no_vat():
    line = calc.margin_line(D("1"), D("20000"), D("25000"))
    assert line.margin == D("-5000.00")
    assert line.vat == 0


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
