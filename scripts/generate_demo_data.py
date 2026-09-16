"""Generate fully synthetic demo bank statements (no real data).

    python scripts/generate_demo_data.py

Writes demo-data/statement.csv, demo-data/statement.xlsx (same month,
Debit/Credit layout) and demo-data/statement-2.csv (next month, ';' delimiter,
decimal comma — contains the same unknown merchants as the first statement).
"""

from __future__ import annotations

import csv
import datetime as dt
import random
from pathlib import Path

from openpyxl import Workbook

OUT = Path(__file__).resolve().parents[1] / "demo-data"
SEED = 20240901

# (description template, amount range, currency, kind)
RECURRING = [
    ("GOOGLE*GSUITE {ref6}", (46.80, 46.80), "USD"),
    ("AWS EMEA {ref8}", (112.40, 240.90), "USD"),
    ("SLACK TECHNOLOGIES {ref6}", (32.50, 32.50), "USD"),
    ("NOTION LABS INC", (16.00, 16.00), "USD"),
    ("GITHUB INC {ref7}", (21.00, 21.00), "USD"),
    ("FIGMA INC", (15.00, 15.00), "USD"),
    ("ZOOM.US {ref6}", (14.99, 14.99), "USD"),
    ("JETBRAINS S.R.O.", (24.90, 24.90), "EUR"),
    ("HETZNER ONLINE GMBH", (38.44, 41.20), "EUR"),
    ("PARTNER COMMUNICATION {ref6}", (89.90, 129.90), "ILS"),
    ("CELLCOM ISRAEL", (59.90, 59.90), "ILS"),
    ("BEZEQ INTERNATIONAL", (99.00, 99.00), "ILS"),
    ("CERCLI LTD {ref6}", (450.00, 450.00), "ILS"),
    ("DOCUSIGN {ref8}", (25.00, 25.00), "USD"),
    ("WEWORK TLV", (1350.00, 1350.00), "ILS"),
    ("NETFLIX.COM", (54.90, 54.90), "ILS"),
    ("SPOTIFY AB", (23.90, 23.90), "ILS"),
]
FREQUENT = [
    ("UBER *TRIP {ref6}", (28.0, 96.0), "ILS"),
    ("GETT TAXI {ref6}", (32.0, 110.0), "ILS"),
    ("YANGO {ref6}", (25.0, 80.0), "ILS"),
    ("WOLT IL {ref6}", (48.0, 165.0), "ILS"),
    ("TENBIS LTD", (42.0, 130.0), "ILS"),
    ("CAFE NIMROD", (14.0, 48.0), "ILS"),
    ("ARCAFFE DIZENGOFF", (18.0, 62.0), "ILS"),
    ("SUSHI BAR ROTHSCHILD", (85.0, 240.0), "ILS"),
    ("SHUFERSAL DEAL {ref4}", (60.0, 420.0), "ILS"),
    ("SUPER-PHARM {ref4}", (30.0, 180.0), "ILS"),
    ("RAMI LEVY", (90.0, 380.0), "ILS"),
    ("KSP COMPUTERS", (120.0, 900.0), "ILS"),
    ("OFFICE DEPOT {ref4}", (45.0, 260.0), "ILS"),
    ("APPLE.COM/BILL", (12.90, 39.90), "ILS"),
    ("STEAM PURCHASE", (29.0, 149.0), "ILS"),
    ("TLV PRINTHOUSE", (85.0, 420.0), "ILS"),
    ("ORNA PRINT & DESIGN", (120.0, 560.0), "ILS"),
    ("MISHKENOT REVIVIM {ref5}", (45.0, 210.0), "ILS"),
    ("LEUMIT SERVICES LTD", (150.0, 150.0), "ILS"),
    ("ALLTECH INSURANCE AGENCY", (312.0, 312.0), "ILS"),
    ("PAYPAL *ENVATO", (19.0, 59.0), "USD"),
    ("PAYPAL *UDEMY", (12.99, 89.99), "USD"),
    ("EL AL AIRLINES {ref6}", (890.0, 2450.0), "ILS"),
    ("BOOKING.COM {ref8}", (95.0, 380.0), "EUR"),
    ("AIRBNB * HMXQ{ref5}", (120.0, 640.0), "EUR"),
]
BANK = [
    ("BANK FEE ACCOUNT MAINTENANCE", (19.90, 19.90), "ILS"),
    ("FX COMMISSION {ref6}", (4.20, 38.50), "ILS"),
    ("WIRE FEE INTERNATIONAL", (25.00, 25.00), "USD"),
    ("CARD FEE ANNUAL", (12.00, 12.00), "ILS"),
]
TAXES = [
    ("TAX AUTHORITY VAT PAYMENT", (2350.0, 6890.0), "ILS"),
    ("BITUACH LEUMI NATIONAL INSURANCE", (1120.0, 1120.0), "ILS"),
    ("INCOME TAX ADVANCE PAYMENT", (1800.0, 4200.0), "ILS"),
]
INCOME = [
    ("INCOMING TRANSFER ACME DIGITAL LTD INV-{ref4}", (4500.0, 18000.0), "ILS"),
    ("INCOMING TRANSFER NORTHWIND STUDIO INV-{ref4}", (1200.0, 6400.0), "USD"),
    ("PAYONEER WITHDRAWAL {ref8}", (2100.0, 7300.0), "USD"),
    ("INCOMING SEPA CONTOSO GMBH INV-{ref4}", (900.0, 4800.0), "EUR"),
    ("REFUND WOLT IL", (48.0, 120.0), "ILS"),
]


