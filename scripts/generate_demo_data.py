"""Generate fully synthetic demo bank statements (no real data).

    python scripts/generate_demo_data.py

Writes
  demo-data/statement.csv      300 transactions + opening/closing balance lines, an empty row,
                               a TOTAL footer and one malformed amount (305 source rows)
  demo-data/statement.xlsx     the same month in a Debit/Credit/Balance layout
  demo-data/statement-2.csv    next month, ';' delimiter, decimal comma — same unknown merchants
  demo-data/statement-balanced.csv    tiny statement that reconciles exactly (difference 0.00)
  demo-data/statement-unbalanced.csv  the same with a closing balance 12.40 lower than expected

All balances are computed with Decimal, so the demo reconciles to the cent.
"""

from __future__ import annotations

import csv
import datetime as dt
import random
from decimal import Decimal
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


# Deterministic tricky values: 0.10 + 0.20 + 0.30 must total exactly 0.60, plus one large amount.
TRICKY = [
    (3, "FX COMMISSION 000010", -0.10, "ILS"),
    (3, "FX COMMISSION 000020", -0.20, "ILS"),
    (3, "FX COMMISSION 000030", -0.30, "ILS"),
    (15, "INCOMING TRANSFER ACME DIGITAL LTD INV-9001", 123456.78, "ILS"),
]
OPENING_ILS = Decimal("12450.30")
MALFORMED_AMOUNT = "12.3O"  # letter O instead of zero — must be flagged, never read as 12.3 or 0


def make_month(
    year: int, month: int, seed: int, target: int, *, tricky: bool = False
) -> list[tuple[dt.date, str, float, str]]:
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
    if tricky:
        rows += [(dt.date(year, month, day), desc, amount, cur) for day, desc, amount, cur in TRICKY]
    add(BANK, 6, -1)
    add(TAXES, 3, -1)
    add(INCOME, 11, +1)
    add(FREQUENT, max(0, target - len(rows)), -1)
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def dec(amount: float) -> Decimal:
    return Decimal(f"{amount:.2f}")


def write_csv(path: Path, rows, *, delimiter=",", decimal=".", datefmt="%Y-%m-%d") -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(["Date", "Description", "Amount", "Currency"])
        for d, desc, amt, cur in rows:
            amount = f"{amt:.2f}".replace(".", decimal)
            w.writerow([d.strftime(datefmt), desc, amount, cur])


def ils_totals(rows) -> tuple[Decimal, Decimal, Decimal]:
    """(net movement, credits, debits) of the ILS transactions, exact."""
    amounts = [dec(a) for _d, _desc, a, cur in rows if cur == "ILS"]
    credits = sum((a for a in amounts if a > 0), Decimal("0.00"))
    debits = sum((-a for a in amounts if a < 0), Decimal("0.00"))
    return credits - debits, credits, debits


def write_audit_csv(path: Path, rows, year: int, month: int) -> None:
    """statement.csv: transactions framed by balance lines, plus an empty row, a malformed row and a footer."""
    net, _credits, _debits = ils_totals(rows)
    first, last = dt.date(year, month, 1), rows[-1][0]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Description", "Amount", "Currency"])
        w.writerow([first.isoformat(), "Opening balance", f"{OPENING_ILS}", "ILS"])
        for i, (d, desc, amt, cur) in enumerate(rows):
            if i == 150:
                w.writerow([])  # empty row in the middle of the statement
            if i == 200:
                w.writerow([d.isoformat(), "COFFEE SHOP ABC", MALFORMED_AMOUNT, "ILS"])
            w.writerow([d.isoformat(), desc, f"{amt:.2f}", cur])
        w.writerow([last.isoformat(), "Closing balance", f"{OPENING_ILS + net}", "ILS"])
        w.writerow(["", "TOTAL ILS", f"{net}", "ILS"])


def write_xlsx_debit_credit(path: Path, rows, year: int, month: int) -> None:
    net, credits, debits = ils_totals(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    ws.append(["Transaction Date", "Details", "Debit", "Credit", "Currency", "Balance"])
    ws.append([dt.date(year, month, 1), "Opening balance", None, None, "ILS", OPENING_ILS])
    for i, (d, desc, amt, cur) in enumerate(rows):
        if i == 150:
            ws.append([])
        if i == 200:
            ws.append([d, "COFFEE SHOP ABC", MALFORMED_AMOUNT, None, "ILS", None])
        ws.append([d, desc, abs(dec(amt)) if amt < 0 else None, dec(amt) if amt > 0 else None, cur, None])
    ws.append([rows[-1][0], "Closing balance", None, None, "ILS", OPENING_ILS + net])
    ws.append([None, "TOTAL ILS", debits, credits, "ILS", None])
    for cell in ws["A"][1:]:
        cell.number_format = "yyyy-mm-dd"
    for col in ("C", "D", "F"):
        for cell in ws[col][1:]:
            cell.number_format = "#,##0.00"
    wb.save(path)


def write_small_fixture(path: Path, closing: str) -> None:
    """Opening 1000.00 + 500.10 − 200.20 − 100.30 → expected closing 1199.60."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Description", "Amount", "Currency"])
        w.writerow(["2024-11-01", "Opening balance", "1000.00", "ILS"])
        w.writerow(["2024-11-03", "INCOMING TRANSFER ACME DIGITAL LTD INV-2001", "500.10", "ILS"])
        w.writerow(["2024-11-08", "OFFICE DEPOT 1182", "-200.20", "ILS"])
        w.writerow(["2024-11-15", "WOLT IL 551020", "-100.30", "ILS"])
        w.writerow(["2024-11-30", "Closing balance", closing, "ILS"])


def main() -> None:
    OUT.mkdir(exist_ok=True)
    month1 = make_month(2024, 9, SEED, 300, tricky=True)
    month2 = make_month(2024, 10, SEED + 1, 140)
    write_audit_csv(OUT / "statement.csv", month1, 2024, 9)
    write_xlsx_debit_credit(OUT / "statement.xlsx", month1, 2024, 9)
    write_csv(OUT / "statement-2.csv", month2, delimiter=";", decimal=",", datefmt="%d.%m.%Y")
    write_small_fixture(OUT / "statement-balanced.csv", "1199.60")
    write_small_fixture(OUT / "statement-unbalanced.csv", "1187.20")  # 12.40 short
    net, _c, _d = ils_totals(month1)
    print(
        f"statement.csv / statement.xlsx: {len(month1)} transactions + 5 audit rows "
        f"(ILS {OPENING_ILS} + {net} = {OPENING_ILS + net}); statement-2.csv: {len(month2)} rows"
    )


if __name__ == "__main__":
    main()
