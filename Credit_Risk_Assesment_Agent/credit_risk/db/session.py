"""
SQLAlchemy engine/session factory for the credit-risk Postgres store.

Connection details come entirely from environment variables (see `.env.example`) — no
credential is ever hardcoded. The engine is built once, lazily, on first use: building
it eagerly at import time would make every module that imports this one fail hard if
Postgres isn't configured yet (e.g. while just running a unit test that doesn't touch
the DB), so construction is deferred to the first `get_engine()`/`get_session()` call —
this is the module-level "construct once, reuse" pattern SQLAlchemy itself recommends
for an engine, not a class, since there is exactly one engine per process and no
swappable-implementation need.
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
    port = os.getenv("PG_PORT", "5432")
    user = os.getenv("PG_USER", "postgres")
    password = os.getenv("PG_PASSWORD", "")
    database = os.getenv("PG_DATABASE")
    if not database:
        raise DatabaseConfigError(
            "PG_DATABASE is not set — configure Postgres connection details in .env "
            "(see .env.example) before starting the API or CLI."
        )
    auth = f"{user}:{password}" if password else user
    return f"postgresql+psycopg://{auth}@{host}:{port}/{database}"


def get_engine() -> Engine:
    """Return the process-wide engine, creating it once."""
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(_database_url(), pool_pre_ping=True, future=True)
        _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency / context-managed session: always closed after use."""
    get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
