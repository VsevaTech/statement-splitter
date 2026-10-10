"""v0.3: pending-upload expiry and bounded upload processing."""

from __future__ import annotations

import asyncio
import datetime as dt
import io
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select

from app import services
from app.config import settings
from app.db import PendingUpload, Statement, Transaction
from app.importer import SUPPORTED_EXTENSIONS, StatementImportError, read_statement
from app.main import _cleanup_loop, app
from tests.conftest import SIMPLE_CSV, csv_bytes, xlsx_bytes

AMBIGUOUS_CSV = "colA,colB,colC\n2024-09-02,foo,1\n2024-09-03,bar,2\n"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def _pending_count(session) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(PendingUpload))


def _make_pending(session, age_hours: float) -> str:
    pending, _ = services.create_pending_upload(session, "s.csv", csv_bytes(AMBIGUOUS_CSV))
    pending.created_at = _now() - dt.timedelta(hours=age_hours)
    session.commit()
    return pending.id


def _error_of(resp) -> str:
    assert resp.status_code == 303, resp.text
    loc = resp.headers["location"]
    assert loc.startswith("/?error="), loc
    return unquote(loc[len("/?error=") :])


def _post(client, name: str, data: bytes):
    return client.post("/upload", files={"file": (name, data)}, follow_redirects=False)


# ------------------------------------------------------------------ pending upload TTL
def test_default_ttl_is_24_hours():
    assert settings.pending_upload_ttl_hours == 24


def test_cleanup_keeps_23h_and_deletes_25h(session):
    young = _make_pending(session, 23)
    old = _make_pending(session, 25)
    assert services.cleanup_expired_uploads(session) == 1
    assert session.get(PendingUpload, young) is not None
    assert session.get(PendingUpload, old) is None


def test_cleanup_does_not_touch_completed_statements(session):
    statement, _pending, _mapping = services.import_statement(session, "ok.csv", csv_bytes(SIMPLE_CSV))
    assert statement is not None
    stale = _make_pending(session, 100)
    tx_before = session.scalar(select(func.count()).select_from(Transaction))
    assert tx_before > 0
    assert services.cleanup_expired_uploads(session) == 1
    session.expire_all()
    assert session.get(PendingUpload, stale) is None
    assert session.get(Statement, statement.id) is not None
    assert session.scalar(select(func.count()).select_from(Transaction)) == tx_before


def test_cleanup_respects_configured_ttl(session, monkeypatch):
    monkeypatch.setattr(settings, "pending_upload_ttl_hours", 1)
    _make_pending(session, 2)
    assert services.cleanup_expired_uploads(session) == 1


def test_expired_pending_returns_clear_410_and_is_deleted(client, session):
    pid = _make_pending(session, 25)
    resp = client.get(f"/uploads/{pid}/map")
    assert resp.status_code == 410
    assert "Upload expired" in resp.text
    assert "The temporary statement data has been deleted." in resp.text
    assert "Please upload your statement again." in resp.text
    assert _pending_count(session) == 0


def test_expired_pending_cannot_be_submitted(client, session):
    pid = _make_pending(session, 25)
    resp = client.post(f"/uploads/{pid}/map", data={"date": "colA", "description": "colB", "amount": "colC"})
    assert resp.status_code == 410
    assert "Upload expired" in resp.text
    assert session.scalar(select(func.count()).select_from(Statement)) == 0


def test_fresh_pending_is_still_usable(client, session):
    pid = _make_pending(session, 23)
    assert client.get(f"/uploads/{pid}/map").status_code == 200


def test_unknown_pending_is_a_friendly_410_not_a_bare_404(client):
    resp = client.get("/uploads/does-not-exist/map")
    assert resp.status_code == 410
    assert "upload your statement again" in resp.text.lower()


def test_cleanup_runs_at_startup(session):
    stale = _make_pending(session, 48)
    with TestClient(app):  # lifespan runs the sweep
        pass
    assert session.get(PendingUpload, stale) is None


def test_periodic_cleanup_loop_deletes_expired_rows(session):
    stale = _make_pending(session, 48)

    async def run() -> None:
        task = asyncio.create_task(_cleanup_loop(0.01))
        await asyncio.sleep(0.3)
        task.cancel()

    asyncio.run(run())
    session.expire_all()
    assert session.get(PendingUpload, stale) is None


def test_periodic_cleanup_survives_errors(monkeypatch):
    calls = {"n": 0}

    def boom() -> int:
        calls["n"] += 1
        raise RuntimeError("db locked")

    monkeypatch.setattr("app.main._run_cleanup", boom)

    async def run() -> None:
        task = asyncio.create_task(_cleanup_loop(0.01))
        await asyncio.sleep(0.2)
        assert not task.done()
        task.cancel()

    asyncio.run(run())
    assert calls["n"] >= 2


