"""Capture README screenshots against a running instance (default http://localhost:8000).

    pip install playwright && playwright install chromium
    python scripts/take_screenshots.py [base_url]

Uploads demo-data/statement.csv, opens the review and skipped-rows screens and saves PNGs to docs/.
Set PLAYWRIGHT_CHROMIUM_EXECUTABLE to use an already installed Chromium.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def main(base_url: str = "http://localhost:8000") -> None:
    DOCS.mkdir(exist_ok=True)
    with httpx.Client(base_url=base_url, timeout=30, follow_redirects=False) as client:
        resp = client.post(
            "/upload", files={"file": ("statement.csv", (ROOT / "demo-data" / "statement.csv").read_bytes())}
        )
        # A successful upload answers 303 with the statement URL; httpx would treat
        # that redirect as an error, so check it explicitly.
        if resp.status_code != 303 or "location" not in resp.headers:
            raise SystemExit(f"upload failed: HTTP {resp.status_code} {resp.text[:200]}")
        statement_path = resp.headers["location"]
    with sync_playwright() as pw:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
        browser = pw.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.goto(f"{base_url}{statement_path}?filter=review", wait_until="networkidle")
        page.screenshot(path=str(DOCS / "screenshot-review.png"))
        page.locator("#integrity").screenshot(path=str(DOCS / "screenshot-integrity.png"))
        page.goto(f"{base_url}{statement_path}/skipped", wait_until="networkidle")
        page.screenshot(path=str(DOCS / "screenshot-skipped-rows.png"), full_page=True)
        page.goto(f"{base_url}/", wait_until="networkidle")
        page.screenshot(path=str(DOCS / "screenshot-home.png"))
        browser.close()
    print("saved", sorted(p.name for p in DOCS.glob("*.png")))


if __name__ == "__main__":
    main(*sys.argv[1:2])
