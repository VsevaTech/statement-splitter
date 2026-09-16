"""Automatic column detection for bank statements + the mapping model.

Detection is header-name based first, then falls back to content heuristics.
If the required roles (date, description, amount OR debit/credit) cannot be
found confidently, `ColumnMapping.confident` is False and the UI asks the user.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, model_validator

from app.parsing import parse_amount, parse_date

ROLES = ("date", "description", "amount", "currency", "debit", "credit", "direction")

_HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "date": ("date", "transaction date", "posting date", "value date", "booking date", "дата", "תאריך"),
    "description": (
        "description",
        "merchant",
        "details",
        "narrative",
        "memo",
        "payee",
        "counterparty",
        "transaction",
        "particulars",
        "назначение",
        "описание",
        "контрагент",
        "שם בית העסק",
        "פרטים",
    ),
    "amount": ("amount", "sum", "value", "сумма", "סכום"),
    "currency": ("currency", "ccy", "cur", "валюта", "מטבע"),
    "debit": ("debit", "withdrawal", "out", "expense", "расход", "списание", "חובה"),
    "credit": ("credit", "deposit", "in", "income", "приход", "поступление", "זכות"),
    "direction": ("type", "dr/cr", "d/c", "direction", "тип"),
}

_CURRENCY_RE = re.compile(r"^(?:[A-Z]{3}|₪|\$|€|£)$")


class ColumnMapping(BaseModel):
    date: str | None = None
    description: str | None = None
    amount: str | None = None
    currency: str | None = None
    debit: str | None = None
    credit: str | None = None
    direction: str | None = None
    default_currency: str = Field(default="ILS", min_length=3, max_length=3)
    confident: bool = False

    @model_validator(mode="after")
    def _upper_currency(self) -> ColumnMapping:
        self.default_currency = self.default_currency.upper()
        return self

    @property
    def is_complete(self) -> bool:
        has_amount = bool(self.amount) or bool(self.debit or self.credit)
        return bool(self.date and self.description and has_amount)

    def missing(self) -> list[str]:
        out = []
        if not self.date:
            out.append("date")
        if not self.description:
            out.append("description")
        if not (self.amount or self.debit or self.credit):
            out.append("amount (or debit/credit)")
        return out


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-zа-яא-ת0-9/ ]+", " ", h.strip().lower()).strip()


def _score_header(header: str, role: str) -> int:
    h = _norm_header(header)
    if not h:
        return 0
    best = 0
    for hint in _HEADER_HINTS[role]:
        if h == hint:
            best = max(best, 3)
        elif re.search(rf"(^|\s){re.escape(hint)}(\s|$)", h):
            best = max(best, 2)
        elif hint in h and len(hint) > 3:
            best = max(best, 1)
    return best


def _column_values(rows: list[list[str]], idx: int, limit: int = 50) -> list[str]:
    return [r[idx] for r in rows[:limit] if idx < len(r) and r[idx] != ""]


def _content_ratio(values: list[str], pred) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if pred(v)) / len(values)


def detect_columns(columns: list[str], rows: list[list[str]]) -> ColumnMapping:
    assigned: dict[str, str] = {}
    used: set[str] = set()

    # Pass 1: header names. Debit/credit "amount" hints must not steal a generic "Amount".
    for role in ("date", "currency", "debit", "credit", "amount", "direction", "description"):
        scored = sorted(
            ((_score_header(c, role), i, c) for i, c in enumerate(columns) if c not in used),
            reverse=True,
        )
        if scored and scored[0][0] >= 2:
            score, _idx, col = scored[0]
            # A column called "Debit amount" is debit, not amount.
            if role == "amount" and (_score_header(col, "debit") >= 2 or _score_header(col, "credit") >= 2):
                continue
            assigned[role] = col
            used.add(col)

    # If we found both debit and credit, an "amount" header is redundant — keep both paths valid.
    # Pass 2: content heuristics for missing required roles.
    def content_pick(role: str, pred, threshold: float) -> None:
        if role in assigned:
            return
        best_ratio, best_col = 0.0, None
        for i, col in enumerate(columns):
            if col in used:
                continue
            ratio = _content_ratio(_column_values(rows, i), pred)
            if ratio > best_ratio:
                best_ratio, best_col = ratio, col
        if best_col is not None and best_ratio >= threshold:
            assigned[role] = best_col
            used.add(best_col)

    content_pick("date", lambda v: parse_date(v) is not None, 0.8)
    content_pick("currency", lambda v: bool(_CURRENCY_RE.match(v.strip().upper())), 0.9)
    if "debit" not in assigned and "credit" not in assigned:
        content_pick("amount", lambda v: parse_amount(v) is not None, 0.9)
    # Description: the text-heaviest remaining column.
    if "description" not in assigned:
        best_len, best_col = 0.0, None
        for i, col in enumerate(columns):
            if col in used:
                continue
            values = _column_values(rows, i)
            if not values:
                continue
            alpha_ratio = _content_ratio(values, lambda v: bool(re.search(r"[A-Za-zА-Яа-яא-ת]{3}", v)))
            avg_len = sum(len(v) for v in values) / len(values)
            score = alpha_ratio * avg_len
            if alpha_ratio >= 0.7 and score > best_len:
                best_len, best_col = score, col
        if best_col is not None:
            assigned["description"] = best_col
            used.add(best_col)

    mapping = ColumnMapping(**assigned)

    # Confidence: required roles present AND their content actually parses.
    confident = mapping.is_complete
    if confident:
        idx = {c: i for i, c in enumerate(columns)}
        confident &= _content_ratio(_column_values(rows, idx[mapping.date]), lambda v: parse_date(v) is not None) >= 0.8
        amount_cols = [c for c in (mapping.amount, mapping.debit, mapping.credit) if c]
        for c in amount_cols:
            vals = _column_values(rows, idx[c])
            if vals:
                confident &= _content_ratio(vals, lambda v: parse_amount(v) is not None) >= 0.8
    mapping.confident = confident
    return mapping
