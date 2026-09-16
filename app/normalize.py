"""Merchant normalization.

Goal: `GOOGLE*GSUITE 839291` and `GOOGLE GSUITE 482901` -> `GOOGLE GSUITE`.

The function is deliberately conservative. It only removes tokens that are
clearly transaction noise (reference numbers, dates, card fragments, payment
processor prefixes). It never fuzzy-matches two different names: if two
descriptions normalize to different keys they stay different merchants, and a
saved rule for one will not be applied to the other.
"""

from __future__ import annotations

import re

_PROCESSOR_PREFIXES = (
    "PAYPAL",
    "PP",
    "SQ",
    "SP",
    "GOOGLE PAY",
    "APPLE PAY",
    "POS",
    "PURCHASE",
    "CARD PAYMENT",
    "CARD PURCHASE",
    "DEBIT CARD",
    "VISA",
    "MASTERCARD",
    "MC",
    "TRANSFER TO",
    "PAYMENT TO",
)
_NOISE_TOKENS = {"LTD", "LTD.", "LLC", "INC", "INC.", "CO", "CO.", "GMBH", "BV", "B.V.", "SARL", "LIMITED"}
_REF_TOKEN = re.compile(r"^[#*]?(?:\d{4,}|\d+[\-/.:][\d\-/.:]+|[A-Z]{0,2}\d{4,}[A-Z0-9]*|[A-Z0-9]*\d{5,}[A-Z0-9]*)$")
_CARD_FRAGMENT = re.compile(r"^(?:X{2,}|\*{2,})\d{2,4}$|^\d{4}X{4,}$", re.IGNORECASE)
_URL_SUFFIX = re.compile(r"\.(COM|IO|CO|NET|ORG|IL|DE|UK|CO\.IL)\b")


def normalize_merchant(description: str) -> str:
    """Return a stable merchant key for a raw statement description."""
    if not description:
        return ""
    text = description.upper()
    text = text.replace("\xa0", " ")
    # Split fused processor names: "GOOGLE*GSUITE" -> "GOOGLE GSUITE", "PAYPAL *SPOTIFY" -> "PAYPAL SPOTIFY"
    text = re.sub(r"\s*[*|_]+\s*", " ", text)
    text = _URL_SUFFIX.sub("", text)
    text = re.sub(r"[^\w\s&'.\-/]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    for prefix in _PROCESSOR_PREFIXES:
        if text.startswith(prefix + " ") and len(text) > len(prefix) + 3:
            text = text[len(prefix) + 1 :]
            break

    tokens = [t.strip(".,-/") for t in text.split(" ")]
    kept: list[str] = []
    for tok in tokens:
        if not tok:
            continue
        if _CARD_FRAGMENT.match(tok):
            continue
        if _REF_TOKEN.match(tok):
            continue
        if tok in _NOISE_TOKENS and kept:
            continue
        kept.append(tok)

    # Trailing pure-digit tokens are branch / reference numbers ("MYSTERY SHOP 42").
    while len(kept) > 1 and kept[-1].isdigit():
        kept.pop()

    key = " ".join(kept).strip()
    if not key:
        # Nothing but numbers — fall back to the cleaned text so it still gets a stable key.
        key = re.sub(r"\s+", " ", text).strip()
    return key
