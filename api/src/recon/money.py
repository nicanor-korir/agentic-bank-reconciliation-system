"""Money handling.

NON-NEGOTIABLE #8: money is Decimal, stored as integer minor units. This module
is the only place in the codebase permitted to convert between the two. A test
asserts that no float literal or float() call appears anywhere under matching/,
db/ or ingest/ -- if you need arithmetic on money, do it here or in minor units.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

# USD. If multi-currency lands (NOTES.md 0.5.7) this becomes a lookup.
MINOR_DIGITS: dict[str, int] = {"USD": 2, "EUR": 2, "GBP": 2, "KES": 2, "ZAR": 2, "JPY": 0}
DEFAULT_CURRENCY = "USD"


class MoneyError(ValueError):
    """Raised when a value cannot be represented exactly in minor units."""


def minor_digits(currency: str) -> int:
    try:
        return MINOR_DIGITS[currency.upper()]
    except KeyError as exc:
        raise MoneyError(f"unknown currency {currency!r}") from exc


def to_minor(amount: Decimal | str | int, currency: str = DEFAULT_CURRENCY) -> int:
    """Convert a decimal amount to signed integer minor units.

    Rejects anything that would lose precision, rather than rounding silently --
    a statement that does not parse exactly is a bug in the parser, not an
    invitation to round.
    """
    if isinstance(amount, float):  # pragma: no cover - defended by test_no_floats
        raise MoneyError("float is never a valid money input")
    try:
        dec = Decimal(amount) if not isinstance(amount, Decimal) else amount
    except InvalidOperation as exc:
        raise MoneyError(f"not a decimal amount: {amount!r}") from exc
    if not dec.is_finite():
        raise MoneyError(f"non-finite amount: {amount!r}")

    scaled = dec.scaleb(minor_digits(currency))
    if scaled != scaled.to_integral_value():
        raise MoneyError(f"{amount} has more precision than {currency} can represent")
    return int(scaled)


def from_minor(minor: int, currency: str = DEFAULT_CURRENCY) -> Decimal:
    """Convert signed integer minor units back to a Decimal amount."""
    if not isinstance(minor, int) or isinstance(minor, bool):
        raise MoneyError(f"minor units must be int, got {type(minor).__name__}")
    return Decimal(minor).scaleb(-minor_digits(currency))


def format_minor(minor: int, currency: str = DEFAULT_CURRENCY) -> str:
    """Human-readable, for rationales and the UI. Never for comparison."""
    digits = minor_digits(currency)
    sign = "-" if minor < 0 else ""
    units, sub = divmod(abs(minor), 10**digits)
    body = f"{units:,}" if digits == 0 else f"{units:,}.{sub:0{digits}d}"
    return f"{sign}{body} {currency.upper()}"
