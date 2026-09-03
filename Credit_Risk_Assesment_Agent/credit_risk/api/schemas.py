"""
API request/response DTOs.

Kept separate from `credit_risk/schemas.py`'s domain models: those model the credit
assessment itself and are shared with the deterministic engine; these model the HTTP
contract and can evolve independently (e.g. adding a field here shouldn't touch scoring
logic, and vice versa).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class AssessmentSummary(BaseModel):
    """One row in a list of assessments — the analyst workspace's case list."""
    version_id: str
    parent_version_id: Optional[str] = None
    status: str
    company_name: Optional[str] = None
    ticker: Optional[str] = None
    grade: Optional[str] = None
    band: Optional[str] = None
    decision: Optional[str] = None
    requested_at: datetime
    completed_at: Optional[datetime] = None


class AssessmentResponse(AssessmentSummary):
    """Full detail for one assessment version."""
    question: Optional[str] = None
    answer: Optional[str] = None
    instruction: Optional[str] = None
    probability_of_default: Optional[float] = None
    loss_given_default: Optional[float] = None
    expected_loss_pct: Optional[float] = None
    hard_stop_reason: Optional[str] = None
    latency_ms: Optional[float] = None
    llm_call_count: Optional[int] = None
    model_name: Optional[str] = None
    embed_model_name: Optional[str] = None
    # The full CreditAssessment.model_dump() snapshot — ratios, benchmark, trend, memo,
    # web sentiment, reconciliation. None while status is still "processing".
    assessment: Optional[dict[str, Any]] = None
    # Human-readable step trace (extraction summary, yfinance/web-sentiment notes,
    # scorecard headline) — the same list `run_credit_assessment` always built, now
    # persisted so a later GET reproduces exactly what the original POST returned.
    reasoning: list[str] = Field(default_factory=list)
    # The original request inputs — restored by a client reopening a past case so its
    # form reflects what was actually submitted, not just the result.
    requested_by: str
    enable_web_sentiment: bool
    search_region: Optional[str] = None
    documents: list[str] = Field(default_factory=list, description="Uploaded filenames, for display only.")


class RerunRequest(BaseModel):
    """Rerun the parent version's documents with a correction and/or a new question."""
    instruction: Optional[str] = Field(None, description="What changed and why, for the audit trail.")
    question: Optional[str] = None
    search_region: Optional[str] = None
    enable_web_sentiment: Optional[bool] = None


class StatusResponse(BaseModel):
    """Cheap poll target while a background job is running — see `status_tracker.py`.

    `phase` is the live, fine-grained progress Redis holds while the job runs
    (indexing_documents/extracting_financials/enriching_and_scoring/writing_memo/done/
    failed); `status` is always the durable Postgres value. `phase` is None once Redis's
    entry has expired or was never available — `status` is the fallback in that case.
    """
    version_id: str
    status: str
    phase: Optional[str] = None
    phase_detail: Optional[str] = None


class QuestionRequest(BaseModel):
    question: str


class QuestionResponse(BaseModel):
    answer: str


class ReviewRequest(BaseModel):
    reviewer: str
    action: str = Field(..., description="One of: approved | corrected | rejected")
    corrected_fields: Optional[dict[str, Any]] = None
    reason: Optional[str] = None


class ReviewResponse(BaseModel):
    id: str
    reviewer: str
    action: str
    reason: Optional[str] = None
    reviewed_at: datetime


class DocumentAudit(BaseModel):
    filename: str
    sha256_hash: str
    page_count: int


class LineItemAudit(BaseModel):
    label: str
    standardised_label: str
    value: Optional[float] = None
    period: str
    page: Optional[int] = None


class RatingFactorAudit(BaseModel):
    factor_type: str
    present: bool
    severity: str
    weight: float
    contribution: float
    evidence: list[str] = Field(default_factory=list)


class AuditTraceResponse(BaseModel):
    """Full reasoning/evidence trace for one assessment (CASE_STUDY.md's audit tab)."""
    version_id: str
    parent_version_id: Optional[str] = None
    status: str
    model_name: Optional[str] = None
    embed_model_name: Optional[str] = None
    requested_by: str
    requested_at: datetime
    completed_at: Optional[datetime] = None
    documents: list[DocumentAudit]
    line_items: list[LineItemAudit]
    rating_factors: list[RatingFactorAudit]
    reviews: list[ReviewResponse]
