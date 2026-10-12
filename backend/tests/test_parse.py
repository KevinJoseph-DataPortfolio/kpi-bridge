from decimal import Decimal

from kpibridge.normalize.parse import infer_convention, infer_date_order, parse_date, parse_number


def test_number_conventions_exact():
    assert parse_number("1 234,56", "comma").value == Decimal("1234.56")
    assert parse_number("1 234,56 €", "comma").value == Decimal("1234.56")
    assert parse_number("1 234,56 €", "comma").currency == "EUR"
    assert parse_number("1,234.56", "dot").value == Decimal("1234.56")
    assert parse_number("(12.50)", "dot").value == Decimal("-12.50")
    assert parse_number("-0,10", "comma").value == Decimal("-0.10")
    assert parse_number("12,5 %", "comma").percent is True
    assert parse_number("abc", "dot") is None
    # 0.1 + 0.2 exact in decimal
    assert parse_number("0.1", "dot").value + parse_number("0.2", "dot").value == Decimal("0.3")


def test_convention_ambiguity_detected():
    conv, ambiguous, _, examples = infer_convention(["1,234", "2,500", "3,750"])
    assert ambiguous and examples
    conv, ambiguous, _, _ = infer_convention(["1,5", "2,25", "10"])
    assert conv == "comma" and not ambiguous
    conv, ambiguous, _, _ = infer_convention(["1.5", "2.25", "1,234.5"])
    assert conv == "dot" and not ambiguous


def test_date_order_ambiguous_until_resolved():
    order, ambiguous, examples = infer_date_order(["03/04/2026", "05/06/2026"])
    assert ambiguous and "03/04/2026" in examples
    order, ambiguous, _ = infer_date_order(["03/04/2026", "25/04/2026"])
    assert order == "DMY" and not ambiguous
    order, ambiguous, _ = infer_date_order(["04/25/2026", "03/04/2026"])
    assert order == "MDY" and not ambiguous
    assert parse_date("03/04/2026", "DMY").isoformat() == "2026-04-03"
    assert parse_date("03/04/2026", "MDY").isoformat() == "2026-03-04"
    assert parse_date("03/04/2026", None) is None  # never a hidden assumption
    assert parse_date("2026年3月4日", None).isoformat() == "2026-03-04"
    assert parse_date("4 марта 2026", None).isoformat() == "2026-03-04"
