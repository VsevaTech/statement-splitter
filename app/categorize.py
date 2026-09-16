"""Categorization pipeline: saved merchant rules > built-in rules > AI fallback > review."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import GeminiClient
from app.categories import INCOME, PERSONAL, is_valid_category
from app.config import settings
from app.db import MerchantRule
from app.rules import match_builtin
from app.schemas import NormalizedTransaction


def load_rules(session: Session) -> dict[str, str]:
    return {r.merchant_key: r.category for r in session.scalars(select(MerchantRule))}


def save_rule(session: Session, merchant_key: str, category: str) -> MerchantRule:
    merchant_key = merchant_key.strip()
    if not merchant_key:
        raise ValueError("merchant key is empty")
    if not is_valid_category(category):
        raise ValueError(f"unknown category '{category}'")
    rule = session.scalar(select(MerchantRule).where(MerchantRule.merchant_key == merchant_key))
    if rule is None:
        rule = MerchantRule(merchant_key=merchant_key, category=category)
        session.add(rule)
    else:
        rule.category = category
    session.flush()
    return rule


def categorize(
    transactions: list[NormalizedTransaction],
    saved_rules: dict[str, str],
    *,
    use_ai: bool = False,
    ai_client: GeminiClient | None = None,
    min_confidence: float | None = None,
) -> list[NormalizedTransaction]:
    """Assign category/source/confidence in place and return the list.

    Credits are labelled Income unless a saved rule says otherwise. Unknown
    merchants are batched into one AI call (unique keys only) when AI is on.
    """
    min_conf = settings.ai_min_confidence if min_confidence is None else min_confidence
    unknown: list[NormalizedTransaction] = []

    for tx in transactions:
        key = tx.normalized_merchant
        if key in saved_rules:
            tx.category, tx.category_source, tx.confidence = saved_rules[key], "rule", 1.0
            continue
        if tx.direction == "credit":
            tx.category, tx.category_source, tx.confidence = INCOME, "income", 0.8
            continue
        hit = match_builtin(key, tx.description)
        if hit:
            tx.category, tx.category_source, tx.confidence = hit[0], "builtin", hit[1]
            continue
        tx.category, tx.category_source, tx.confidence = None, "unknown", 0.0
        unknown.append(tx)

    if unknown and use_ai:
        client = ai_client or GeminiClient()
        if client.available:
            suggestions = client.suggest(sorted({t.normalized_merchant for t in unknown}))
            for tx in unknown:
                s = suggestions.get(tx.normalized_merchant)
                if s is None or s.confidence < min_conf:
                    continue  # stays in Needs review
                tx.category, tx.category_source, tx.confidence = s.category, "ai", s.confidence
    return transactions


def summarize(transactions: list[NormalizedTransaction]) -> dict[str, int]:
    return {
        "transactions": len(transactions),
        "auto_categorized": sum(1 for t in transactions if t.category_source in ("rule", "builtin", "ai", "income")),
        "needs_review": sum(1 for t in transactions if t.category is None),
        "personal": sum(1 for t in transactions if t.category == PERSONAL),
        "income": sum(1 for t in transactions if t.direction == "credit"),
        "manual": sum(1 for t in transactions if t.category_source == "manual"),
    }
