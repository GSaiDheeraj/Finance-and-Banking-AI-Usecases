"""Data contracts for the table-based extraction pipeline.

Ported from FinDoc Pypi's `fundamentals_module/models.py`. Each `Cell` carries a
free-form `coordinates` dict so the same shape covers primary statements (period
only), maturity ladders (period + maturity bucket), and consolidated/parent-only
views (period + consolidation) without a different model per table shape.

This `LineItem` is intentionally distinct from `fin_doc_intel.schemas.LineItem` —
it's the pipeline's internal, cell-based representation; `extraction.py` flattens
it into one `schemas.LineItem` row per cell at the boundary.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Scale = Literal["actual", "thousands", "millions", "billions", "percentage"]
StatementType = Literal["balance_sheet", "income_statement", "cash_flow", "changes_in_equity", "other"]


class Cell(BaseModel):
    """A single numeric fact pinned to a point in N-dimensional space.

    coordinates always includes `period_end` (ISO date) and `period_type`
    ("point" for balance-sheet items; "3M"/"6M"/"9M"/"12M" for income-statement
    and cash-flow flows). Additional axes (`segment_id`, `maturity_bucket`,
    `consolidation`) appear only when the source table has more than one axis.
    """
    coordinates: dict[str, str]
    value: float
    currency: Optional[str] = None
    scale: Optional[Scale] = None
    raw_text: Optional[str] = Field(
        default=None,
        description="The literal token as printed in the source table (e.g. '(1,234)'), "
                    "before sign/scale normalization — lets validation check the sign "
                    "conversion without re-parsing the table HTML.",
    )


class LineItem(BaseModel):
    """A row of a financial statement — one or more Cells, one per period/axis point."""
    label: str
    section_path: list[str] = Field(default_factory=list)
    values: list[Cell] = Field(default_factory=list)


class RelevanceVerdict(BaseModel):
    """Output schema for the relevance-filter node."""
    relevant: bool
    reason: str


class StatementClassification(BaseModel):
    """Output schema for the statement-classifier node."""
    statement_type: StatementType
    is_breakdown: bool = False
    breakdown_of: Optional[str] = None


class ConsolidationMeta(BaseModel):
    """Output schema for the consolidation-classifier node."""
    consolidated_columns: list[int] = Field(default_factory=list)
    unconsolidated_columns: list[int] = Field(default_factory=list)
    segment_id: Optional[str] = None
    skip: bool = False
    reason: str = ""


class ExtractedLineItems(BaseModel):
    """Output schema for the line-item extraction node."""
    line_items: list[LineItem] = Field(default_factory=list)
