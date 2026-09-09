"""
Database models for FinDoc Intelligence Agent.

This module defines the SQLAlchemy models for storing projects, documents, and
their extracted line items.
"""

from sqlalchemy import Column, String, DateTime, Date, Float, Integer, Text, JSON, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()


class Project(Base):
    """Main project entity for extraction projects."""
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(32), default="created")  # created, processing, completed, failed
    model_name = Column(String(128), nullable=True)
    embed_model_name = Column(String(128), nullable=True)
    raw_result = Column(JSON, nullable=True)
    reasoning = Column(JSON, nullable=True)
    latency_ms = Column(Float, nullable=True)
    llm_call_count = Column(Integer, nullable=True)
    requested_by = Column(String(128), default="local-analyst")
    requested_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    documents = relationship("ProjectDocument", back_populates="project", cascade="all, delete-orphan")


class Document(Base):
    """A physical uploaded file, deduped globally by content hash.

    Re-uploading byte-identical content (even under a different project or
    filename) resolves to the same row/storage folder instead of creating a
    duplicate — see `persistence.resolve_document`. Extraction results
    (`LineItemRecord`) belong to this table, not to any one project, since
    they're a function of the document's content alone.
    """
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sha256_hash = Column(String(64), nullable=False, unique=True, index=True)
    filename = Column(String(255), nullable=False)
    company_name = Column(String(255), nullable=True)  # detected issuer; known only after extraction
    period = Column(String(64), nullable=True)  # detected fiscal period, e.g. "FY2024"; known only after extraction
    document_type = Column(String(128), nullable=True)  # detected e.g. "Annual Report", "10-K"; known only after extraction
    storage_uri = Column(Text, nullable=False)
    s3_uri = Column(Text, nullable=True)  # set only when the PDF was actually mirrored to S3
    page_count = Column(Integer, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    # How many validate/correct/re-extract iterations the last run took (1-3,
    # see fin_doc_intel.extraction) — null until a run has completed.
    validation_iterations = Column(Integer, nullable=True)

    # Relationships
    line_items = relationship("LineItemRecord", back_populates="document", cascade="all, delete-orphan")


class ProjectDocument(Base):
    """Join row: which documents a project references.

    Carries no document metadata of its own — documents are shared/global
    (see `Document`), so filename/company_name/storage/hash all live there.
    """
    __tablename__ = "project_documents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)

    # Relationships
    project = relationship("Project", back_populates="documents")
    document = relationship("Document")


class LineItemRecord(Base):
    """Raw per-document line item, one row per extracted fact.

    Mirrors `fin_doc_intel.schemas.LineItem` (the Pydantic extraction schema) 1:1.
    Kept flat with `section_path` rather than a parent-child tree, matching that
    schema's design. Scoped to `document_id` only (not `project_id`) — a
    reprocessing run replaces every row for its document regardless of which
    project triggered it, since the facts are a property of the document.
    """
    __tablename__ = "line_items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    statement = Column(String(32), nullable=False)
    label = Column(Text, nullable=False)
    section_path = Column(JSON, nullable=False, default=list)
    value = Column(Float, nullable=False)
    unit = Column(String(64), nullable=True)
    currency = Column(String(16), nullable=True)
    period = Column(String(64), nullable=True)
    period_end_date = Column(Date, nullable=True)
    period_length_months = Column(Integer, nullable=True)
    scale = Column(String(16), nullable=True)  # actual, thousands, millions, billions, percentage
    consolidated = Column(Boolean, nullable=True)
    page = Column(Integer, nullable=True)
    source_snippet = Column(Text, nullable=True)
    # List of fin_doc_intel.fundamentals_pipeline.validators.ValidationFlag dicts
    # ({check, severity, message}) still open after the last extraction attempt.
    validation_errors = Column(JSON, nullable=False, default=list)

    # Relationships
    document = relationship("Document", back_populates="line_items")
