"""Exact money helpers.

Every monetary value in the app is a :class:`decimal.Decimal`. Binary floats are
never used for amounts, totals, balances or differences. Values keep the scale
they were written with (``"12.30"`` stays ``Decimal("12.30")``) — the app does
not assume that every currency has exactly two decimal places.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

ZERO = Decimal("0.00")
_CENT = Decimal("0.01")


def to_decimal(value: object) -> Decimal | None:
    """Convert a stored/typed value to Decimal without passing through binary float math.

    * ``str`` / ``int`` / ``Decimal`` are converted exactly;
    * a ``float`` (only produced by spreadsheet cells) is converted through its
      shortest round-trip ``repr`` — ``0.1`` becomes ``Decimal("0.1")``, not
      ``Decimal("0.1000000000000000055511151231257827")``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return Decimal(repr(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def dsum(values: Iterable[Decimal]) -> Decimal:
    """Exact sum; an empty sum is ``0.00``."""
    total = ZERO
    for v in values:
        total += v
    return total


def canonical(value: Decimal) -> str:
    """Deterministic plain string (no exponent) used for storage and JSON: ``"-12.40"``."""
    if not isinstance(value, Decimal):  # pragma: no cover - defensive
        raise TypeError(f"expected Decimal, got {type(value).__name__}")
    if value == 0:
        value = abs(value)  # never "-0.00"
    return format(value, "f")


def json_money(value: Decimal | None) -> str | None:
    return None if value is None else canonical(value)


def display_scale(value: Decimal) -> Decimal:
    """At least two decimals for display; more if the value really has them (no rounding)."""
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent > -2:
        value = value.quantize(_CENT)
    if value == 0:
        value = abs(value)
    return value


def format_money(value: object, *, signed: bool = False) -> str:
    """Human format with thousands separators: ``12,450.30`` (``+``/``-`` sign when `signed`)."""
    d = to_decimal(value)
    if d is None:
        return "—"
    d = display_scale(d)
    text = format(d, ",f")
    if signed and d > 0:
        text = "+" + text
    return text


def excel_number_format(values: Iterable[Decimal]) -> str:
    """`#,##0.00`, or more decimal places when some value needs them (e.g. 3-decimal currencies)."""
    places = 2
    for v in values:
        exponent = v.as_tuple().exponent
        if isinstance(exponent, int):
            places = max(places, -exponent)
    return "#,##0." + "0" * min(places, 10)