def _fill(template: str, rnd: random.Random) -> str:
    out = template
    for n in (4, 5, 6, 7, 8):
        token = f"{{ref{n}}}"
        while token in out:
            out = out.replace(token, "".join(rnd.choice("0123456789") for _ in range(n)), 1)
    return out


def _amount(rng: tuple[float, float], rnd: random.Random) -> float:
    lo, hi = rng
    return round(lo if lo == hi else rnd.uniform(lo, hi), 2)


def make_month(year: int, month: int, seed: int, target: int) -> list[tuple[dt.date, str, float, str]]:
    rnd = random.Random(seed)
    days = (dt.date(year + (month == 12), (month % 12) + 1, 1) - dt.date(year, month, 1)).days
    rows: list[tuple[dt.date, str, float, str]] = []

    def add(pool, count, sign):
        for _ in range(count):
            template, rng, cur = rnd.choice(pool)
            rows.append(
                (dt.date(year, month, rnd.randint(1, days)), _fill(template, rnd), sign * _amount(rng, rnd), cur)
            )

    for template, rng, cur in RECURRING:  # once each, on a fixed-ish day
        rows.append(
            (dt.date(year, month, min(days, rnd.randint(1, 10))), _fill(template, rnd), -_amount(rng, rnd), cur)
        )
    add(BANK, 6, -1)
    add(TAXES, 3, -1)
    add(INCOME, 11, +1)
    add(FREQUENT, max(0, target - len(rows)), -1)
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def write_csv(path: Path, rows, *, delimiter=",", decimal=".", datefmt="%Y-%m-%d") -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(["Date", "Description", "Amount", "Currency"])
        for d, desc, amt, cur in rows:
            amount = f"{amt:.2f}".replace(".", decimal)
            w.writerow([d.strftime(datefmt), desc, amount, cur])


def write_xlsx_debit_credit(path: Path, rows) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    ws.append(["Transaction Date", "Details", "Debit", "Credit", "Currency"])
    for d, desc, amt, cur in rows:
        ws.append([d, desc, abs(amt) if amt < 0 else None, amt if amt > 0 else None, cur])
    for cell in ws["A"][1:]:
        cell.number_format = "yyyy-mm-dd"
    wb.save(path)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    month1 = make_month(2024, 9, SEED, 300)
    month2 = make_month(2024, 10, SEED + 1, 140)
    write_csv(OUT / "statement.csv", month1)
    write_xlsx_debit_credit(OUT / "statement.xlsx", month1)
    write_csv(OUT / "statement-2.csv", month2, delimiter=";", decimal=",", datefmt="%d.%m.%Y")
    print(f"statement.csv / statement.xlsx: {len(month1)} rows; statement-2.csv: {len(month2)} rows")


if __name__ == "__main__":
    main()
