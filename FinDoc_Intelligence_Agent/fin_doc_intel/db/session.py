"""Postgres engine and session helpers for the filing store."""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fin_doc_intel.db.models import Base

_cached_engine = None
_cached_factory = None


def _connection_string() -> str:
    return os.getenv(
        "DATABASE_URL",
        f"postgresql://{os.getenv('PGUSER', 'postgres')}:{os.getenv('PGPASSWORD', '7194')}@"
        f"{os.getenv('PGHOST', 'localhost')}:{os.getenv('PGPORT', '5433')}"
        f"/{os.getenv('PGDATABASE', 'findoc_db')}",
    )


def get_engine():
    """Create the shared engine on first use, then reuse it."""
    global _cached_engine, _cached_factory
    if _cached_engine is None:
        _cached_engine = create_engine(_connection_string(), echo=False)
        _cached_factory = sessionmaker(autocommit=False, autoflush=False, bind=_cached_engine)
    return _cached_engine


def reset_engine() -> None:
    """Drop the cached engine (intended for tests)."""
    global _cached_engine, _cached_factory
    _cached_engine = None
    _cached_factory = None


def SessionLocal() -> Session:  # noqa: N802
    """Open a fresh session from the shared factory."""
    global _cached_factory
    if _cached_factory is None:
        get_engine()
    assert _cached_factory is not None
    return _cached_factory()


def init_db() -> None:
    """Create every table when it does not exist yet."""
    Base.metadata.create_all(bind=get_engine())


def get_db() -> Iterator[Session]:
    """Request-scoped session for FastAPI routes."""
    handle = SessionLocal()
    try:
        yield handle
    finally:
        handle.close()


def get_db_session() -> Session:
    """Standalone session for background work outside a request."""
    return SessionLocal()
