"""
Database models for Fraud Detection Agent.

This module defines the SQLAlchemy models for storing onboarding cases, parties,
ownership graphs, screening results, and EDD measures.
"""

from sqlalchemy import Column, String, DateTime, Float, Integer, Text, JSON, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()


class Case(Base):
    """Main case entity for onboarding assessments."""
    __tablename__ = "cases"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    client_name = Column(String(255), nullable=False, index=True)  # Added index for faster lookups
    case_type = Column(String(32), nullable=False)  # documents, name_search
    description = Column(Text, nullable=True)
    status = Column(String(32), default="created")  # created, processing, awaiting_review, approved, rejected, prohibited
    model_name = Column(String(128), nullable=True)
    embed_model_name = Column(String(128), nullable=True)
    risk_score = Column(Float, nullable=True)
    risk_band = Column(String(32), nullable=True)  # LOW, MEDIUM, HIGH, PROHIBITED
    decision = Column(String(64), nullable=True)  # APPROVE_STANDARD_CDD, APPROVE_WITH_EDD, ESCALATE_MLRO, DECLINE
    hard_stop_reason = Column(Text, nullable=True)
    raw_result = Column(JSON, nullable=True)
    reasoning = Column(JSON, nullable=True)
    latency_ms = Column(Float, nullable=True)
    llm_call_count = Column(Integer, nullable=True)
    requested_by = Column(String(128), default="local-analyst")
    requested_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    # Fields for handling historical analysis
    analysis_version = Column(Integer, default=1)  # Version number for same client
    storage_path = Column(String(512), nullable=True)  # Path to stored analysis files
    is_latest = Column(Boolean, default=True)  # Flag to identify latest analysis for client
    
    # Relationships
    documents = relationship("CaseDocument", back_populates="case", cascade="all, delete-orphan")
    parties = relationship("Party", back_populates="case", cascade="all, delete-orphan")
    ownership_edges = relationship("OwnershipEdge", back_populates="case", cascade="all, delete-orphan")
    watchlist_hits = relationship("WatchlistHit", back_populates="case", cascade="all, delete-orphan")
    ubos = relationship("UBO", back_populates="case", cascade="all, delete-orphan")
    edd_measures = relationship("EDDMeasure", back_populates="case", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="case", cascade="all, delete-orphan")


class CaseDocument(Base):
    """Document entity for uploaded KYC PDFs."""
    __tablename__ = "case_documents"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    storage_uri = Column(Text, nullable=False)
    sha256_hash = Column(String(64), nullable=False)
    page_count = Column(Integer, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    case = relationship("Case", back_populates="documents")
    page_chunks = relationship("PageChunk", back_populates="document", cascade="all, delete-orphan")


class PageChunk(Base):
    """Page chunk entity for semantic indexing."""
    __tablename__ = "page_chunks"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    document_id = Column(String, ForeignKey("case_documents.id"), nullable=True)
    page_number = Column(Integer, nullable=False)
    doc_name = Column(String(255), nullable=False)
    text = Column(Text, nullable=False)
    embedding = Column(JSON, nullable=True)  # Store as JSON for pgvector compatibility
    
    # Relationships
    case = relationship("Case")
    document = relationship("CaseDocument", back_populates="page_chunks")


class Party(Base):
    """Party entity for individuals and legal entities."""
    __tablename__ = "parties"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    party_id = Column(String(32), nullable=False)  # P1, P2, etc.
    name = Column(String(255), nullable=False)
    party_type = Column(String(64), nullable=False)  # individual, trust, foundation, holding_company, etc.
    country = Column(String(128), nullable=True)
    declared_pep = Column(Boolean, default=False)
    source_document = Column(String(255), nullable=True)
    page = Column(Integer, nullable=True)
    
    # Relationships
    case = relationship("Case", back_populates="parties")
    ownership_edges_as_owner = relationship("OwnershipEdge", foreign_keys="OwnershipEdge.owner_id", back_populates="owner")
    ownership_edges_as_owned = relationship("OwnershipEdge", foreign_keys="OwnershipEdge.owned_id", back_populates="owned")


class OwnershipEdge(Base):
    """Ownership edge entity for ownership relationships."""
    __tablename__ = "ownership_edges"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    owner_id = Column(String, ForeignKey("parties.id"), nullable=False)
    owned_id = Column(String, ForeignKey("parties.id"), nullable=False)
    ownership_percentage = Column(Float, nullable=True)
    ownership_type = Column(String(32), nullable=True)  # direct, indirect, beneficial
    source_document = Column(String(255), nullable=True)
    page = Column(Integer, nullable=True)
    
    # Relationships
    case = relationship("Case", back_populates="ownership_edges")
    owner = relationship("Party", foreign_keys=[owner_id], back_populates="ownership_edges_as_owner")
    owned = relationship("Party", foreign_keys=[owned_id], back_populates="ownership_edges_as_owned")


class WatchlistHit(Base):
    """Watchlist hit entity for PEP/sanctions/adverse media matches."""
    __tablename__ = "watchlist_hits"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    party_id = Column(String, nullable=False)
    party_name = Column(String(255), nullable=False)
    list_type = Column(String(32), nullable=False)  # pep, sanctions, adverse_media
    matched_name = Column(String(255), nullable=False)
    match_score = Column(Float, nullable=True)
    tier = Column(String(32), nullable=True)  # low, medium, high
    details = Column(Text, nullable=True)
    source_list = Column(String(255), nullable=True)
    
    # Relationships
    case = relationship("Case", back_populates="watchlist_hits")


class UBO(Base):
    """UBO (Ultimate Beneficial Owner) entity."""
    __tablename__ = "ubos"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    party_id = Column(String, nullable=False)
    party_name = Column(String(255), nullable=False)
    effective_ownership_percentage = Column(Float, nullable=True)
    basis = Column(String(32), nullable=False)  # ownership, control
    control_roles = Column(JSON, nullable=True)  # List of control roles
    ownership_paths = Column(JSON, nullable=True)  # List of ownership paths
    
    # Relationships
    case = relationship("Case", back_populates="ubos")


class EDDMeasure(Base):
    """EDD (Enhanced Due Diligence) measure entity."""
    __tablename__ = "edd_measures"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    measure = Column(Text, nullable=False)
    category = Column(String(64), nullable=True)  # monitoring, documentation, approval, etc.
    severity = Column(String(32), nullable=True)  # low, medium, high, critical
    
    # Relationships
    case = relationship("Case", back_populates="edd_measures")


class Review(Base):
    """Review entity for compliance feedback."""
    __tablename__ = "reviews"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    reviewer = Column(String(128), nullable=False)
    action = Column(String(16), nullable=False)  # approved, corrected, rejected
    corrected_fields = Column(JSON, nullable=True)
    reason = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    case = relationship("Case", back_populates="reviews")
