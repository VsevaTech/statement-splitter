"""Value parsers: amounts (many locales), dates (many formats), currencies."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from app.money import to_decimal

_CURRENCY_SYMBOLS = {"₪": "ILS", "$": "USD", "€": "EUR", "£": "GBP", "₽": "RUB", "₴": "UAH"}
_CURRENCY_WORDS = {"NIS": "ILS", "ILS": "ILS", "USD": "USD", "EUR": "EUR", "GBP": "GBP", "RUB": "RUB"}
_CURRENCY_TOKENS = "|".join([re.escape(s) for s in _CURRENCY_SYMBOLS] + [r"\b[A-Z]{3}\b", r"\b(?:US|C|A|NZ|HK|S)\$"])
_CURRENCY_AFFIX = re.compile(rf"^(?:{_CURRENCY_TOKENS})\.?\s*|\s*(?:{_CURRENCY_TOKENS})$")
_DRCR = re.compile(r"\s+(CR|DR)\.?$|(?<=\d)(CR|DR)$", re.IGNORECASE)
_NUMBER_CHARS = re.compile(r"[\d.,' ’]+")
_SCIENTIFIC = re.compile(r"[+-]?\d+(?:\.\d+)?[eE][+-]?\d+")
# Cells that mean "no value" in debit/credit columns.
EMPTY_AMOUNT_MARKERS = frozenset({"-", "–", "—", "--"})
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


def parse_amount(value: object) -> Decimal | None:
    """Parse '1,234.56', '1 234,56', '-12.00', '(12.00)', '₪ 45.90', '12,50 EUR' into an exact Decimal.

    Returns None when the text is not an amount — never 0 for garbage. Sign is
    preserved; parentheses, a trailing minus and a trailing ``DR`` mean negative.
    Only currency symbols/codes and ``CR``/``DR`` markers may surround the number:
    anything else (``12.3O``, ``12abc``) is rejected rather than silently cleaned.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return to_decimal(value)
    text = str(value).strip().replace("\xa0", " ").replace(" ", " ").replace("−", "-")
    if not text:
        return None
    if _SCIENTIFIC.fullmatch(text):  # typed numeric spreadsheet cells, e.g. '1e+16'
        return to_decimal(text)

    negative = False
    marker = _DRCR.search(text)
    if marker:
        negative = (marker.group(1) or marker.group(2)).upper() == "DR"
        text = text[: marker.start()].strip()
    text = _strip_currency(text)
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    text = _strip_currency(text)
    if text.endswith("-"):
        negative, text = True, text[:-1].strip()
    if text.startswith("-"):
        negative, text = True, text[1:].strip()
    elif text.startswith("+"):
        text = text[1:].strip()
    text = _strip_currency(text)
    if not _NUMBER_CHARS.fullmatch(text) or not re.search(r"\d", text):
        return None
    text = text.replace(" ", "").replace("'", "").replace("’", "")

    if "," in text and "." in text:
        # The last separator is the decimal separator; the other one must group by thousands.
        decimal_comma = text.rfind(",") > text.rfind(".")
        group, dec = (".", ",") if decimal_comma else (",", ".")
        if text.count(dec) != 1:
            return None
        integer, fraction = text.split(dec)
        groups = integer.split(group)
        if not groups[0] or not all(len(g) == 3 for g in groups[1:]):
            return None
        text = "".join(groups) + "." + fraction
    elif "," in text:
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            text = parts[0] + "." + parts[1]  # decimal comma
        elif parts[0] and all(len(p) == 3 for p in parts[1:]):
            text = "".join(parts)  # thousands separators
        else:
            return None
    elif text.count(".") > 1:
        parts = text.split(".")
        if parts[0] and all(len(p) == 3 for p in parts[1:]):
            text = "".join(parts)
        else:
            return None
    if not re.fullmatch(r"\d*\.?\d*", text) or text in ("", "."):
        return None
    number = to_decimal(text)
    if number is None:
        return None
    return -number if negative else number


def _strip_currency(text: str) -> str:
    text = text.strip()
    for _ in range(2):
        text = _CURRENCY_AFFIX.sub("", text).strip()
    return text


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
