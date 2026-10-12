"""Relational records behind filing projects and their extracted facts."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

_NAME_COL = 255
_CODE_COL = 32
_MODEL_COL = 128
_DIGEST_COL = 64
_TERM_COL = 64
_FILING_KIND_COL = 128
_MEASURE_COL = 64
_MONEY_COL = 16
_MAGNITUDE_COL = 16


def _fresh_id() -> str:
    return str(uuid.uuid4())


def _now_utc() -> datetime:
    return datetime.now(UTC)


class Project(Base):
    """One user upload batch and the background run working through it."""

    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=_fresh_id)
    project_name = Column(String(_NAME_COL), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(_CODE_COL), default="created")
    model_name = Column(String(_MODEL_COL), nullable=True)
    embed_model_name = Column(String(_MODEL_COL), nullable=True)
    raw_result = Column(JSON, nullable=True)
    reasoning = Column(JSON, nullable=True)
    latency_ms = Column(Float, nullable=True)
    llm_call_count = Column(Integer, nullable=True)
    requested_by = Column(String(_MODEL_COL), default="local-analyst")
    requested_at = Column(DateTime, default=_now_utc)
    completed_at = Column(DateTime, nullable=True)

    documents = relationship(
        "ProjectDocument", back_populates="project", cascade="all, delete-orphan"
    )


class Document(Base):
    """A single uploaded file, shared globally when its bytes repeat."""

    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=_fresh_id)
    sha256_hash = Column(String(_DIGEST_COL), nullable=False, unique=True, index=True)
    filename = Column(String(_NAME_COL), nullable=False)
    company_name = Column(String(_NAME_COL), nullable=True)
    period = Column(String(_TERM_COL), nullable=True)
    document_type = Column(String(_FILING_KIND_COL), nullable=True)
    storage_uri = Column(Text, nullable=False)
    s3_uri = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=False)
    uploaded_at = Column(DateTime, default=_now_utc)
    validation_iterations = Column(Integer, nullable=True)

    line_items = relationship(
        "LineItemRecord", back_populates="document", cascade="all, delete-orphan"
    )


class ProjectDocument(Base):
    """Membership row tying documents to the project that uploaded them."""

    __tablename__ = "project_documents"

    id = Column(String, primary_key=True, default=_fresh_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)

    project = relationship("Project", back_populates="documents")
    document = relationship("Document")


class LineItemRecord(Base):
    """One extracted number stored beside the filing it was read from."""

    __tablename__ = "line_items"

    id = Column(String, primary_key=True, default=_fresh_id)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    statement = Column(String(_CODE_COL), nullable=False)
    label = Column(Text, nullable=False)
    section_path = Column(JSON, nullable=False, default=list)
    value = Column(Float, nullable=False)
    unit = Column(String(_MEASURE_COL), nullable=True)
    currency = Column(String(_MONEY_COL), nullable=True)
    period = Column(String(_TERM_COL), nullable=True)
    period_end_date = Column(Date, nullable=True)
    period_length_months = Column(Integer, nullable=True)
    scale = Column(String(_MAGNITUDE_COL), nullable=True)
    consolidated = Column(Boolean, nullable=True)
    page = Column(Integer, nullable=True)
    source_snippet = Column(Text, nullable=True)
    validation_errors = Column(JSON, nullable=False, default=list)

    document = relationship("Document", back_populates="line_items")
