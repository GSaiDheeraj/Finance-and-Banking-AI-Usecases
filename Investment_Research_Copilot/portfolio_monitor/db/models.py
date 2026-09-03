"""
SQLAlchemy models for the portfolio-monitor Postgres store.

Normalized tables for queryable fields (holdings, alerts, rebalancing trades) plus
one `raw_result` jsonb column on `PortfolioRun` for the full MonitoringResult snapshot.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _new_id() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class PortfolioRun(Base):
    """One monitoring run — the audit anchor every other table hangs off of."""

    __tablename__ = "portfolio_runs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    parent_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("portfolio_runs.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="created")
    # 'monitoring' (default, existing behavior) | 'construction' — a from-scratch build whose
    # raw_result is a ConstructionResult dump instead of a MonitoringResult one. Same table,
    # same lifecycle; only the shape of raw_result and which job function filled it differ.
    result_type: Mapped[str] = mapped_column(String(20), default="monitoring")

    portfolio_name: Mapped[str] = mapped_column(String(255))
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mandate: Mapped[str] = mapped_column(String(64), default="balanced")
    base_currency: Mapped[str] = mapped_column(String(16), default="USD")
    mode: Mapped[str] = mapped_column(String(32), default="both")
    enable_news: Mapped[bool] = mapped_column(default=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    construction_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    health_score: Mapped[float | None] = mapped_column(nullable=True)
    health_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    llm_call_count: Mapped[int | None] = mapped_column(nullable=True)

    raw_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reasoning: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    requested_by: Mapped[str] = mapped_column(String(128), default="local-advisor")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    documents: Mapped[list["PortfolioDocument"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    holdings: Mapped[list["HoldingRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["AlertRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    rebalancing_trades: Mapped[list["RebalancingTradeRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    briefing: Mapped["BriefingRow | None"] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan"
    )
    reviews: Mapped[list["ReviewRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class PortfolioDocument(Base):
    __tablename__ = "portfolio_documents"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    portfolio_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("portfolio_runs.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255))
    storage_uri: Mapped[str] = mapped_column(Text)
    sha256_hash: Mapped[str] = mapped_column(String(64))
    doc_type: Mapped[str] = mapped_column(String(32), default="holdings")

    run: Mapped["PortfolioRun"] = relationship(back_populates="documents")


class HoldingRow(Base):
    __tablename__ = "holdings"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    portfolio_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("portfolio_runs.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    asset_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quantity: Mapped[float] = mapped_column(Float)
    market_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    run: Mapped["PortfolioRun"] = relationship(back_populates="holdings")


class AlertRow(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    portfolio_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("portfolio_runs.id"), nullable=False
    )
    alert_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["PortfolioRun"] = relationship(back_populates="alerts")


class RebalancingTradeRow(Base):
    __tablename__ = "rebalancing_trades"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    portfolio_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("portfolio_runs.id"), nullable=False
    )
    bucket: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(16))
    trade_value: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["PortfolioRun"] = relationship(back_populates="rebalancing_trades")


class BriefingRow(Base):
    __tablename__ = "briefings"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    portfolio_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("portfolio_runs.id"), unique=True, nullable=False
    )
    method: Mapped[str] = mapped_column(String(32), default="deterministic")
    executive_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_findings: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    run: Mapped["PortfolioRun"] = relationship(back_populates="briefing")

    __table_args__ = (UniqueConstraint("portfolio_run_id", name="uq_briefing_portfolio_run"),)


class ReviewRow(Base):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    portfolio_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("portfolio_runs.id"), nullable=False
    )
    reviewer: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(16))
    corrected_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    run: Mapped["PortfolioRun"] = relationship(back_populates="reviews")


def init_schema() -> None:
    from .session import get_engine

    Base.metadata.create_all(get_engine())
