"""Internal shapes for the staged table readers.

A `Cell` pins one number to a flexible address map so the same model covers
plain period tables, maturity ladders, and consolidated-vs-standalone views
without a separate class per grid shape. This `LineItem` is the pipeline's
working draft; `extraction.py` expands it into one stored row per cell at the
edge. It is deliberately separate from `fin_doc_intel.schemas.LineItem`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Scale = Literal["actual", "thousands", "millions", "billions", "percentage"]
StatementType = Literal[
    "balance_sheet", "income_statement", "cash_flow", "changes_in_equity", "other"
]


class Cell(BaseModel):
    """One number plus the address of where it sat in the table.

    `coordinates` always carries `period_end` (ISO day) and `period_type`
    (`point` for balance-sheet snapshots; `3M`/`6M`/`9M`/`12M` for flows).
    Further axes (`segment_id`, `maturity_bucket`, `consolidation`) appear
    only when the source grid actually has them.
    """

    coordinates: dict[str, str]
    value: float
    currency: str | None = None
    scale: Scale | None = None
    raw_text: str | None = Field(
        default=None,
        description="Token exactly as printed (e.g. '(1,234)') before sign/scale "
        "cleanup — lets checks audit the conversion without re-reading HTML.",
    )


class LineItem(BaseModel):
    """One grid row: a label with a cell per period or axis point."""

    label: str
    section_path: list[str] = Field(default_factory=list)
    values: list[Cell] = Field(default_factory=list)


class RelevanceVerdict(BaseModel):
    """Answer shape for the keep-or-drop pass."""

    relevant: bool
    reason: str


class StatementClassification(BaseModel):
    """Answer shape for the statement-labelling pass."""

    statement_type: StatementType
    is_breakdown: bool = False
    breakdown_of: str | None = None


class ConsolidationMeta(BaseModel):
    """Answer shape for the column-scope pass."""

    consolidated_columns: list[int] = Field(default_factory=list)
    unconsolidated_columns: list[int] = Field(default_factory=list)
    segment_id: str | None = None
    skip: bool = False
    reason: str = ""


class ExtractedLineItems(BaseModel):
    """Answer shape for the careful reading pass."""

    line_items: list[LineItem] = Field(default_factory=list)
