"""Built-in keyword/regex rules for obvious merchants. Small on purpose."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app import categories as c


@dataclass(frozen=True, slots=True)
class BuiltinRule:
    pattern: re.Pattern[str]
    category: str
    confidence: float = 0.9


def _r(pattern: str, category: str, confidence: float = 0.9) -> BuiltinRule:
    return BuiltinRule(re.compile(pattern, re.IGNORECASE), category, confidence)


BUILTIN_RULES: tuple[BuiltinRule, ...] = (
    # Transport
    _r(r"\b(UBER|GETT|YANGO|BOLT|LYFT|TAXI|MONIT|RAV[- ]?KAV|EGGED|ISRAEL RAILWAYS|METRO)\b", c.TRANSPORT),
    # Cloud / hosting
    _r(
        r"\b(AWS|AMAZON WEB SERVICES|GOOGLE CLOUD|GCP|DIGITALOCEAN|HETZNER|LINODE|VERCEL|NETLIFY|HEROKU|CLOUDFLARE)\b",
        c.CLOUD,
    ),
    # Software / SaaS
    _r(
        r"\b(GSUITE|GOOGLE WORKSPACE|MICROSOFT 365|OFFICE 365|MSFT|SLACK|NOTION|FIGMA|GITHUB|GITLAB|ATLASSIAN"
        r"|JETBRAINS|ADOBE|ZOOM|DROPBOX|1PASSWORD|OPENAI|ANTHROPIC|CANVA)\b",
        c.SOFTWARE,
    ),
    # Communication
    _r(
        r"\b(CELLCOM|PARTNER|PELEPHONE|HOT MOBILE|GOLAN|BEZEQ|VODAFONE|VERIZON|T-MOBILE|TELECOM|MOBILE)\b",
        c.COMMUNICATION,
        0.8,
    ),
    # Food
    _r(
        r"\b(WOLT|TENBIS|10BIS|DELIVEROO|UBER EATS|DOORDASH|CAFE|COFFEE|RESTAURANT|PIZZA|SUSHI|BURGER"
        r"|ARCAFFE|AROMA|MCDONALD)\b",
        c.FOOD,
    ),
    # Bank fees
    _r(r"\b(BANK FEE|FEE|COMMISSION|SERVICE CHARGE|ACCOUNT MAINTENANCE|FX FEE|WIRE FEE|CHARGEBACK)\b", c.BANK_FEES),
    # Taxes
    _r(r"\b(TAX AUTHORITY|INCOME TAX|VAT|MAAM|BITUACH LEUMI|NATIONAL INSURANCE|IRS|HMRC|TAX PAYMENT)\b", c.TAXES),
    # Travel
    _r(r"\b(EL AL|ELAL|RYANAIR|WIZZ|EASYJET|LUFTHANSA|BOOKING\.?COM|AIRBNB|HOTEL|EXPEDIA|AIRLINES?)\b", c.TRAVEL),
    # Office
    _r(r"\b(OFFICE DEPOT|IKEA|STAPLES|WEWORK|COWORKING|AMAZON MARKETPLACE|KSP|IVORY)\b", c.OFFICE, 0.8),
    # Accounting
    _r(r"\b(QUICKBOOKS|XERO|FRESHBOOKS|GREEN INVOICE|MORNING|HASHAVSHEVET|ACCOUNTANT|BOOKKEEPING)\b", c.ACCOUNTING),
    # Personal (obvious)
    _r(
        r"\b(NETFLIX|SPOTIFY|APPLE\.COM/BILL|STEAM|PLAYSTATION|SUPER-?PHARM|SHUFERSAL|RAMI LEVY|GYM|PHARMACY)\b",
        c.PERSONAL,
    ),
)


def match_builtin(merchant_key: str, description: str = "") -> tuple[str, float] | None:
    """Return (category, confidence) for the first matching built-in rule."""
    haystack = f"{merchant_key} {description}".strip()
    for rule in BUILTIN_RULES:
        if rule.pattern.search(haystack):
            return rule.category, rule.confidence
    return None
