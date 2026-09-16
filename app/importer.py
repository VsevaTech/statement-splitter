"""Read CSV / XLSX statements into a plain table (list of column names + list of rows).

Everything downstream works with plain Python values so that raw rows can be
stored temporarily as JSON while the user confirms the column mapping.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

import pandas as pd

SUPPORTED_EXTENSIONS = (".csv", ".xlsx", ".xls", ".txt")
_CANDIDATE_DELIMITERS = [",", ";", "\t", "|"]
_FALLBACK_ENCODINGS = ["utf-8-sig", "utf-8", "cp1255", "cp1251", "cp1252", "latin-1"]
_SINGLE_BYTE_CANDIDATES = ["cp1251", "cp1255", "cp1252", "iso-8859-8", "koi8-r", "cp1250"]


class StatementImportError(ValueError):
    """Raised when a file cannot be parsed into a table."""


@dataclass(slots=True)
class RawTable:
    columns: list[str]
    rows: list[list[str]]
    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def preview(self, n: int = 10) -> list[list[str]]:
        return self.rows[:n]


def detect_encoding(data: bytes) -> str:
    """Return a usable text encoding for `data`. Conservative: utf-8 first."""
    for enc in ("utf-8-sig", "utf-8"):
        try:
            data.decode(enc)
            return enc
        except UnicodeDecodeError:
            pass
    try:
        from charset_normalizer import from_bytes

        # Restrict to code pages actually seen in bank exports; unrestricted detection
        # is unreliable on small files.
        best = from_bytes(data, cp_isolation=_SINGLE_BYTE_CANDIDATES).best()
        if best is not None and best.encoding:
            return best.encoding
    except Exception:  # pragma: no cover - optional dependency path
        pass
    for enc in _FALLBACK_ENCODINGS[2:]:
        try:
            data.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def detect_delimiter(text: str) -> str:
    """Detect the CSV delimiter from the first lines of `text`."""
    sample_lines = [ln for ln in text.splitlines()[:20] if ln.strip()]
    sample = "\n".join(sample_lines)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(_CANDIDATE_DELIMITERS))
        if dialect.delimiter in _CANDIDATE_DELIMITERS:
            return dialect.delimiter
    except csv.Error:
        pass
    # Fallback: the delimiter that appears consistently (same count) on most lines.
    best, best_score = ",", -1
    for delim in _CANDIDATE_DELIMITERS:
        counts = [ln.count(delim) for ln in sample_lines]
        if not counts or max(counts) == 0:
            continue
        common = max(set(counts), key=counts.count)
        if common == 0:
            continue
        score = counts.count(common) * 1000 + common
        if score > best_score:
            best, best_score = delim, score
    return best


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if hasattr(value, "isoformat"):
        try:
            iso = value.isoformat()
            return iso[:10] if iso.endswith("T00:00:00") else iso
        except Exception:  # pragma: no cover
            return str(value)
    return str(value).strip()


def _dataframe_to_table(df: pd.DataFrame) -> tuple[list[str], list[list[str]]]:
    df = df.dropna(how="all").dropna(axis=1, how="all")
    columns = [
        str(c).strip() if str(c).strip() and not str(c).startswith("Unnamed") else f"column_{i + 1}"
        for i, c in enumerate(df.columns)
    ]
    rows = [[_clean_cell(v) for v in record] for record in df.itertuples(index=False, name=None)]
    rows = [r for r in rows if any(cell for cell in r)]
    return columns, rows


def read_csv_bytes(data: bytes) -> RawTable:
    encoding = detect_encoding(data)
    text = data.decode(encoding, errors="replace")
    if not text.strip():
        raise StatementImportError("The file is empty.")
    delimiter = detect_delimiter(text)
    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=delimiter,
            dtype=str,
            keep_default_na=False,
            skip_blank_lines=True,
            engine="python",
            on_bad_lines="skip",
        )
    except Exception as exc:  # pragma: no cover - pandas error surface is wide
        raise StatementImportError(f"Could not parse CSV: {exc}") from exc
    if df.shape[1] < 2:
        raise StatementImportError("Could not detect a table with at least two columns.")
    columns, rows = _dataframe_to_table(df)
    return RawTable(columns=columns, rows=rows, encoding=encoding, delimiter=delimiter)


def read_xlsx_bytes(data: bytes, sheet: str | int = 0) -> RawTable:
    try:
        df = pd.read_excel(io.BytesIO(data), sheet_name=sheet, dtype=object, engine="openpyxl")
    except Exception as exc:
        raise StatementImportError(f"Could not read the Excel file: {exc}") from exc
    if df.empty or df.shape[1] < 2:
        raise StatementImportError("The first sheet does not contain a table with at least two columns.")
    columns, rows = _dataframe_to_table(df)
    return RawTable(columns=columns, rows=rows, sheet=str(sheet))


def read_statement(filename: str, data: bytes) -> RawTable:
    name = filename.lower()
    if name.endswith((".xlsx", ".xls")):
        return read_xlsx_bytes(data)
    if name.endswith((".csv", ".txt")):
        return read_csv_bytes(data)
    # Unknown extension: sniff by magic bytes (xlsx is a zip container).
    if data[:2] == b"PK":
        return read_xlsx_bytes(data)
    return read_csv_bytes(data)
