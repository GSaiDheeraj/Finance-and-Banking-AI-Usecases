"""Shared pytest fixtures for the FinDoc Intelligence Agent test suite."""
import pytest

from fin_doc_intel.db.models import Project
from fin_doc_intel.db.session import SessionLocal, init_db


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    """Ensure tables exist, and clean up test-created rows afterward.

    This is a local single-developer project with no CI and no test-DB swap —
    tests run against the real local Postgres (configured via .env) so they
    exercise the actual persistence path rather than mocks.
    """
    init_db()
    yield
    db = SessionLocal()
    try:
        # ORM-level delete (not a bulk statement) so related documents/line
        # items/research notes cascade per the relationships in db/models.py.
        for project in db.query(Project).filter(Project.project_name == "Test Project"):
            db.delete(project)
        db.commit()
    finally:
        db.close()
