"""Financial integrity report: import audit, per-currency totals and statement balance reconciliation.

Direction semantics follow the rest of the app: a transaction's *signed* amount
is the source of truth (credits > 0, debits < 0 — after Debit/Credit columns and
Dr/Cr indicators have been applied at import). Therefore

    income (credits)  = Σ positive amounts
    expenses (debits) = Σ |negative amounts|
    net movement      = Σ signed amounts = income − expenses

per currency, independent of categories (a *Personal* expense still moved money).
Currencies are never added together, and a balance check only uses the opening
and closing balance of the same currency.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from app.money import ZERO, dsum, json_money

BALANCED = "balanced"
DIFFERENCE = "difference"
INCOMPLETE = "incomplete"


class _TxLike(Protocol):
    amount: Decimal
    currency: str


class _BalanceLike(Protocol):
    currency: str
    opening: Decimal | None
    closing: Decimal | None
    opening_source: str | None
    closing_source: str | None
    opening_row: int | None
    closing_row: int | None
    confirmed: bool


@dataclass(slots=True)
class CurrencyTotals:
    currency: str
    count: int
    income: Decimal
    expenses: Decimal
    net: Decimal

    def to_json(self) -> dict:
        return {
            "currency": self.currency,
            "transactions": self.count,
            "income": json_money(self.income),
            "expenses": json_money(self.expenses),
            "net_movement": json_money(self.net),
        }


@dataclass(slots=True)
class BalanceCheck:
    currency: str
    opening: Decimal | None
    actual_closing: Decimal | None
    income: Decimal
    expenses: Decimal
    net_movement: Decimal
    opening_source: str | None = None
    closing_source: str | None = None
    opening_row: int | None = None
    closing_row: int | None = None
    confirmed: bool = False

    @property
    def expected_closing(self) -> Decimal | None:
        return None if self.opening is None else self.opening + self.net_movement

    @property
    def difference(self) -> Decimal | None:
        """actual − expected (negative: the bank shows less money than the transactions explain)."""
        if self.expected_closing is None or self.actual_closing is None:
            return None
        return self.actual_closing - self.expected_closing

    @property
    def status(self) -> str:
        if self.difference is None:
            return INCOMPLETE
        return BALANCED if self.difference == 0 else DIFFERENCE

    @property
    def missing(self) -> list[str]:
        out = []
        if self.opening is None:
            out.append("opening balance")
        if self.actual_closing is None:
            out.append("closing balance")
        return out

    @property
    def detected_unconfirmed(self) -> bool:
        return not self.confirmed and "detected" in (self.opening_source, self.closing_source)

    def to_json(self) -> dict:
        return {
            "currency": self.currency,
            "opening": json_money(self.opening),
            "income": json_money(self.income),
            "expenses": json_money(self.expenses),
            "net_movement": json_money(self.net_movement),
            "expected_closing": json_money(self.expected_closing),
            "actual_closing": json_money(self.actual_closing),
            "difference": json_money(self.difference),
            "status": self.status,
            "opening_source": self.opening_source,
            "closing_source": self.closing_source,
            "confirmed": self.confirmed,
        }


@dataclass(slots=True)
class ImportSummary:
    source_rows: int | None
    imported: int
    ignored: int | None
    not_imported: int | None
    imported_with_warnings: int | None

    @property
    def available(self) -> bool:
        return self.source_rows is not None

    @property
    def needs_attention(self) -> int | None:
        if self.not_imported is None:
            return None
        return self.not_imported + (self.imported_with_warnings or 0)

    @property
    def consistent(self) -> bool:
        """source rows = imported + ignored + not imported."""
        if not self.available:
            return True
        return self.source_rows == self.imported + (self.ignored or 0) + (self.not_imported or 0)

    def to_json(self) -> dict:
        return {
            "source_rows": self.source_rows,
            "imported": self.imported,
            "ignored": self.ignored,
            "needs_attention": self.needs_attention,
            "not_imported": self.not_imported,
            "imported_with_warnings": self.imported_with_warnings,
        }


@dataclass(slots=True)
class IntegrityReport:
    import_summary: ImportSummary
    totals: list[CurrencyTotals]
    balance_checks: list[BalanceCheck]
    audit_reviewed: bool = False
    currencies_without_balance: list[str] = field(default_factory=list)

    @property
    def balance_check(self) -> BalanceCheck | None:
        """The single balance check when exactly one currency has balance data (API convenience)."""
        return self.balance_checks[0] if len(self.balance_checks) == 1 else None

    def to_json(self) -> dict:
        return {
            "exact_decimal_arithmetic": True,
            "import_summary": self.import_summary.to_json(),
            "audit_reviewed": self.audit_reviewed,
            "currency_totals": [t.to_json() for t in self.totals],
            "balance_check": self.balance_check.to_json() if self.balance_check else None,
            "balance_checks": [b.to_json() for b in self.balance_checks],
            "currencies_without_balance": self.currencies_without_balance,
        }


def currency_totals(transactions: Iterable[_TxLike]) -> list[CurrencyTotals]:
    buckets: dict[str, list[Decimal]] = defaultdict(list)
    for tx in transactions:
        buckets[tx.currency].append(tx.amount)
    out = []
    for currency in sorted(buckets):
        amounts = buckets[currency]
        income = dsum(a for a in amounts if a > 0)
        expenses = dsum(-a for a in amounts if a < 0)
        out.append(CurrencyTotals(currency, len(amounts), income, expenses, dsum(amounts)))
    return out


def balance_checks(totals: list[CurrencyTotals], balances: Iterable[_BalanceLike]) -> list[BalanceCheck]:
    by_currency = {t.currency: t for t in totals}
    checks = []
    for b in sorted(balances, key=lambda x: x.currency):
        if b.opening is None and b.closing is None:
            continue
        t = by_currency.get(b.currency) or CurrencyTotals(b.currency, 0, ZERO, ZERO, ZERO)
        checks.append(
            BalanceCheck(
                currency=b.currency,
                opening=b.opening,
                actual_closing=b.closing,
                income=t.income,
                expenses=t.expenses,
                net_movement=t.net,
                opening_source=b.opening_source,
                closing_source=b.closing_source,
                opening_row=b.opening_row,
                closing_row=b.closing_row,
                confirmed=bool(b.confirmed),
            )
        )
    return checks


def build_report(statement, transactions: list[_TxLike]) -> IntegrityReport:
    """`statement` is a db.Statement (audit counters + balances relationship)."""
    totals = currency_totals(transactions)
    warnings = None
    if statement.source_rows is not None:
        warnings = sum(1 for r in statement.skipped_rows if r.status == "attention" and r.imported)
    summary = ImportSummary(
        source_rows=statement.source_rows,
        imported=len(transactions),
        ignored=statement.ignored_rows,
        not_imported=statement.not_imported_rows,
        imported_with_warnings=warnings,
    )
    checks = balance_checks(totals, statement.balances)
    checked = {c.currency for c in checks}
    return IntegrityReport(
        import_summary=summary,
        totals=totals,
        balance_checks=checks,
        audit_reviewed=bool(statement.audit_reviewed),
        currencies_without_balance=[t.currency for t in totals if t.currency not in checked],
    )
