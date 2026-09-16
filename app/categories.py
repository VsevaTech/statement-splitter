"""The closed list of categories the app (and the AI fallback) may use."""

from __future__ import annotations

SOFTWARE = "Software / SaaS"
CLOUD = "Cloud / Hosting"
ACCOUNTING = "Accounting"
COMMUNICATION = "Communication"
TRANSPORT = "Transport"
TRAVEL = "Travel"
OFFICE = "Office"
FOOD = "Food"
BANK_FEES = "Bank fees"
TAXES = "Taxes"
PROFESSIONAL = "Professional services"
PERSONAL = "Personal"
OTHER = "Other"
INCOME = "Income"

# Categories an expense (debit) may be assigned to. This is the only list the AI
# provider is allowed to pick from.
EXPENSE_CATEGORIES: tuple[str, ...] = (
    SOFTWARE,
    CLOUD,
    ACCOUNTING,
    COMMUNICATION,
    TRANSPORT,
    TRAVEL,
    OFFICE,
    FOOD,
    BANK_FEES,
    TAXES,
    PROFESSIONAL,
    PERSONAL,
    OTHER,
)

# Everything a user may select in the review UI.
ALL_CATEGORIES: tuple[str, ...] = EXPENSE_CATEGORIES + (INCOME,)


def is_valid_category(value: str | None) -> bool:
    return value in ALL_CATEGORIES
