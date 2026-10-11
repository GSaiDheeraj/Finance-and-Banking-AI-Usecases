"""HTTP request/response DTOs for the portfolio monitoring API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PortfolioSummary(BaseModel):
    portfolio_id: str
    parent_run_id: str | None = None
    status: str
    result_type: str = "monitoring"
    portfolio_name: str
    client_name: str | None = None
    mandate: str
    health_score: float | None = None
    health_band: str | None = None
    requested_at: datetime
    completed_at: datetime | None = None


class PortfolioResponse(PortfolioSummary):
    base_currency: str = "USD"
    mode: str = "both"
    enable_news: bool = True
    question: str | None = None
    answer: str | None = None
    instruction: str | None = None
    construction_params: dict[str, Any] | None = None
    latency_ms: float | None = None
    llm_call_count: int | None = None
    model_name: str | None = None
    result: dict[str, Any] | None = None
    reasoning: list[str] = Field(default_factory=list)
    requested_by: str
    documents: list[str] = Field(default_factory=list)


class RerunRequest(BaseModel):
    instruction: str | None = None
    question: str | None = None
    mandate: str | None = None
    enable_news: bool | None = None


class StatusResponse(BaseModel):
    portfolio_id: str
    status: str
    phase: str | None = None
    phase_detail: str | None = None


class QuestionRequest(BaseModel):
    question: str


class QuestionResponse(BaseModel):
    answer: str


class ReviewRequest(BaseModel):
    reviewer: str
    action: Literal["approved", "corrected", "rejected"]
    corrected_fields: dict[str, Any] | None = None
    reason: str | None = None


class ReviewResponse(BaseModel):
    id: str
    reviewer: str
    action: str
    reason: str | None = None
    reviewed_at: datetime


class AuditTraceResponse(BaseModel):
    portfolio_id: str
    parent_run_id: str | None = None
    status: str
    model_name: str | None = None
    requested_by: str
    requested_at: datetime
    completed_at: datetime | None = None
    documents: list[dict[str, Any]] = Field(default_factory=list)
    holdings: list[dict[str, Any]] = Field(default_factory=list)
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    reviews: list[ReviewResponse] = Field(default_factory=list)
