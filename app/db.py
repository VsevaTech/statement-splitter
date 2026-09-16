"""SQLAlchemy 2 engine, session factory and ORM models (SQLite)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from app.config import settings


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
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Statement(Base):
    __tablename__ = "statements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="statement", cascade="all, delete-orphan", order_by="Transaction.row_index"
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement_id: Mapped[str] = mapped_column(ForeignKey("statements.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    date: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ISO yyyy-mm-dd
    description: Mapped[str] = mapped_column(Text)
    normalized_merchant: Mapped[str] = mapped_column(String(255), index=True)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8))
    direction: Mapped[str] = mapped_column(String(8))  # debit | credit
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category_source: Mapped[str] = mapped_column(String(16))  # rule|builtin|ai|manual|income|unknown
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    statement: Mapped[Statement] = relationship(back_populates="transactions")

    @property
    def needs_review(self) -> bool:
        return self.category is None


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


def init_db(target_engine=None) -> None:
    Base.metadata.create_all(target_engine or engine)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
