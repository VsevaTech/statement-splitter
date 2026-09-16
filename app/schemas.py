"""Pydantic models for the internal transaction representation."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["debit", "credit"]
CategorySource = Literal["rule", "builtin", "ai", "manual", "income", "unknown"]


class NormalizedTransaction(BaseModel):
    row_index: int
    date: dt.date | None
    description: str
    normalized_merchant: str
    amount: float
    currency: str = Field(min_length=3, max_length=3)
    direction: Direction
    category: str | None = None
    category_source: CategorySource = "unknown"
    confidence: float = 0.0

    @property
    def needs_review(self) -> bool:
        return self.category is None


class Summary(BaseModel):
    transactions: int
    auto_categorized: int
    needs_review: int
    personal: int
    income: int
    manual: int = 0