# ------------------------------------------------------------------ bounded upload
def test_oversized_upload_rejected_without_pending(client, session, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    payload = b"a,b\n" + b"1,2\n" * (1024 * 1024 // 4)  # just over 1 MB
    assert len(payload) > 1024 * 1024
    assert "larger than 1 MB" in _error_of(_post(client, "big.csv", payload))
    assert _pending_count(session) == 0


def test_upload_at_exact_limit_is_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    payload = SIMPLE_CSV.encode()
    filler = ("2024-09-08,%s,-1.00,ILS\n" % ("x" * 50000)).encode()
    while len(payload) + len(filler) <= 1024 * 1024:
        payload += filler
    tail = b"2024-09-09,,-1.00,ILS\n"
    payload += tail.replace(b",,", b",%s," % (b"y" * (1024 * 1024 - len(payload) - len(tail))))
    assert len(payload) == 1024 * 1024
    resp = _post(client, "edge.csv", payload)
    assert resp.status_code == 303 and not resp.headers["location"].startswith("/?error=")


def test_upload_reads_at_most_limit_plus_one_byte(monkeypatch, session):
    from app.main import upload

    monkeypatch.setattr(settings, "max_upload_mb", 1)
    limit = 1024 * 1024
    seen: list[int | None] = []

    class FakeFile:
        filename = "big.csv"

        async def read(self, size: int | None = None) -> bytes:
            seen.append(size)
            return b"x" * (size if size is not None else 50 * limit)

    resp = asyncio.run(upload(session, FakeFile()))
    assert seen == [limit + 1]
    assert resp.status_code == 303 and "larger" in unquote(resp.headers["location"])


def test_empty_upload_is_rejected(client):
    assert "empty" in _error_of(_post(client, "e.csv", b""))


def _zip_bomb_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "Amount"])
    chunk = "A" * 2000
    for _ in range(2500):  # ~5 MB of XML that deflates to a few KB
        ws.append(["2024-09-01", chunk, 1])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_zip_bomb_xlsx_rejected_by_uncompressed_size(client, session, monkeypatch):
    bomb = _zip_bomb_xlsx()
    monkeypatch.setattr(settings, "max_xlsx_uncompressed_mb", 1)
    assert len(bomb) < 200 * 1024  # small on the wire, passes the upload limit
    assert "uncompressed" in _error_of(_post(client, "bomb.xlsx", bomb))
    assert _pending_count(session) == 0


def test_xlsx_row_limit(client, session, monkeypatch):
    monkeypatch.setattr(settings, "max_rows", 50)
    data = xlsx_bytes(["Date", "Description", "Amount"], [["2024-09-01", f"m{i}", 1] for i in range(100)])
    assert "too many rows" in _error_of(_post(client, "rows.xlsx", data))
    assert _pending_count(session) == 0


def test_xlsx_column_limit(client, session, monkeypatch):
    monkeypatch.setattr(settings, "max_columns", 5)
    data = xlsx_bytes([f"c{i}" for i in range(10)], [[1] * 10])
    assert "too many columns" in _error_of(_post(client, "cols.xlsx", data))
    assert _pending_count(session) == 0


def test_sparse_xlsx_with_huge_declared_dimension_is_rejected_fast(client, monkeypatch):
    monkeypatch.setattr(settings, "max_rows", 1000)
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "Date"
    ws["B1"] = "Amount"
    ws["B1000000"] = 1
    buf = io.BytesIO()
    wb.save(buf)
    assert "too many rows" in _error_of(_post(client, "sparse.xlsx", buf.getvalue()))


def test_csv_row_and_column_limits(client, monkeypatch):
    monkeypatch.setattr(settings, "max_rows", 10)
    rows = "\n".join(f"2024-09-01,m{i},1" for i in range(50))
    assert "too many rows" in _error_of(_post(client, "r.csv", csv_bytes("Date,Description,Amount\n" + rows)))
    monkeypatch.setattr(settings, "max_columns", 3)
    assert "too many columns" in _error_of(_post(client, "c.csv", csv_bytes("a,b,c,d,e\n1,2,3,4,5\n")))


@pytest.mark.parametrize(
    "name,data",
    [
        ("trunc.xlsx", b"PK\x03\x04" + b"\x00" * 40),
        ("fake.xlsx", b"this is not a workbook at all"),
        ("empty-zip.xlsx", b"PK\x05\x06" + b"\x00" * 18),
        ("nul.csv", b"\x00\x00\x00\x00\x00"),
    ],
)
def test_malformed_files_give_controlled_error_not_500(client, session, name, data):
    resp = _post(client, name, data)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/?error=")
    assert "Traceback" not in unquote(resp.headers["location"])
    assert _pending_count(session) == 0


def test_valid_zip_that_is_not_a_workbook_is_rejected(client):
    buf = io.BytesIO()
    import zipfile

    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    assert "Excel" in _error_of(_post(client, "x.xlsx", buf.getvalue()))


def test_app_keeps_working_after_rejected_files(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    _post(client, "big.csv", b"x" * (1024 * 1024 + 1))
    _post(client, "bad.xlsx", b"nope")
    resp = _post(client, "ok.csv", csv_bytes(SIMPLE_CSV))
    assert resp.headers["location"].startswith("/statements/")
    assert client.get("/health").status_code == 200


# ------------------------------------------------------------------ declared formats == implemented formats
def test_xls_is_not_declared_as_supported(client):
    assert ".xls" not in SUPPORTED_EXTENSIONS
    assert set(SUPPORTED_EXTENSIONS) == {".csv", ".xlsx", ".txt"}
    assert ".xls," not in client.get("/").text and '.xls"' not in client.get("/").text


def test_legacy_xls_rejected_with_clear_message(client):
    assert ".xlsx or .csv" in _error_of(_post(client, "old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100))


def test_xls_content_renamed_to_xlsx_is_still_recognised():
    with pytest.raises(StatementImportError, match="Legacy .xls"):
        read_statement("renamed.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100)


def test_only_import_errors_escape_read_statement(monkeypatch):
    def explode(_data: bytes):
        raise MemoryError("boom")

    monkeypatch.setattr("app.importer.read_csv_bytes", explode)
    with pytest.raises(StatementImportError):
        read_statement("a.csv", b"a,b\n1,2\n")
