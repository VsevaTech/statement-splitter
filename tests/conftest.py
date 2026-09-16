from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="statement-splitter-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["GEMINI_API_KEY"] = ""  # tests never use a real key
os.environ.pop("GEMINI_MODEL", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "demo-data"


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def session():
    with SessionLocal() as s:
        yield s


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def csv_bytes(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


def xlsx_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


SIMPLE_CSV = """Date,Description,Amount,Currency
2024-09-02,GOOGLE*GSUITE 839291,-46.80,USD
2024-09-03,UBER *TRIP 123456,-38.50,ILS
2024-09-04,CERCLI LTD 778812,-450.00,ILS
2024-09-05,INCOMING TRANSFER ACME LTD INV-1001,12000.00,ILS
2024-09-06,BANK FEE ACCOUNT MAINTENANCE,-19.90,ILS
2024-09-07,MYSTERY SHOP 42,-99.00,EUR
"""
