"""Tests for the Investment Research Copilot API contract.

DB-free: these tests exercise the health endpoint, request validation, and the
exception-to-response mapping without needing a live Postgres.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from portfolio_monitor.api.main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app, raise_server_exceptions=False)


def test_health_check(client):
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_malformed_portfolio_id_returns_404_without_touching_db(client):
    """A non-UUID id is rejected by the boundary before any DB access."""
    response = client.get("/portfolios/not-a-uuid")
    assert response.status_code == 404


def test_review_rejects_unknown_action(client):
    """Review actions outside approved|corrected|rejected fail validation (422)."""
    response = client.post(
        "/portfolios/00000000-0000-0000-0000-000000000000/review",
        json={"reviewer": "tester", "action": "bogus"},
    )
    assert response.status_code == 422


def test_openapi_exposes_portfolio_routes(client):
    """The router stays mounted with the documented monitoring operations."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/portfolios" in paths
    assert "/portfolios/{portfolio_id}" in paths
    assert "/portfolios/{portfolio_id}/review" in paths
