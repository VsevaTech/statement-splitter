"""Turn a raw table + column mapping into normalized transactions."""

from __future__ import annotations

from app.columns import ColumnMapping
from app.normalize import normalize_merchant
from app.parsing import detect_currency_in_text, normalize_currency, parse_amount, parse_date
from app.schemas import NormalizedTransaction

_CREDIT_WORDS = {"credit", "cr", "c", "in", "income", "deposit", "зачисление", "приход", "поступление"}
_DEBIT_WORDS = {"debit", "dr", "d", "out", "expense", "withdrawal", "списание", "расход"}


class TransformError(ValueError):
    pass


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def normalize_rows(columns: list[str], rows: list[list[str]], mapping: ColumnMapping) -> list[NormalizedTransaction]:
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

    out: list[NormalizedTransaction] = []
    for row_index, row in enumerate(rows):
        description = _cell(row, i_desc).strip()
        amount_text = _cell(row, i_amount)
        debit_text, credit_text = _cell(row, i_debit), _cell(row, i_credit)

        signed: float | None
        if i_debit is not None or i_credit is not None:
            debit = parse_amount(debit_text) if debit_text else None
            credit = parse_amount(credit_text) if credit_text else None
            if debit is None and credit is None:
                if i_amount is not None and (amt := parse_amount(amount_text)) is not None:
                    signed = amt
                else:
                    continue  # no amount at all: skip (e.g. balance-only line)
            else:
                signed = (credit or 0.0) - abs(debit or 0.0)
        else:
            signed = parse_amount(amount_text)
            if signed is None:
                continue

        direction_text = _cell(row, i_dir).strip().lower()
        if direction_text in _CREDIT_WORDS:
            signed = abs(signed)
        elif direction_text in _DEBIT_WORDS:
            signed = -abs(signed)

        if signed == 0 and not description:
            continue
        direction = "credit" if signed > 0 else "debit"

        currency_source = _cell(row, i_cur) if i_cur is not None else ""
        currency = normalize_currency(currency_source, default="") or detect_currency_in_text(amount_text) or ""
        currency = currency or mapping.default_currency

        out.append(
            NormalizedTransaction(
                row_index=row_index,
                date=parse_date(_cell(row, i_date)),
                description=description,
                normalized_merchant=normalize_merchant(description),
                amount=round(signed, 2),
                currency=currency,
                direction=direction,
            )
        )
    if not out:
        raise TransformError("No transactions could be read with this column mapping.")
    return out
