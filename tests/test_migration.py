"""v0.1 → v0.2 SQLite migration: REAL amounts become exact decimal text, nothing is lost."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db import SCHEMA_VERSION, Statement, Transaction, migrate

V1_SCHEMA = [
    """CREATE TABLE merchant_rules (id INTEGER NOT NULL, merchant_key VARCHAR(255) NOT NULL,
       category VARCHAR(64) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id))""",
    "CREATE UNIQUE INDEX ix_merchant_rules_merchant_key ON merchant_rules (merchant_key)",
    """CREATE TABLE settings ("key" VARCHAR(64) NOT NULL, value VARCHAR(255) NOT NULL, PRIMARY KEY ("key"))""",
    """CREATE TABLE pending_uploads (id VARCHAR(36) NOT NULL, filename VARCHAR(255) NOT NULL, columns_json TEXT NOT NULL,
       rows_json TEXT NOT NULL, detected_json TEXT NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id))""",
    """CREATE TABLE statements (id VARCHAR(36) NOT NULL, filename VARCHAR(255) NOT NULL, created_at DATETIME NOT NULL,
       PRIMARY KEY (id))""",
    """CREATE TABLE transactions (id INTEGER NOT NULL, statement_id VARCHAR(36) NOT NULL, row_index INTEGER NOT NULL,
       date VARCHAR(32), description TEXT NOT NULL, normalized_merchant VARCHAR(255) NOT NULL, amount FLOAT NOT NULL,
       currency VARCHAR(8) NOT NULL, direction VARCHAR(8) NOT NULL, category VARCHAR(64),
       category_source VARCHAR(16) NOT NULL, confidence FLOAT NOT NULL, PRIMARY KEY (id),
       FOREIGN KEY(statement_id) REFERENCES statements (id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_transactions_normalized_merchant ON transactions (normalized_merchant)",
    "CREATE INDEX ix_transactions_statement_id ON transactions (statement_id)",
]


def _v1_database(path):
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        for ddl in V1_SCHEMA:
            conn.execute(text(ddl))
        conn.execute(text("INSERT INTO statements VALUES ('s1', 'old.csv', '2024-09-01 10:00:00')"))
        for i, amount in enumerate([0.1, -46.8, -0.3, 12000.0, -1234.56]):
            conn.execute(
                text(
                    "INSERT INTO transactions VALUES (:id, 's1', :i, '2024-09-01', 'X', 'X', :a, 'ILS', "
                    "'debit', 'Office', 'manual', 1.0)"
                ),
                {"id": i + 1, "i": i, "a": amount},
            )
        conn.execute(text("INSERT INTO merchant_rules VALUES (1, 'CERCLI', 'Accounting', '2024-09-01', '2024-09-01')"))
    return engine


def test_v1_database_is_migrated_without_losing_precision(tmp_path):
    engine = _v1_database(tmp_path / "v1.db")
    steps = migrate(engine)
    assert "transactions.amount REAL -> TEXT decimal" in steps
    with Session(engine) as session:
        amounts = [t.amount for t in session.query(Transaction).order_by(Transaction.row_index)]
        assert amounts == [
            Decimal("0.10"),
            Decimal("-46.80"),
            Decimal("-0.30"),
            Decimal("12000.00"),
            Decimal("-1234.56"),
        ]
        st = session.get(Statement, "s1")
        assert st.source_rows is None and not st.has_audit  # honest: no audit for old imports
        assert len(st.transactions) == 5
    with engine.connect() as conn:
        assert conn.execute(text("SELECT typeof(amount) FROM transactions LIMIT 1")).scalar() == "text"
        assert conn.execute(text("SELECT category FROM merchant_rules")).scalar() == "Accounting"
        assert conn.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert {"skipped_rows", "statement_balances", "transactions"} <= tables and "transactions_v1" not in tables


def test_migration_is_idempotent(tmp_path):
    engine = _v1_database(tmp_path / "v1.db")
    migrate(engine)
    assert migrate(engine) == []


def test_old_statement_renders_without_audit(tmp_path, monkeypatch):
    from app import services

    engine = _v1_database(tmp_path / "v1.db")
    migrate(engine)
    with Session(engine) as session:
        report = services.integrity_report(session, session.get(Statement, "s1"))
        assert not report.import_summary.available
        assert report.totals[0].net == Decimal("10718.44")
