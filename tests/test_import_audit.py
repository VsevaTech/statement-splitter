"""Import audit: every source row gets an outcome — imported, ignored (with reason) or needs attention."""

from __future__ import annotations

import logging

import pytest

from app import services
from app.columns import ColumnMapping
from app.importer import read_statement
from app.transform import Reason, RowStatus, analyze_rows
from tests.conftest import DEMO, csv_bytes, xlsx_bytes

MAP = ColumnMapping(date="Date", description="Description", amount="Amount", currency="Currency")
COLS = ["Date", "Description", "Amount", "Currency"]


def _analyze(rows, mapping=MAP, cols=COLS):
    return analyze_rows(cols, rows, mapping)


def _reasons(analysis):
    return {o.reason for o in analysis.outcomes}


def test_empty_row_is_ignored_and_counted():
    a = _analyze([["2024-09-01", "WOLT IL", "-45.90", "ILS"], ["", "", "", ""], ["2024-09-02", "GETT", "-30", "ILS"]])
    assert a.imported == 2 and a.ignored == 1 and a.source_rows == 3
    assert a.outcomes[0].reason == Reason.EMPTY_ROW and a.outcomes[0].status == RowStatus.IGNORED


def test_footer_row_is_ignored_as_header_or_footer():
    a = _analyze([["2024-09-01", "WOLT IL", "-45.90", "ILS"], ["", "TOTAL", "-45.90", "ILS"]])
    assert a.imported == 1 and _reasons(a) == {Reason.HEADER_OR_FOOTER}


def test_merchant_starting_with_total_is_not_a_footer():
    a = _analyze([["2024-09-01", "TOTAL ENERGIES STATION 12", "-210.00", "ILS"]])
    assert a.imported == 1 and a.outcomes == []


def test_repeated_header_row_is_ignored():
    a = _analyze([["2024-09-01", "WOLT IL", "-45.90", "ILS"], ["Date", "Description", "Amount", "Currency"]])
    assert a.imported == 1 and _reasons(a) == {Reason.HEADER_OR_FOOTER}


def test_balance_rows_are_ignored_and_detected():
    a = _analyze(
        [
            ["2024-09-01", "Opening balance", "1000.00", "ILS"],
            ["2024-09-02", "WOLT IL", "-45.90", "ILS"],
            ["2024-09-30", "Closing balance", "954.10", "ILS"],
        ]
    )
    assert a.imported == 1 and a.ignored == 2 and _reasons(a) == {Reason.BALANCE_ROW}
    detected = {(b.kind, b.currency): (str(b.amount), b.source_row) for b in a.balances}
    assert detected == {("opening", "ILS"): ("1000.00", 2), ("closing", "ILS"): ("954.10", 4)}


def test_invalid_amount_needs_attention_and_is_not_imported_as_zero():
    a = _analyze([["2024-09-21", "COFFEE SHOP ABC", "12.3O", "ILS"], ["2024-09-22", "GETT", "-30", "ILS"]])
    assert a.imported == 1 and a.not_imported == 1 and a.needs_attention == 1
    [o] = a.outcomes
    assert (o.reason, o.status, o.imported, o.source_row) == (Reason.UNPARSEABLE_AMOUNT, RowStatus.ATTENTION, False, 2)
    assert all(t.amount != 0 for t in a.transactions)


def test_unreadable_debit_is_not_replaced_by_the_credit_side():
    cols = ["Date", "Details", "Debit", "Credit"]
    m = ColumnMapping(date="Date", description="Details", debit="Debit", credit="Credit")
    a = _analyze([["2024-09-01", "AWS", "12,3,4", ""], ["2024-09-02", "CLIENT", "-", "500"]], m, cols)
    assert a.imported == 1 and [o.reason for o in a.outcomes] == [Reason.UNPARSEABLE_AMOUNT]


def test_invalid_date_is_imported_but_flagged():
    a = _analyze([["31/31/2024", "WOLT IL", "-45.90", "ILS"]])
    assert a.imported == 1 and a.transactions[0].date is None
    [o] = a.outcomes
    assert (o.reason, o.status, o.imported) == (Reason.INVALID_DATE, RowStatus.ATTENTION, True)
    assert a.needs_attention == 1 and a.not_imported == 0


def test_missing_amount_on_a_dated_row_needs_attention():
    a = _analyze([["2024-09-03", "CARD PAYMENT PENDING", "", "ILS"]])
    assert a.imported == 0 and [o.reason for o in a.outcomes] == [Reason.MISSING_AMOUNT]


def test_unknown_row_is_unrecognized_not_silently_dropped():
    a = _analyze([["", "Account holder: synthetic", "", ""], ["2024-09-02", "GETT", "-30", "ILS"]])
    assert [o.reason for o in a.outcomes] == [Reason.UNRECOGNIZED_ROW]
    assert a.outcomes[0].status == RowStatus.ATTENTION


def test_malformed_csv_line_is_kept_and_flagged():
    text = "Date,Description,Amount,Currency\n2024-09-01,WOLT IL,-45.90,ILS\n2024-09-02,ACME, INC,-10.00,ILS\n"
    table = read_statement("s.csv", csv_bytes(text))
    assert table.row_count == 2  # the bad line is no longer dropped by the reader
    a = analyze_rows(table.columns, table.rows, MAP, table.row_numbers)
    assert [(o.reason, o.source_row) for o in a.outcomes] == [(Reason.MALFORMED_ROW, 3)]


