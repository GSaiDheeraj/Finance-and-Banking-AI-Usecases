"""HTTP request/response DTOs for the portfolio monitoring API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class PortfolioSummary(BaseModel):
    portfolio_id: str
    parent_run_id: Optional[str] = None
    status: str
    result_type: str = "monitoring"
    portfolio_name: str
    client_name: Optional[str] = None
    mandate: str
    health_score: Optional[float] = None
    health_band: Optional[str] = None
    requested_at: datetime
    completed_at: Optional[datetime] = None


class PortfolioResponse(PortfolioSummary):
    base_currency: str = "USD"
    mode: str = "both"
    enable_news: bool = True
    question: Optional[str] = None
    answer: Optional[str] = None
    instruction: Optional[str] = None
    construction_params: Optional[dict[str, Any]] = None
    latency_ms: Optional[float] = None
    llm_call_count: Optional[int] = None
    model_name: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    reasoning: list[str] = Field(default_factory=list)
    requested_by: str
    documents: list[str] = Field(default_factory=list)


class RerunRequest(BaseModel):
    instruction: Optional[str] = None
    question: Optional[str] = None
    mandate: Optional[str] = None
    enable_news: Optional[bool] = None


class StatusResponse(BaseModel):
    portfolio_id: str
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


class AuditTraceResponse(BaseModel):
    portfolio_id: str
    parent_run_id: Optional[str] = None
    status: str
    model_name: Optional[str] = None
    requested_by: str
    requested_at: datetime
    completed_at: Optional[datetime] = None
    documents: list[dict[str, Any]] = Field(default_factory=list)
    holdings: list[dict[str, Any]] = Field(default_factory=list)
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    reviews: list[ReviewResponse] = Field(default_factory=list)
