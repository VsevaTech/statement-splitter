# Demo data

Everything here is **synthetic** — no real bank, merchant or personal data.
The files are produced by `scripts/generate_demo_data.py` with a fixed seed, so
they are reproducible byte-for-byte (CSV) and CI checks that.

| File | Layout | Rows |
|---|---|---|
| `statement.csv` | `Date, Description, Amount, Currency` — signed amounts, comma delimiter, plus `Opening balance` / `Closing balance` lines, one empty row, a `TOTAL ILS` footer and one malformed amount (`12.3O`) | 305 source rows = 300 imported + 4 ignored + 1 needs attention; ILS reconciles to 0.00 |
| `statement.xlsx` | the same month as `Transaction Date, Details, Debit, Credit, Currency, Balance` — Excel date cells | 305 → 300 |
| `statement-2.csv` | next month, `;` delimiter, decimal comma, `dd.mm.yyyy` dates | 140 |
| `statement-balanced.csv` | opening 1000.00 + 500.10 − 200.20 − 100.30 = closing 1199.60 | difference 0.00 |
| `statement-unbalanced.csv` | the same with closing 1187.20 | difference −12.40 |

The CSV files are byte-for-byte reproducible and CI checks that; `scripts/check_demo_integrity.py`
verifies the counts and balance differences above.

Regenerate with:

```bash
python scripts/generate_demo_data.py
```
