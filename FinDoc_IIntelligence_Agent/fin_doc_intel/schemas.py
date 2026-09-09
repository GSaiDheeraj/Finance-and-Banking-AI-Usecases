"""
Structured line-item schema for financial document extraction.

`LineItem` is flat — enclosing headers/sub-totals are captured in `section_path`
(a list of strings, outermost first) rather than a parent-child tree. Every fact
keeps its source `page` for traceability.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .fundamentals_pipeline.validators import ValidationFlag


class StatementType(str, Enum):
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW = "cash_flow"
    CHANGES_IN_EQUITY = "changes_in_equity"
    OTHER = "other"


class LineItem(BaseModel):
    statement: StatementType
    label: str = Field(..., description="Verbatim label as printed in the document.")
    section_path: List[str] = Field(
        default_factory=list,
        description="Enclosing headers/sub-totals, outermost first.",
    )
    value: float
    unit: Optional[str] = None           # e.g. 'INR mn', 'USD k'
    currency: Optional[str] = None
    period: Optional[str] = None         # e.g. 'FY2024', '31-Mar-2025'
    period_end_date: Optional[date] = None
    period_length_months: Optional[int] = None
    scale: Optional[Literal["actual", "thousands", "millions", "billions", "percentage"]] = None
    consolidated: Optional[bool] = None
    page: Optional[int] = None
    source_snippet: Optional[str] = None
    validation_errors: List[ValidationFlag] = Field(
        default_factory=list,
        description="Issues still open after the last extraction attempt (see "
                    "fundamentals_pipeline.validators) — empty when the row was clean "
                    "or every issue was resolved within the retry budget.",
    )
