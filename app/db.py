"""SQLAlchemy 2 engine, session factory and ORM models (SQLite)."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import settings
from app.money import canonical, to_decimal

log = logging.getLogger(__name__)

# PRAGMA user_version of a database created/migrated by this code.
# 0/1: v0.1 schema (amount stored as REAL); 2: exact decimal amounts + import audit + balances.
SCHEMA_VERSION = 2


class DecimalString(TypeDecorator):
    """Exact decimal stored as a canonical TEXT value (``"-12.40"``).

    SQLite has no decimal type — ``Numeric`` would be stored as a binary REAL —
    so money is kept as text and converted to/from :class:`Decimal` at the ORM
    boundary. The scale is preserved (``12.300`` stays ``12.300``).
    """

    impl = String(64)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, Decimal):
            if isinstance(value, float):
                raise TypeError("binary float passed as a money value; convert to Decimal first")
            value = to_decimal(value)
            if value is None:
                raise ValueError("not a decimal value")
        return canonical(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return to_decimal(value)


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class MerchantRule(Base):
    """A learned mapping normalized merchant key -> category. Highest priority."""

    __tablename__ = "merchant_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Setting(Base):
    """Key/value app settings (e.g. use_ai). API keys are NOT stored here."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


class PendingUpload(Base):
    """Raw rows of an uploaded file waiting for column mapping.

    Deleted as soon as the mapping is confirmed — we never keep the source file.
    """

    __tablename__ = "pending_uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    columns_json: Mapped[str] = mapped_column(Text)
    rows_json: Mapped[str] = mapped_column(Text)
    detected_json: Mapped[str] = mapped_column(Text)
    # Source line/row number of each entry in rows_json (JSON list); NULL for uploads made before v0.2.
    row_numbers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Statement(Base):
    __tablename__ = "statements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    # Import audit. NULL for statements imported before v0.2 (the audit did not exist yet).
    source_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imported_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ignored_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    not_imported_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audit_reviewed: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="statement", cascade="all, delete-orphan", order_by="Transaction.row_index"
    )
    skipped_rows: Mapped[list[SkippedRow]] = relationship(
        back_populates="statement", cascade="all, delete-orphan", order_by="SkippedRow.source_row"
    )
    balances: Mapped[list[StatementBalance]] = relationship(
        back_populates="statement", cascade="all, delete-orphan", order_by="StatementBalance.currency"
    )

    @property
    def has_audit(self) -> bool:
        return self.source_rows is not None


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement_id: Mapped[str] = mapped_column(ForeignKey("statements.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    date: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ISO yyyy-mm-dd
    description: Mapped[str] = mapped_column(Text)
    normalized_merchant: Mapped[str] = mapped_column(String(255), index=True)
    amount: Mapped[Decimal] = mapped_column(DecimalString)
    currency: Mapped[str] = mapped_column(String(8))
    direction: Mapped[str] = mapped_column(String(8))  # debit | credit
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category_source: Mapped[str] = mapped_column(String(16))  # rule|builtin|ai|manual|income|unknown
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    statement: Mapped[Statement] = relationship(back_populates="transactions")

    @property
    def needs_review(self) -> bool:
        return self.category is None


class SkippedRow(Base):
    """Audit record for a source row that did not become a normal transaction.

    `status` is ``ignored`` (safe to skip: empty, balance, footer…) or ``attention``
    (possible lost transaction). `imported` is True for rows that were imported
    but carry a warning (e.g. an unreadable date). `cells_json` holds the row's
    cells from *this* upload only, so the user can review it; it is deleted with
    the statement and can be cleared on its own.
    """

    __tablename__ = "skipped_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement_id: Mapped[str] = mapped_column(ForeignKey("statements.id", ondelete="CASCADE"), index=True)
    source_row: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # ignored | attention
    reason: Mapped[str] = mapped_column(String(32))
    imported: Mapped[bool] = mapped_column(Boolean, default=False)
    cell_index: Mapped[int | None] = mapped_column(Integer, nullable=True)  # problem cell, for highlighting
    cells_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    statement: Mapped[Statement] = relationship(back_populates="skipped_rows")


class StatementBalance(Base):
    """Opening/closing balance of one currency of a statement (detected from the file or entered by the user)."""

    __tablename__ = "statement_balances"
    __table_args__ = (UniqueConstraint("statement_id", "currency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement_id: Mapped[str] = mapped_column(ForeignKey("statements.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(8))
    opening: Mapped[Decimal | None] = mapped_column(DecimalString, nullable=True)
    closing: Mapped[Decimal | None] = mapped_column(DecimalString, nullable=True)
    opening_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # detected | manual
    closing_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    opening_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    closing_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    statement: Mapped[Statement] = relationship(back_populates="balances")


def make_engine(database_url: str | None = None):
    url = database_url or settings.database_url
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        db_path = url.removeprefix("sqlite:///")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def _column_types(conn, table: str) -> dict[str, str]:
    return {row[1]: (row[2] or "").upper() for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def migrate(target_engine) -> list[str]:
    """Bring an existing SQLite database up to SCHEMA_VERSION. Idempotent; returns the steps applied.

    v1 → v2:
      * ``transactions.amount`` REAL → exact TEXT decimal. v0.1 always stored
        ``round(amount, 2)``, so ``printf('%.2f', amount)`` recovers the exact
        intended value. The table is rebuilt (SQLite cannot change a column type).
      * new nullable audit columns on ``statements`` and ``pending_uploads``;
      * new tables ``skipped_rows`` and ``statement_balances`` (via create_all).
    """
    steps: list[str] = []
    if not str(target_engine.url).startswith("sqlite"):
        return steps
    tables = set(inspect(target_engine).get_table_names())
    with target_engine.begin() as conn:
        if "transactions" in tables:
            types = _column_types(conn, "transactions")
            if types.get("amount") in ("REAL", "FLOAT", "DOUBLE", "NUMERIC"):
                conn.execute(text("PRAGMA foreign_keys=OFF"))
                conn.execute(text("ALTER TABLE transactions RENAME TO transactions_v1"))
                conn.execute(text("DROP INDEX IF EXISTS ix_transactions_statement_id"))
                conn.execute(text("DROP INDEX IF EXISTS ix_transactions_normalized_merchant"))
                Transaction.__table__.create(conn)
                cols = [c.name for c in Transaction.__table__.columns]
                select_cols = ["printf('%.2f', amount)" if c == "amount" else c for c in cols]
                conn.execute(
                    text(
                        f"INSERT INTO transactions ({', '.join(cols)}) "
                        f"SELECT {', '.join(select_cols)} FROM transactions_v1"
                    )
                )
                conn.execute(text("DROP TABLE transactions_v1"))
                conn.execute(text("PRAGMA foreign_keys=ON"))
                steps.append("transactions.amount REAL -> TEXT decimal")
        for table, model in (
            ("statements", Statement),
            ("pending_uploads", PendingUpload),
            ("skipped_rows", SkippedRow),
            ("statement_balances", StatementBalance),
        ):
            if table not in tables:
                continue
            existing = _column_types(conn, table)
            for column in model.__table__.columns:
                if column.name not in existing:
                    ddl = column.type.compile(dialect=conn.dialect)
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column.name} {ddl}"))
                    steps.append(f"{table}.{column.name} added")
    Base.metadata.create_all(target_engine)
    with target_engine.begin() as conn:
        conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
    if steps:
        log.info("database migrated to schema v%s (%d step(s))", SCHEMA_VERSION, len(steps))
    return steps


def init_db(target_engine=None) -> None:
    migrate(target_engine or engine)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
