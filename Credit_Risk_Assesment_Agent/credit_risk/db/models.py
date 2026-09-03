"""
SQLAlchemy models for the credit-risk Postgres store.

Scope (the "pragmatic core" from the approved plan): normalized, queryable tables for
the fields that benefit from being queried/joined directly — line items, ratios, rating
factors, the memo, reviews — plus one `raw_result` jsonb column on `AssessmentVersion`
holding the full `CreditAssessment.model_dump()` snapshot (notes, benchmark, trend, web
sentiment, reconciliation). That snapshot is the "pragmatic middle path"
`DOCUMENTATION.md`'s own HLD already recommends, rather than a normalized table for
every nested field before any of them has a real query/reporting need.

Enum-valued fields (grade, band, severity, ...) are stored as plain strings, matching
how `credit_risk/schemas.py`'s own `str` Enums already serialize (`.value`) everywhere
else in this codebase — introducing a second, Postgres-native enum type here would just
be a second representation of the same fixed vocabulary to keep in sync.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def embedding_dim() -> int:
    """Must match whatever `EMBED_MODEL_NAME` (config.py) actually resolves to."""
    try:
        return int(os.getenv("EMBED_DIM", "1536"))
    except (TypeError, ValueError):
        return 1536


def _new_id() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AssessmentVersion(Base):
    """One assessment run — the audit anchor every other table hangs off of.

    A rerun creates a new row with `parent_version_id` set; the original is never
    mutated (CASE_STUDY.md §7.1's "version, don't overwrite" rule).
    """
    __tablename__ = "assessment_versions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    parent_version_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("assessment_versions.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="created")

    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ticker: Mapped[str | None] = mapped_column(String(16), nullable=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The two web-sentiment request inputs — not derivable after the fact from whether
    # `web_sentiment` ended up populated (a blank company name or a search failure would
    # leave it empty even when this was requested), so they're stored as the caller's
    # actual inputs, not inferred from the outcome.
    enable_web_sentiment: Mapped[bool] = mapped_column(default=True)
    search_region: Mapped[str | None] = mapped_column(String(16), nullable=True)

    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embed_model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    grade: Mapped[str | None] = mapped_column(String(8), nullable=True)
    band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    probability_of_default: Mapped[float | None] = mapped_column(nullable=True)
    loss_given_default: Mapped[float | None] = mapped_column(nullable=True)
    expected_loss_pct: Mapped[float | None] = mapped_column(nullable=True)
    hard_stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    llm_call_count: Mapped[int | None] = mapped_column(nullable=True)

    raw_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # The human-readable step trace `run_credit_assessment` builds (extraction summary,
    # yfinance/web-sentiment notes, scorecard headline) — persisted so a later GET can
    # reproduce exactly what the original POST returned, not just the raw scorecard.
    reasoning: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    requested_by: Mapped[str] = mapped_column(String(128), default="local-analyst")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    documents: Mapped[list["AssessmentDocument"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    pages: Mapped[list["PageChunk"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    line_items: Mapped[list["LineItemRow"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    ratios: Mapped[list["RatioRow"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    rating_factors: Mapped[list["RatingFactorRow"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    memo: Mapped["CreditMemoRow | None"] = relationship(
        back_populates="version", uselist=False, cascade="all, delete-orphan"
    )
    reviews: Mapped[list["ReviewRow"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class AssessmentDocument(Base):
    """One uploaded filing, persisted for real (not a leaked temp file)."""
    __tablename__ = "assessment_documents"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    assessment_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("assessment_versions.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255))
    storage_uri: Mapped[str] = mapped_column(Text)
    sha256_hash: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int] = mapped_column(default=0)

    version: Mapped["AssessmentVersion"] = relationship(back_populates="documents")


class PageChunk(Base):
    """One page's text + embedding, scoped to one assessment version.

    Backs `db.page_index.PgPageIndex` — the pgvector-based replacement for
    `doc_index.PageIndex`'s in-memory numpy index.
    """
    __tablename__ = "page_chunks"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    assessment_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("assessment_versions.id"), nullable=False
    )
    document_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("assessment_documents.id"), nullable=True
    )
    page_number: Mapped[int] = mapped_column()
    doc_name: Mapped[str] = mapped_column(String(255), default="")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(embedding_dim()))

    version: Mapped["AssessmentVersion"] = relationship(back_populates="pages")


class LineItemRow(Base):
    """One `schemas.LineItem`, persisted with its assessment version."""
    __tablename__ = "line_items"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    assessment_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("assessment_versions.id"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(255))
    standardised_label: Mapped[str] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    unit: Mapped[str] = mapped_column(String(16), default="absolute")
    period: Mapped[str] = mapped_column(String(32))
    period_type: Mapped[str] = mapped_column(String(16), default="annual")
    period_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_length_months: Mapped[int | None] = mapped_column(nullable=True)
    statement_type: Mapped[str] = mapped_column(String(32), default="other")
    page: Mapped[int | None] = mapped_column(nullable=True)
    source_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)

    version: Mapped["AssessmentVersion"] = relationship(back_populates="line_items")


class RatioRow(Base):
    """One `schemas.RatioValue`, persisted with its assessment version."""
    __tablename__ = "ratios"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    assessment_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("assessment_versions.id"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(32))
    ratio_name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(nullable=True)
    category: Mapped[str] = mapped_column(String(32), default="")
    inputs_used: Mapped[list] = mapped_column(JSONB, default=list)
    missing_inputs: Mapped[list] = mapped_column(JSONB, default=list)
    pages: Mapped[list] = mapped_column(JSONB, default=list)

    version: Mapped["AssessmentVersion"] = relationship(back_populates="ratios")


class RatingFactorRow(Base):
    """One `schemas.RatingFactorResult`, persisted with its assessment version."""
    __tablename__ = "rating_factors"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    assessment_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("assessment_versions.id"), nullable=False
    )
    factor_type: Mapped[str] = mapped_column(String(64))
    present: Mapped[bool] = mapped_column(default=False)
    severity: Mapped[str] = mapped_column(String(16), default="low")
    weight: Mapped[float] = mapped_column(default=0.0)
    contribution: Mapped[float] = mapped_column(default=0.0)
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    pages: Mapped[list] = mapped_column(JSONB, default=list)

    version: Mapped["AssessmentVersion"] = relationship(back_populates="rating_factors")


class CreditMemoRow(Base):
    """One `schemas.CreditMemo`, 1:1 with its assessment version."""
    __tablename__ = "credit_memos"
    __table_args__ = (UniqueConstraint("assessment_version_id", name="uq_credit_memo_version"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    assessment_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("assessment_versions.id"), nullable=False
    )
    executive_summary: Mapped[str] = mapped_column(Text, default="")
    financial_analysis: Mapped[str] = mapped_column(Text, default="")
    key_strengths: Mapped[list] = mapped_column(JSONB, default=list)
    key_risks: Mapped[list] = mapped_column(JSONB, default=list)
    mitigants: Mapped[list] = mapped_column(JSONB, default=list)
    recommended_covenants: Mapped[list] = mapped_column(JSONB, default=list)
    monitoring_triggers: Mapped[list] = mapped_column(JSONB, default=list)

    version: Mapped["AssessmentVersion"] = relationship(back_populates="memo")


class ReviewRow(Base):
    """A credit-analyst/committee review action (FR-8.2) — approve, correct, or reject."""
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_new_id)
    assessment_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("assessment_versions.id"), nullable=False
    )
    reviewer: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(16))  # approved | corrected | rejected
    corrected_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    version: Mapped["AssessmentVersion"] = relationship(back_populates="reviews")


def init_schema() -> None:
    """Create the `vector` extension and every table, idempotently. No Alembic yet —
    there is exactly one local Postgres instance and nothing to migrate between."""
    from sqlalchemy import text

    from .session import get_engine

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
