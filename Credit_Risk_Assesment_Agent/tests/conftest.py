"""
Shared test fixtures — a dedicated `credit_risk_test` Postgres database.

pgvector needs a real Postgres (no SQLite fallback), so tests run against an actual
database, isolated from the real `credit_risk` one by forcing `PG_DATABASE` to the test
database *before* anything imports `credit_risk.db.session` — that module caches its
engine in a module-level global on first use, so whichever database is configured at
that first import is what every test gets for the rest of the process.
"""
from __future__ import annotations

import os

import credit_risk.config  # noqa: E402  loads .env for PG_HOST/PORT/USER/PASSWORD

# Force the test database regardless of what .env says for the real one — tests must
# never be able to run against real assessment data even if PG_DATABASE is already set.
os.environ["PG_DATABASE"] = "credit_risk_test"
os.environ.setdefault("EMBED_DIM", "1536")

import pytest  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from credit_risk.db.models import Base, init_schema  # noqa: E402
from credit_risk.db.session import get_engine, get_session  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _test_schema():
    init_schema()
    yield
    Base.metadata.drop_all(get_engine())


@pytest.fixture
def db_session() -> Session:
    session = next(get_session())
    try:
        yield session
    finally:
        session.rollback()  # never let one test's writes leak into the next
        session.close()