def test_source_row_numbers_follow_the_file():
    text = "Date,Description,Amount,Currency\n2024-09-01,A,-1,ILS\n\n\n2024-09-02,B,-2,ILS\n,TOTAL,-3,ILS\n\n"
    table = read_statement("s.csv", csv_bytes(text))
    assert table.row_numbers == [2, 3, 4, 5, 6]  # trailing blank line is not a source row
    a = analyze_rows(table.columns, table.rows, MAP, table.row_numbers)
    assert [(o.source_row, o.reason) for o in a.outcomes] == [
        (3, Reason.EMPTY_ROW),
        (4, Reason.EMPTY_ROW),
        (6, Reason.HEADER_OR_FOOTER),
    ]


def test_xlsx_row_numbers_and_empty_rows():
    data = xlsx_bytes(COLS, [["2024-09-01", "A", -1, "ILS"], [None, None, None, None], ["2024-09-02", "B", -2, "ILS"]])
    table = read_statement("s.xlsx", data)
    assert table.row_numbers == [2, 3, 4]
    a = analyze_rows(table.columns, table.rows, MAP, table.row_numbers)
    assert a.imported == 2 and [(o.source_row, o.reason) for o in a.outcomes] == [(3, Reason.EMPTY_ROW)]


@pytest.mark.parametrize("name", ["statement.csv", "statement.xlsx"])
def test_demo_statement_counts(session, name):
    statement, _p, mapping = services.import_statement(session, name, (DEMO / name).read_bytes())
    assert statement is not None, mapping
    s = services.integrity_report(session, statement).import_summary
    assert (s.source_rows, s.imported, s.ignored, s.needs_attention) == (305, 300, 4, 1)
    assert s.consistent
    groups = services.skipped_rows(statement)
    assert [r.reason for r in groups["not_imported"]] == ["UNPARSEABLE_AMOUNT"]
    assert sorted(r.reason for r in groups["ignored"]) == [
        "BALANCE_ROW",
        "BALANCE_ROW",
        "EMPTY_ROW",
        "HEADER_OR_FOOTER",
    ]


def test_counts_always_add_up():
    rows = [
        ["2024-09-01", "Opening balance", "10", "ILS"],
        ["", "", "", ""],
        ["2024-09-02", "A", "-1", "ILS"],
        ["2024-09-03", "B", "x1", "ILS"],
        ["bad date", "C", "-2", "ILS"],
        ["", "Totals", "", ""],
        ["", "free text", "", ""],
    ]
    a = _analyze(rows)
    assert a.source_rows == 7
    assert a.imported + a.ignored + a.not_imported == a.source_rows
    assert (a.imported, a.ignored, a.not_imported, a.imported_with_warnings) == (2, 3, 2, 1)


def test_skipped_rows_page_separates_ignored_from_attention(client, session):
    statement, _p, _m = services.import_statement(session, "statement.csv", (DEMO / "statement.csv").read_bytes())
    review = client.get(f"/statements/{statement.id}").text
    assert "Financial integrity" in review and "Review skipped rows" in review
    assert "1 row needs attention" in review
    page = client.get(f"/statements/{statement.id}/skipped")
    assert page.status_code == 200
    html = page.text
    attention, ignored = html.split("Needs attention", 2)[-1].split("Ignored", 1)
    assert "UNPARSEABLE_AMOUNT" in attention and "COFFEE SHOP ABC" in attention and "12.3O" in attention
    assert "BALANCE_ROW" not in attention and "HEADER_OR_FOOTER" not in attention
    assert "BALANCE_ROW" in ignored and "HEADER_OR_FOOTER" in ignored and "Opening balance" in ignored
    assert 'class="reason warn"' in attention and 'class="reason info"' in ignored


def test_mark_import_reviewed(client, session):
    statement, _p, _m = services.import_statement(session, "statement.csv", (DEMO / "statement.csv").read_bytes())
    resp = client.post(f"/statements/{statement.id}/audit-reviewed", data={"reviewed": "on"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "Import reviewed" in client.get(f"/statements/{statement.id}").text


def test_clear_skipped_row_contents_keeps_reasons(client, session):
    statement, _p, _m = services.import_statement(session, "statement.csv", (DEMO / "statement.csv").read_bytes())
    client.post(f"/statements/{statement.id}/skipped/clear-details", follow_redirects=False)
    rows = client.get(f"/api/statements/{statement.id}/skipped-rows").json()["rows"]
    assert len(rows) == 5 and all(r["cells"] == [] for r in rows)
    assert "COFFEE SHOP ABC" not in client.get(f"/statements/{statement.id}/skipped").text


def test_raw_rows_are_never_logged(client, caplog):
    caplog.set_level(logging.DEBUG)
    body = "Date,Description,Amount,Currency\n2024-09-01,SECRET MERCHANT 991,12.3O,ILS\n2024-09-02,GETT,-30,ILS\n"
    client.post("/upload", files={"file": ("s.csv", body.encode())}, follow_redirects=False)
    assert "SECRET MERCHANT" not in caplog.text and "12.3O" not in caplog.text


def test_statement_delete_removes_skipped_rows_and_balances(client, session):
    from sqlalchemy import select

    from app.db import SkippedRow, StatementBalance

    statement, _p, _m = services.import_statement(session, "statement.csv", (DEMO / "statement.csv").read_bytes())
    client.post(f"/statements/{statement.id}/delete", follow_redirects=False)
    session.expire_all()
    assert session.scalars(select(SkippedRow)).all() == []
    assert session.scalars(select(StatementBalance)).all() == []


def test_problem_cell_is_highlighted_on_the_review_page(client, session):
    statement, _p, _m = services.import_statement(session, "statement.csv", (DEMO / "statement.csv").read_bytes())
    [bad] = services.skipped_rows(statement)["not_imported"]
    assert bad.cell_index == 2  # the Amount column
    html = client.get(f"/statements/{statement.id}/skipped").text
    assert '<span class="cell bad-cell" title="This value could not be read">12.3O</span>' in html
