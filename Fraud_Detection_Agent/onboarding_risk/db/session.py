"""
Database session management for Fraud Detection Agent.

This module provides database connection and session management using SQLAlchemy.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from onboarding_risk.db.models import Base
import os

# Database URL from environment variable or default
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{os.getenv('PGUSER', 'postgres')}:{os.getenv('PGPASSWORD', '7194')}@"
    f"{os.getenv('PGHOST', 'localhost')}:{os.getenv('PGPORT', '5433')}/{os.getenv('PGDATABASE', 'fraud_detection_db')}"
)

# Create engine
engine = create_engine(DATABASE_URL, echo=False)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Initialize the database by creating all tables."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """
    Dependency function to get a database session.
    
    Yields:
        Session: SQLAlchemy database session
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """
    Get a database session directly (for background tasks).
    
    Returns:
        Session: SQLAlchemy database session
    """
    return SessionLocal()
