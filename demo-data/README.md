# Demo data

Everything here is **synthetic** — no real bank, merchant or personal data.
The files are produced by `scripts/generate_demo_data.py` with a fixed seed, so
they are reproducible byte-for-byte (CSV) and CI checks that.

| File | Layout | Rows |
|---|---|---|
| `statement.csv` | `Date, Description, Amount, Currency` — signed amounts, comma delimiter | 300 |
| `statement.xlsx` | `Transaction Date, Details, Debit, Credit, Currency` — Excel date cells | 300 |
| `statement-2.csv` | next month, `;` delimiter, decimal comma, `dd.mm.yyyy` dates | 140 |

Regenerate with:

```bash
python scripts/generate_demo_data.py
```
