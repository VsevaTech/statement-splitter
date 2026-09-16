"""Value parsers: amounts (many locales), dates (many formats), currencies."""

from __future__ import annotations

import datetime as dt
import re

_CURRENCY_SYMBOLS = {"₪": "ILS", "$": "USD", "€": "EUR", "£": "GBP", "₽": "RUB", "₴": "UAH"}
_CURRENCY_WORDS = {"NIS": "ILS", "ILS": "ILS", "USD": "USD", "EUR": "EUR", "GBP": "GBP", "RUB": "RUB"}
_AMOUNT_JUNK = re.compile(r"[^\d,.\-+()]")
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d-%m-%Y",
    "%d/%m/%y",
    "%d.%m.%y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
)


def parse_amount(value: object) -> float | None:
    """Parse '1,234.56', '1 234,56', '-12.00', '(12.00)', '₪ 45.90', '12,50 EUR'.

    Returns None when the text is not an amount. Sign is preserved; parentheses
    and a trailing minus mean negative.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if value != value else float(value)  # NaN guard
    text = str(value).strip()
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.endswith("-"):
        negative, text = True, text[:-1]
    text = text.replace("−", "-").replace("\xa0", " ").replace(" ", "")
    text = _AMOUNT_JUNK.sub("", text)
    if not text or not re.search(r"\d", text):
        return None
    if text.startswith("-"):
        negative, text = True, text[1:]
    text = text.lstrip("+")
    if "-" in text or "(" in text or ")" in text:
        return None

    if "," in text and "." in text:
        # The last separator is the decimal separator.
        decimal_comma = text.rfind(",") > text.rfind(".")
        text = text.replace(".", "").replace(",", ".") if decimal_comma else text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            text = parts[0] + "." + parts[1]  # decimal comma
        elif all(len(p) == 3 for p in parts[1:]):
            text = "".join(parts)  # thousands separators
        else:
            return None
    elif text.count(".") > 1:
        parts = text.split(".")
        if all(len(p) == 3 for p in parts[1:]):
            text = "".join(parts)
        else:
            return None
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def detect_currency_in_text(value: str) -> str | None:
    """Return an ISO currency if the amount cell itself carries a symbol/code."""
    for symbol, iso in _CURRENCY_SYMBOLS.items():
        if symbol in value:
            return iso
    match = re.search(r"\b([A-Z]{3})\b", value.upper())
    if match and match.group(1) in _CURRENCY_WORDS:
        return _CURRENCY_WORDS[match.group(1)]
    return None


def normalize_currency(value: object, default: str = "ILS") -> str:
    text = str(value or "").strip().upper()
    if not text:
        return default
    if text in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[text]
    if text in _CURRENCY_WORDS:
        return _CURRENCY_WORDS[text]
    if re.fullmatch(r"[A-Z]{3}", text):
        return text
    return default


def parse_date(value: object) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # ISO with timezone / fractional seconds
    try:
        return dt.datetime.fromisoformat(text).date()
    except ValueError:
        return None
