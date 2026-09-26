"""Deterministic financial-integrity check of the synthetic demo data (used by CI).

    python scripts/check_demo_integrity.py            # in-process, temporary SQLite
    python scripts/check_demo_integrity.py http://localhost:8000   # against a running instance

Exits non-zero unless:
  * statement.csv / statement.xlsx: 305 source rows = 300 imported + 4 ignored + 1 needs attention, ILS balanced
  * statement-balanced.csv:   difference 0.00
  * statement-unbalanced.csv: difference -12.40
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo-data"
EXPECTED = {
    "statement.csv": ((305, 300, 4, 1), "balanced", "0.00"),
    "statement.xlsx": ((305, 300, 4, 1), "balanced", "0.00"),
    "statement-balanced.csv": ((5, 3, 2, 0), "balanced", "0.00"),
    "statement-unbalanced.csv": ((5, 3, 2, 0), "difference", "-12.40"),
}


def _in_process() -> dict[str, dict]:
    os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/integrity-check.db"
    os.environ["GEMINI_API_KEY"] = ""
    sys.path.insert(0, str(ROOT))
    from app import services
    from app.db import SessionLocal, init_db

    init_db()
    out = {}
    with SessionLocal() as session:
        for name in EXPECTED:
            statement, _pending, _mapping = services.import_statement(session, name, (DEMO / name).read_bytes())
            if statement is None:
                raise SystemExit(f"{name}: column detection was not confident")
            out[name] = services.integrity_report(session, statement).to_json()
    return out


def _remote(base_url: str) -> dict[str, dict]:
    import httpx

    out = {}
    with httpx.Client(base_url=base_url, timeout=30) as client:
        for name in EXPECTED:
            resp = client.post("/upload", files={"file": (name, (DEMO / name).read_bytes())}, follow_redirects=False)
            match = re.fullmatch(r"/statements/([0-9a-f-]{36})", resp.headers.get("location", ""))
            if resp.status_code != 303 or not match:
                raise SystemExit(f"{name}: upload failed (HTTP {resp.status_code})")
            out[name] = client.get(f"/api/statements/{match.group(1)}").json()
    return out


def main(base_url: str | None = None) -> None:
    results = _remote(base_url) if base_url else _in_process()
    failures = []
    for name, ((source, imported, ignored, attention), status, difference) in EXPECTED.items():
        data = results[name]
        s = data["import_summary"]
        got_counts = (s["source_rows"], s["imported"], s["ignored"], s["needs_attention"])
        check = data["balance_check"] or {}
        line = (
            f"{name:26} rows {got_counts[0]}={got_counts[1]}+{got_counts[2]}+{got_counts[3]}  "
            f"balance {check.get('status')} difference {check.get('difference')} {check.get('currency')}"
        )
        print(line)
        if got_counts != (source, imported, ignored, attention):
            failures.append(f"{name}: counts {got_counts} != {(source, imported, ignored, attention)}")
        if (check.get("status"), check.get("difference")) != (status, difference):
            failures.append(f"{name}: balance {check.get('status')}/{check.get('difference')} != {status}/{difference}")
    if failures:
        raise SystemExit("FAILED\n" + "\n".join(failures))
    print("demo integrity: PASS")


if __name__ == "__main__":
    main(*sys.argv[1:2])
