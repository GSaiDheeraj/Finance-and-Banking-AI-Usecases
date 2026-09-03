"""
SQLAlchemy engine/session factory for the portfolio-monitor Postgres store.

Connection details come entirely from environment variables (see `.env.example`).
The engine is built lazily on first use so import-time failures are avoided when
running unit tests that never touch the database.
"""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker | None = None


class DatabaseConfigError(RuntimeError):
    """Raised when Postgres connection details are missing or invalid."""


def _database_url() -> str:
    host = os.getenv("PG_HOST", "127.0.0.1")
    port = os.getenv("PG_PORT", "5433")
    user = os.getenv("PG_USER", "postgres")
    password = os.getenv("PG_PASSWORD", "7194")
    database = os.getenv("PG_DATABASE","investment_research" )
    if not database:
        raise DatabaseConfigError(
            "PG_DATABASE is not set — configure Postgres in .env (see .env.example)."
        )
    auth = f"{user}:{password}" if password else user
    return f"postgresql+psycopg://{auth}@{host}:{port}/{database}"


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(_database_url(), pool_pre_ping=True, future=True)
        _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_session() -> Iterator[Session]:
    get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
