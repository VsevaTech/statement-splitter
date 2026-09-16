"""Application services shared by the HTTP layer and the tests."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.ai import GeminiClient
from app.categories import is_valid_category
from app.categorize import categorize, load_rules, save_rule, summarize
from app.columns import ColumnMapping, detect_columns
from app.db import MerchantRule, PendingUpload, Setting, Statement, Transaction
from app.importer import RawTable, read_statement
from app.transform import normalize_rows

USE_AI_KEY = "use_ai"


# ---------------------------------------------------------------- settings
def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row else default


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    session.commit()


def ai_enabled(session: Session) -> bool:
    return get_setting(session, USE_AI_KEY, "off") == "on"


# ---------------------------------------------------------------- upload flow
def create_pending_upload(session: Session, filename: str, data: bytes) -> tuple[PendingUpload, ColumnMapping]:
    """Parse the file, detect columns and stash the raw rows until mapping is confirmed.

    The uploaded bytes are not written anywhere; only the parsed rows are kept
    (in SQLite) and they are deleted the moment the statement is created.
    """
    table: RawTable = read_statement(filename, data)
    mapping = detect_columns(table.columns, table.rows)
    pending = PendingUpload(
        id=str(uuid.uuid4()),
        filename=filename,
        columns_json=json.dumps(table.columns, ensure_ascii=False),
        rows_json=json.dumps(table.rows, ensure_ascii=False),
        detected_json=mapping.model_dump_json(),
    )
    session.add(pending)
    session.commit()
    return pending, mapping


def pending_table(pending: PendingUpload) -> tuple[list[str], list[list[str]]]:
    return json.loads(pending.columns_json), json.loads(pending.rows_json)


def finalize_upload(
    session: Session,
    pending: PendingUpload,
    mapping: ColumnMapping,
    *,
    use_ai: bool | None = None,
    ai_client: GeminiClient | None = None,
) -> Statement:
    columns, rows = pending_table(pending)
    normalized = normalize_rows(columns, rows, mapping)
    if use_ai is None:
        use_ai = ai_enabled(session)
    categorize(normalized, load_rules(session), use_ai=use_ai, ai_client=ai_client)

    statement = Statement(id=str(uuid.uuid4()), filename=pending.filename)
    session.add(statement)
    for tx in normalized:
        session.add(
            Transaction(
                statement_id=statement.id,
                row_index=tx.row_index,
                date=tx.date.isoformat() if tx.date else None,
                description=tx.description,
                normalized_merchant=tx.normalized_merchant,
                amount=tx.amount,
                currency=tx.currency,
                direction=tx.direction,
                category=tx.category,
                category_source=tx.category_source,
                confidence=tx.confidence,
            )
        )
    session.delete(pending)  # raw rows are gone as soon as we have the normalized model
    session.commit()
    return statement


def import_statement(
    session: Session,
    filename: str,
    data: bytes,
    *,
    mapping: ColumnMapping | None = None,
    use_ai: bool | None = None,
    ai_client: GeminiClient | None = None,
) -> tuple[Statement | None, PendingUpload, ColumnMapping]:
    """Convenience: upload + auto-map in one go. Returns (statement | None if mapping needed, pending, mapping)."""
    pending, detected = create_pending_upload(session, filename, data)
    effective = mapping or detected
    if not effective.is_complete or (mapping is None and not detected.confident):
        return None, pending, detected
    statement = finalize_upload(session, pending, effective, use_ai=use_ai, ai_client=ai_client)
    return statement, pending, effective


# ---------------------------------------------------------------- review
FILTERS = ("all", "review", "expenses", "income", "personal")


def list_transactions(session: Session, statement_id: str, filter_name: str = "all") -> list[Transaction]:
    stmt = select(Transaction).where(Transaction.statement_id == statement_id)
    if filter_name == "review":
        stmt = stmt.where(Transaction.category.is_(None))
    elif filter_name == "expenses":
        stmt = stmt.where(Transaction.direction == "debit", Transaction.category != "Personal")
    elif filter_name == "income":
        stmt = stmt.where(Transaction.direction == "credit")
    elif filter_name == "personal":
        stmt = stmt.where(Transaction.category == "Personal")
    return list(session.scalars(stmt.order_by(Transaction.row_index)))


def statement_summary(session: Session, statement_id: str) -> dict[str, int]:
    return summarize(list_transactions(session, statement_id, "all"))


def set_category(session: Session, tx: Transaction, category: str, *, remember: bool) -> Transaction:
    if not is_valid_category(category):
        raise ValueError(f"unknown category '{category}'")
    tx.category, tx.category_source, tx.confidence = category, "manual", 1.0
    if remember and tx.normalized_merchant:
        save_rule(session, tx.normalized_merchant, category)
    session.commit()
    return tx


def apply_to_similar(session: Session, statement_id: str, merchant_key: str, category: str) -> int:
    """Set `category` on every transaction of the statement with the same merchant key and save the rule."""
    if not is_valid_category(category):
        raise ValueError(f"unknown category '{category}'")
    save_rule(session, merchant_key, category)
    rows = session.scalars(
        select(Transaction).where(
            Transaction.statement_id == statement_id, Transaction.normalized_merchant == merchant_key
        )
    ).all()
    for tx in rows:
        tx.category, tx.category_source, tx.confidence = category, "manual", 1.0
    session.commit()
    return len(rows)


def similar_count(session: Session, statement_id: str, merchant_key: str) -> int:
    return (
        session.scalar(
            select(func.count(Transaction.id)).where(
                Transaction.statement_id == statement_id, Transaction.normalized_merchant == merchant_key
            )
        )
        or 0
    )


def delete_statement(session: Session, statement: Statement) -> None:
    session.delete(statement)
    session.commit()


def list_rules(session: Session) -> list[MerchantRule]:
    return list(session.scalars(select(MerchantRule).order_by(MerchantRule.merchant_key)))


def delete_rule(session: Session, rule_id: int) -> None:
    session.execute(delete(MerchantRule).where(MerchantRule.id == rule_id))
    session.commit()
