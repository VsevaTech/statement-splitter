from __future__ import annotations

import pytest

from app.importer import StatementImportError, detect_delimiter, detect_encoding, read_statement
from tests.conftest import DEMO, SIMPLE_CSV, csv_bytes, xlsx_bytes


def test_csv_import_basic():
    table = read_statement("s.csv", csv_bytes(SIMPLE_CSV))
    assert table.columns == ["Date", "Description", "Amount", "Currency"]
    assert table.row_count == 6
    assert table.rows[0] == ["2024-09-02", "GOOGLE*GSUITE 839291", "-46.80", "USD"]
    assert table.delimiter == ","


@pytest.mark.parametrize("delim", [";", "\t", "|"])
def test_csv_delimiter_detection(delim):
    text = SIMPLE_CSV.replace(",", delim)
    assert detect_delimiter(text) == delim
    table = read_statement("s.csv", csv_bytes(text))
    assert table.delimiter == delim
    assert table.columns == ["Date", "Description", "Amount", "Currency"]
    assert table.row_count == 6


def test_csv_semicolon_with_decimal_comma():
    text = "Date;Description;Amount\n01.09.2024;WOLT IL;-45,90\n02.09.2024;GETT;-30,00\n"
    table = read_statement("s.csv", csv_bytes(text))
    assert table.delimiter == ";"
    assert table.rows[0][2] == "-45,90"


_WORDS = {
    "cp1251": ["Кафе Нимрод", "Супермаркет", "Такси", "Аптека", "Связь", "Электричество", "Интернет", "Продукты"],
    "cp1255": [
        "קפה נמרוד",
        "סופר פארם",
        "רמי לוי",
        "וולט ישראל",
        "גט טקסי",
        "חברת החשמל",
        "בזק בינלאומי",
        "שופרסל דיל",
    ],
}


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1251", "cp1255"])
def test_csv_encodings(encoding):
    words = _WORDS.get(
        encoding, ["Café Nimrod", "Süpermarkt", "Zürich Taxi", "Crème", "Café", "Élan", "Über", "Straße"]
    )
    text = "Date,Description,Amount\n" + "".join(f"2024-09-0{i + 1},{w},-20.00\n" for i, w in enumerate(words))
    table = read_statement("s.csv", text.encode(encoding))
    assert [r[1] for r in table.rows] == words
    assert detect_encoding(text.encode(encoding)).lower().replace("_", "-") in {
        encoding,
        "utf-8",
        "utf-8-sig",
        "cp1251",
        "windows-1251",
        "cp1255",
        "windows-1255",
    }


def test_xlsx_import():
    data = xlsx_bytes(
        ["Transaction Date", "Details", "Debit", "Credit", "Currency"],
        [["2024-09-02", "AWS EMEA", 120.5, None, "USD"], ["2024-09-03", "CLIENT PAYMENT", None, 5000, "ILS"]],
    )
    table = read_statement("s.xlsx", data)
    assert table.columns == ["Transaction Date", "Details", "Debit", "Credit", "Currency"]
    assert table.rows[0] == ["2024-09-02", "AWS EMEA", "120.5", "", "USD"]
    assert table.rows[1][3] == "5000"


def test_xlsx_date_cells_become_iso():
    import datetime as dt

    data = xlsx_bytes(["Date", "Description", "Amount"], [[dt.datetime(2024, 9, 5), "WOLT IL", -45.9]])
    table = read_statement("s.xlsx", data)
    assert table.rows[0][0] == "2024-09-05"


def test_empty_file_rejected():
    with pytest.raises(StatementImportError):
        read_statement("s.csv", b"")


def test_single_column_rejected():
    with pytest.raises(StatementImportError):
        read_statement("s.csv", b"just\nsome\nlines\n")


def test_demo_files_parse():
    for name in ("statement.csv", "statement.xlsx", "statement-2.csv"):
        table = read_statement(name, (DEMO / name).read_bytes())
        assert table.row_count >= 100, name
