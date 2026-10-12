"""Persisted shape of one extracted financial fact.

Each filing yields many rows; every row stands alone. The headings that
surrounded the number in the document are kept as an ordered breadcrumb trail
instead of a tree, and the source page travels with the value for audit.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .fundamentals_pipeline.validators import ValidationFlag


class StatementType(str, Enum):  # noqa: UP042
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW = "cash_flow"
    CHANGES_IN_EQUITY = "changes_in_equity"
    OTHER = "other"


class LineItem(BaseModel):
    statement: StatementType
    label: str = Field(..., description="Wording exactly as printed in the filing.")
    section_path: list[str] = Field(
        default_factory=list,
        description="Headings above the row, outermost first.",
    )
    value: float
    unit: str | None = None  # e.g. 'INR mn', 'USD k'
    currency: str | None = None
    period: str | None = None  # e.g. 'FY2024', '31-Mar-2025'
    period_end_date: date | None = None
    period_length_months: int | None = None
    scale: Literal["actual", "thousands", "millions", "billions", "percentage"] | None = None
    consolidated: bool | None = None
    page: int | None = None
    source_snippet: str | None = None
    validation_errors: list[ValidationFlag] = Field(
        default_factory=list,
        description="Checks still failing after the last retry — empty when the "
        "row was clean or every finding was cleared inside the retry budget.",
    )
