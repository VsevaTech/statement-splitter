from __future__ import annotations

import datetime as dt

import pytest

from app.columns import ColumnMapping, detect_columns
from app.parsing import normalize_currency, parse_amount, parse_date
from app.transform import TransformError, normalize_rows


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-46.80", -46.80),
        ("46.80", 46.80),
        ("1,234.56", 1234.56),
        ("1.234,56", 1234.56),
        ("1 234,56", 1234.56),
        ("-45,90", -45.90),
        ("(12.00)", -12.00),
        ("12.00-", -12.00),
        ("₪ 45.90", 45.90),
        ("$1,000", 1000.0),
        ("12,50 EUR", 12.50),
        ("+300", 300.0),
        ("1.234.567", 1234567.0),
        ("−15.5", -15.5),  # unicode minus
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "abc", "N/A", None, "12-34", "1,23,45"])
def test_parse_amount_rejects_garbage(raw):
    assert parse_amount(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2024-09-02", dt.date(2024, 9, 2)),
        ("02/09/2024", dt.date(2024, 9, 2)),
        ("02.09.2024", dt.date(2024, 9, 2)),
        ("2024-09-02T10:11:12", dt.date(2024, 9, 2)),
        ("02 Sep 2024", dt.date(2024, 9, 2)),
        ("not a date", None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_normalize_currency():
    assert normalize_currency("₪") == "ILS"
    assert normalize_currency("nis") == "ILS"
    assert normalize_currency("usd") == "USD"
    assert normalize_currency("", default="EUR") == "EUR"
    assert normalize_currency("??", default="EUR") == "EUR"


def test_detect_columns_by_header():
    cols = ["Date", "Description", "Amount", "Currency"]
    rows = [["2024-09-01", "WOLT IL", "-45.90", "ILS"], ["2024-09-02", "UBER", "-30.00", "ILS"]]
    m = detect_columns(cols, rows)
    assert (m.date, m.description, m.amount, m.currency) == ("Date", "Description", "Amount", "Currency")
    assert m.confident


def test_detect_debit_credit_columns():
    cols = ["Transaction Date", "Details", "Debit", "Credit", "Currency"]
    rows = [["2024-09-01", "AWS EMEA", "120.50", "", "USD"], ["2024-09-02", "CLIENT", "", "5000", "ILS"]]
    m = detect_columns(cols, rows)
    assert m.debit == "Debit" and m.credit == "Credit" and m.amount is None
    assert m.date == "Transaction Date" and m.description == "Details"
    assert m.confident


def test_detect_columns_by_content_when_headers_are_useless():
    cols = ["column_1", "column_2", "column_3"]
    rows = [
        ["2024-09-01", "WOLT IL TEL AVIV", "-45.90"],
        ["2024-09-02", "UBER TRIP", "-30.00"],
        ["2024-09-03", "CERCLI LTD", "-450.00"],
    ]
    m = detect_columns(cols, rows)
    assert m.date == "column_1" and m.description == "column_2" and m.amount == "column_3"
    assert m.confident


def test_detect_columns_not_confident_when_amount_missing():
    cols = ["Date", "Description", "Note"]
    rows = [["2024-09-01", "WOLT IL", "hello"], ["2024-09-02", "UBER", "world"]]
    m = detect_columns(cols, rows)
    assert not m.confident
    assert not m.is_complete
    assert "amount (or debit/credit)" in m.missing()


def test_manual_mapping_and_direction_column():
    cols = ["When", "Who", "Value", "DrCr"]
    rows = [["2024-09-01", "CLIENT PAYMENT", "1000", "CR"], ["2024-09-02", "WOLT IL", "45.90", "DR"]]
    mapping = ColumnMapping(date="When", description="Who", amount="Value", direction="DrCr", default_currency="usd")
    txs = normalize_rows(cols, rows, mapping)
    assert [t.direction for t in txs] == ["credit", "debit"]
    assert [t.amount for t in txs] == [1000.0, -45.90]
    assert {t.currency for t in txs} == {"USD"}


def test_debit_credit_to_signed_amount():
    cols = ["Date", "Details", "Debit", "Credit"]
    rows = [
        ["2024-09-01", "AWS", "120.50", ""],
        ["2024-09-02", "CLIENT", "", "5000"],
        ["2024-09-03", "BALANCE", "", ""],
    ]
    mapping = ColumnMapping(date="Date", description="Details", debit="Debit", credit="Credit")
    txs = normalize_rows(cols, rows, mapping)
    assert [(t.amount, t.direction) for t in txs] == [(-120.5, "debit"), (5000.0, "credit")]


def test_currency_from_amount_cell_and_column():
    cols = ["Date", "Description", "Amount", "Ccy"]
    rows = [["2024-09-01", "A", "€12,50", ""], ["2024-09-02", "B", "-30.00", "usd"], ["2024-09-03", "C", "-1", ""]]
    mapping = ColumnMapping(
        date="Date", description="Description", amount="Amount", currency="Ccy", default_currency="ILS"
    )
    txs = normalize_rows(cols, rows, mapping)
    assert [t.currency for t in txs] == ["EUR", "USD", "ILS"]


def test_incomplete_mapping_raises():
    with pytest.raises(TransformError):
        normalize_rows(["a", "b"], [["x", "y"]], ColumnMapping(date="a"))
