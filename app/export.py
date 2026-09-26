"""XLSX export: Transactions + Summary (per category AND currency — never mixed).

Money is written as numeric cells from exact Decimals (openpyxl serializes the
Decimal's own text, so ``12.30`` is stored as ``12.30``, not as a float repr).
Text cells that a spreadsheet would treat as a formula are neutralized.
"""

from __future__ import annotations

import io
from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.money import dsum, excel_number_format

if TYPE_CHECKING:
    from app.integrity import IntegrityReport

TRANSACTION_HEADERS = (
    "Date",
    "Original Description",
    "Normalized Merchant",
    "Amount",
    "Currency",
    "Direction",
    "Category",
    "Category Source",
)
SUMMARY_HEADERS = ("Category", "Transaction Count", "Total Amount", "Currency")
NEEDS_REVIEW_LABEL = "Needs review"


class TransactionLike(Protocol):
    date: object
    description: str
    normalized_merchant: str
    amount: Decimal
    currency: str
    direction: str
    category: str | None
    category_source: str


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_text(value: object) -> object:
    """Neutralize spreadsheet formula injection (OWASP): prefix risky text with an apostrophe."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def build_summary(transactions: Iterable[TransactionLike]) -> list[tuple[str, int, Decimal, str]]:
    """Rows of (category, count, exact total, currency), sorted by currency then category.

    Amounts of different currencies are never added together.
    """
    buckets: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    for tx in transactions:
        buckets[(tx.category or NEEDS_REVIEW_LABEL, tx.currency)].append(tx.amount)
    rows = [(cat, len(v), dsum(v), cur) for (cat, cur), v in buckets.items()]
    return sorted(rows, key=lambda r: (r[3], r[0]))


def _style_header(ws, width_hint: dict[int, int]) -> None:
    fill = PatternFill("solid", fgColor="1F3A5F")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
        cell.alignment = Alignment(vertical="center")
    for idx, width in width_hint.items():
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A2"


def _money_cells(ws, column: int, min_row: int) -> None:
    cells = [row[0] for row in ws.iter_rows(min_row=min_row, min_col=column, max_col=column)]
    values = [c.value for c in cells if isinstance(c.value, Decimal)]
    fmt = excel_number_format(values)
    for cell in cells:
        if isinstance(cell.value, (Decimal, int, float)):
            cell.number_format = fmt


def _integrity_rows(report: IntegrityReport) -> list[list[object]]:
    s = report.import_summary
    rows: list[list[object]] = [["Import integrity", None]]
    if s.available:
        rows += [
            ["Source rows", s.source_rows],
            ["Imported rows", s.imported],
            ["Ignored rows", s.ignored],
            ["Rows needing attention", s.needs_attention],
        ]
        if s.imported_with_warnings:
            rows.append(["  of which imported with a warning", s.imported_with_warnings])
    else:
        rows.append(["Import audit", "not available (statement imported before v0.2)"])
    for check in report.balance_checks:
        rows += [
            [None, None],
            [f"Balance check ({check.currency})", None],
            ["Opening balance", check.opening],
            ["Income", check.income],
            ["Expenses", check.expenses],
            ["Net movement", check.net_movement],
            ["Expected closing", check.expected_closing],
            ["Actual closing", check.actual_closing],
            ["Difference", check.difference],
            ["Status", {"balanced": "Balanced", "difference": "Needs review"}.get(check.status, "Incomplete")],
        ]
    return rows


def export_workbook(transactions: list[TransactionLike], integrity: IntegrityReport | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"
    ws.append(TRANSACTION_HEADERS)
    for tx in transactions:
        date_value = tx.date
        if hasattr(date_value, "isoformat"):
            date_value = date_value.isoformat()
        ws.append(
            [
                date_value or "",
                safe_text(tx.description),
                safe_text(tx.normalized_merchant),
                tx.amount,
                safe_text(tx.currency),
                tx.direction,
                safe_text(tx.category or NEEDS_REVIEW_LABEL),
                tx.category_source,
            ]
        )
    _money_cells(ws, 4, 2)
    _style_header(ws, {1: 12, 2: 40, 3: 28, 4: 14, 5: 10, 6: 10, 7: 22, 8: 16})
    ws.auto_filter.ref = ws.dimensions

    ws2 = wb.create_sheet("Summary")
    ws2.append(SUMMARY_HEADERS)
    for category, count, total, currency in build_summary(transactions):
        ws2.append([safe_text(category), count, total, safe_text(currency)])
    _money_cells(ws2, 3, 2)
    if integrity is not None:
        ws2.append([])
        start = ws2.max_row + 1
        for label, value in _integrity_rows(integrity):
            ws2.append([label, value])
        for row in ws2.iter_rows(min_row=start, max_col=2):
            label_cell, value_cell = row
            if value_cell.value is None and label_cell.value:
                label_cell.font = Font(bold=True)
        _money_cells(ws2, 2, start)
        for row in ws2.iter_rows(min_row=start, min_col=2, max_col=2):
            if isinstance(row[0].value, int) and not isinstance(row[0].value, bool):
                row[0].number_format = "0"
    _style_header(ws2, {1: 24, 2: 18, 3: 16, 4: 10})

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
