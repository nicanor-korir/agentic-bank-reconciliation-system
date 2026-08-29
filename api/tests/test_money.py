from decimal import Decimal

import pytest

from recon.money import MoneyError, format_minor, from_minor, to_minor


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("0.00", 0),
        ("1450.00", 145_000),
        ("-25.00", -2_500),
        ("0.01", 1),
        ("999999.99", 99_999_999),
        ("-0.01", -1),
    ],
)
def test_to_minor_exact(amount, expected):
    assert to_minor(Decimal(amount)) == expected


def test_round_trip_preserves_value():
    for cents in (0, 1, -1, 145_000, -99_999_999):
        assert to_minor(from_minor(cents)) == cents


def test_float_is_never_accepted():
    with pytest.raises(MoneyError, match="float"):
        to_minor(1450.00)  # type: ignore[arg-type]


def test_excess_precision_is_an_error_not_a_rounding():
    # 0.005 USD cannot be represented. Rounding it silently is how a
    # reconciliation system loses a cent per transaction.
    with pytest.raises(MoneyError, match="precision"):
        to_minor(Decimal("10.005"))


def test_unknown_currency_rejected():
    with pytest.raises(MoneyError, match="unknown currency"):
        to_minor(Decimal("1.00"), "XXX")


def test_zero_decimal_currency():
    assert to_minor(Decimal("500"), "JPY") == 500
    assert format_minor(500, "JPY") == "500 JPY"


def test_format_is_human_readable():
    assert format_minor(145_000) == "1,450.00 USD"
    assert format_minor(-2_500) == "-25.00 USD"
