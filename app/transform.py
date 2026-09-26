"""Turn a raw table + column mapping into normalized transactions — and account for every source row.

Every source row after the header ends in exactly one outcome:

* **imported** — became a transaction (possibly with a warning, e.g. an unreadable date);
* **ignored** — safe to skip, with a reason (empty row, balance row, footer/total…);
* **not imported / needs attention** — looked like data but could not be read safely
  (unparseable amount, missing amount, malformed row, unrecognized row).

Nothing is dropped silently, and an unreadable amount never becomes 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.columns import ColumnMapping
from app.normalize import normalize_merchant
from app.parsing import EMPTY_AMOUNT_MARKERS, detect_currency_in_text, normalize_currency, parse_amount, parse_date
from app.schemas import NormalizedTransaction

_CREDIT_WORDS = {"credit", "cr", "c", "in", "income", "deposit", "зачисление", "приход", "поступление"}
_DEBIT_WORDS = {"debit", "dr", "d", "out", "expense", "withdrawal", "списание", "расход"}


class TransformError(ValueError):
    pass


class RowStatus(StrEnum):
    IMPORTED = "imported"
    IGNORED = "ignored"
    ATTENTION = "attention"


class Reason(StrEnum):
    # ignored — informational
    EMPTY_ROW = "EMPTY_ROW"
    BALANCE_ROW = "BALANCE_ROW"
    HEADER_OR_FOOTER = "HEADER_OR_FOOTER"
    ZERO_AMOUNT = "ZERO_AMOUNT"
    # needs attention — possible lost transaction
    UNPARSEABLE_AMOUNT = "UNPARSEABLE_AMOUNT"
    MISSING_AMOUNT = "MISSING_AMOUNT"
    MALFORMED_ROW = "MALFORMED_ROW"
    UNRECOGNIZED_ROW = "UNRECOGNIZED_ROW"
    # imported, but flagged
    INVALID_DATE = "INVALID_DATE"


REASON_TEXT: dict[str, str] = {
    Reason.EMPTY_ROW: "Empty row",
    Reason.BALANCE_ROW: "Balance line, not a transaction",
    Reason.HEADER_OR_FOOTER: "Repeated header or total/footer line",
    Reason.ZERO_AMOUNT: "Zero amount without a description",
    Reason.UNPARSEABLE_AMOUNT: "The amount could not be read — this may be a missing transaction",
    Reason.MISSING_AMOUNT: "Dated row without an amount — this may be a missing transaction",
    Reason.MALFORMED_ROW: "Row has more cells than the header (e.g. an unquoted delimiter)",
    Reason.UNRECOGNIZED_ROW: "Row could not be recognized as a transaction",
    Reason.INVALID_DATE: "Imported without a date: the date could not be read",
}

# Deterministic, explicit labels only. A row is a balance row when its description
# *starts with* one of these phrases; no fuzzy matching, no AI.
_OPENING = (
    "opening balance",
    "balance brought forward",
    "brought forward",
    "balance b/f",
    "previous balance",
    "starting balance",
    "start balance",
    "входящий остаток",
    "остаток на начало",
    "יתרת פתיחה",
)
_CLOSING = (
    "closing balance",
    "balance carried forward",
    "carried forward",
    "balance c/f",
    "ending balance",
    "end balance",
    "final balance",
    "исходящий остаток",
    "остаток на конец",
    "יתרת סגירה",
)
_GENERIC_BALANCE = ("balance", "available balance", "current balance", "остаток", "баланс", "יתרה")
_FOOTER = re.compile(r"^(?:total|totals|grand total|sub-?total|итого|всего|סה\"כ|סך הכל)\b", re.IGNORECASE)


def _label(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[:.\-–—]+$", "", text.strip().lower())).strip()


def balance_kind(text: str) -> str | None:
    """'opening' | 'closing' | 'balance' | None for a description cell."""
    label = _label(text)
    if not label:
        return None
    for prefixes, kind in ((_OPENING, "opening"), (_CLOSING, "closing")):
        for p in prefixes:
            if label == p or label.startswith(p + " ") or label.startswith(p + ":"):
                return kind
    if label in _GENERIC_BALANCE:
        return "balance"
    return None


@dataclass(slots=True)
class RowOutcome:
    source_row: int
    status: RowStatus
    reason: Reason | None
    cells: list[str]
    imported: bool = False
    cell_index: int | None = None  # the cell that caused the problem (for highlighting), if known

    @property
    def description(self) -> str:
        return REASON_TEXT.get(self.reason, "") if self.reason else ""


@dataclass(slots=True)
class DetectedBalance:
    kind: str  # opening | closing
    currency: str
    amount: Decimal
    source_row: int


@dataclass(slots=True)
class ImportAnalysis:
    transactions: list[NormalizedTransaction]
    outcomes: list[RowOutcome]  # every non-plain outcome (ignored, attention, imported-with-warning)
    source_rows: int
    balances: list[DetectedBalance] = field(default_factory=list)
    ambiguous_balances: list[str] = field(default_factory=list)  # "opening ILS" found more than once

    @property
    def imported(self) -> int:
        return len(self.transactions)

    @property
    def ignored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == RowStatus.IGNORED)

    @property
    def not_imported(self) -> int:
        return sum(1 for o in self.outcomes if o.status == RowStatus.ATTENTION and not o.imported)

    @property
    def imported_with_warnings(self) -> int:
        return sum(1 for o in self.outcomes if o.status == RowStatus.ATTENTION and o.imported)

    @property
    def needs_attention(self) -> int:
        return self.not_imported + self.imported_with_warnings


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def _amount_cell(text: str) -> tuple[bool, Decimal | None]:
    """(present, value). Present-but-None means an unreadable amount."""
    text = text.strip()
    if not text or text in EMPTY_AMOUNT_MARKERS:
        return False, None
    return True, parse_amount(text)


def analyze_rows(
    columns: list[str],
    rows: list[list[str]],
    mapping: ColumnMapping,
    row_numbers: list[int] | None = None,
) -> ImportAnalysis:
    if not mapping.is_complete:
        raise TransformError("Column mapping is incomplete: " + ", ".join(mapping.missing()))
    index = {c: i for i, c in enumerate(columns)}

    def col(name: str | None) -> int | None:
        if name is None:
            return None
        if name not in index:
            raise TransformError(f"Unknown column '{name}'")
        return index[name]

    i_date, i_desc = col(mapping.date), col(mapping.description)
    i_amount, i_cur = col(mapping.amount), col(mapping.currency)
    i_debit, i_credit, i_dir = col(mapping.debit), col(mapping.credit), col(mapping.direction)
    header_labels = {c.strip().lower() for c in columns}
    numbers = row_numbers if row_numbers and len(row_numbers) == len(rows) else [i + 2 for i in range(len(rows))]

    out: list[NormalizedTransaction] = []
    outcomes: list[RowOutcome] = []
    detected: dict[tuple[str, str], list[DetectedBalance]] = {}

    def currency_of(row: list[str], amount_text: str) -> str:
        source = _cell(row, i_cur) if i_cur is not None else ""
        return (
            normalize_currency(source, default="") or detect_currency_in_text(amount_text) or mapping.default_currency
        )

    def skip(
        row_index: int,
        status: RowStatus,
        reason: Reason,
        row: list[str],
        imported: bool = False,
        cell: int | None = None,
    ) -> None:
        outcomes.append(RowOutcome(numbers[row_index], status, reason, list(row), imported, cell))

    for row_index, row in enumerate(rows):
        cells = [c.strip() for c in row]
        if not any(cells):
            skip(row_index, RowStatus.IGNORED, Reason.EMPTY_ROW, [])
            continue
        non_empty = [c for c in cells if c]
        if all(c.lower() in header_labels for c in non_empty) and len(non_empty) >= 2:
            skip(row_index, RowStatus.IGNORED, Reason.HEADER_OR_FOOTER, row)
            continue
        if len(row) > len(columns):
            skip(row_index, RowStatus.ATTENTION, Reason.MALFORMED_ROW, row)
            continue

        description = _cell(row, i_desc).strip()
        label_text = description or next((c for c in cells if c and parse_amount(c) is None), "")
        date_text = _cell(row, i_date).strip()
        date_value = parse_date(date_text) if date_text else None

        kind = balance_kind(label_text)
        if kind is not None:
            if kind in ("opening", "closing"):
                values = []
                for i, c in enumerate(cells):
                    if i in (i_date, i_desc, i_cur, i_dir) or not c or c in EMPTY_AMOUNT_MARKERS:
                        continue
                    values.append((c, parse_amount(c)))
                parsed = {v for _c, v in values if v is not None}
                if len(parsed) == 1 and all(v is not None for _c, v in values):
                    amount = parsed.pop()
                    raw = next(c for c, v in values if v is not None)
                    cur = currency_of(row, raw)
                    detected.setdefault((kind, cur), []).append(DetectedBalance(kind, cur, amount, numbers[row_index]))
            skip(row_index, RowStatus.IGNORED, Reason.BALANCE_ROW, row)
            continue
        if _FOOTER.match(label_text) and date_value is None:
            skip(row_index, RowStatus.IGNORED, Reason.HEADER_OR_FOOTER, row)
            continue

        amount_text = _cell(row, i_amount)
        signed: Decimal | None = None
        unreadable = False
        bad_cell = i_amount
        if i_debit is not None or i_credit is not None:
            has_debit, debit = _amount_cell(_cell(row, i_debit))
            has_credit, credit = _amount_cell(_cell(row, i_credit))
            if (has_debit and debit is None) or (has_credit and credit is None):
                unreadable = True
                bad_cell = i_debit if has_debit and debit is None else i_credit
            elif has_debit or has_credit:
                signed = (credit or Decimal(0)) - abs(debit or Decimal(0))
            elif i_amount is not None:
                has_amount, amt = _amount_cell(amount_text)
                unreadable = has_amount and amt is None
                signed = amt
        else:
            has_amount, signed = _amount_cell(amount_text)
            unreadable = has_amount and signed is None

        if unreadable:
            skip(row_index, RowStatus.ATTENTION, Reason.UNPARSEABLE_AMOUNT, row, cell=bad_cell)
            continue
        if signed is None:
            if date_value is not None and description:
                skip(row_index, RowStatus.ATTENTION, Reason.MISSING_AMOUNT, row)
            else:
                skip(row_index, RowStatus.ATTENTION, Reason.UNRECOGNIZED_ROW, row)
            continue

        direction_text = _cell(row, i_dir).strip().lower()
        if direction_text in _CREDIT_WORDS:
            signed = abs(signed)
        elif direction_text in _DEBIT_WORDS:
            signed = -abs(signed)

        if signed == 0 and not description:
            skip(row_index, RowStatus.IGNORED, Reason.ZERO_AMOUNT, row)
            continue
        if date_text and date_value is None:
            # Backward compatible: the transaction is still imported (its money counts),
            # but the row is flagged for attention instead of silently losing its date.
            skip(row_index, RowStatus.ATTENTION, Reason.INVALID_DATE, row, imported=True, cell=i_date)

        direction = "credit" if signed > 0 else "debit"
        out.append(
            NormalizedTransaction(
                row_index=row_index,
                date=date_value,
                description=description,
                normalized_merchant=normalize_merchant(description),
                amount=signed,
                currency=currency_of(row, amount_text),
                direction=direction,
            )
        )

    balances: list[DetectedBalance] = []
    ambiguous: list[str] = []
    for (kind, cur), found in sorted(detected.items()):
        if len(found) == 1:
            balances.append(found[0])
        else:
            ambiguous.append(f"{kind} {cur}")  # more than one candidate: do not guess
    return ImportAnalysis(
        transactions=out,
        outcomes=outcomes,
        source_rows=len(rows),
        balances=balances,
        ambiguous_balances=ambiguous,
    )


def normalize_rows(columns: list[str], rows: list[list[str]], mapping: ColumnMapping) -> list[NormalizedTransaction]:
    """Backward-compatible helper: only the transactions. Raises if there are none."""
    analysis = analyze_rows(columns, rows, mapping)
    if not analysis.transactions:
        raise TransformError("No transactions could be read with this column mapping.")
    return analysis.transactions
