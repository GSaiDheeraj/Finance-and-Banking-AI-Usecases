"""
Tests for the Investment Research Copilot API endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from portfolio_monitor.api.main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


def test_health_check(client):
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_create_portfolio(client, tmp_path):
    """Test creating a new portfolio with a holdings CSV upload."""
    csv_file = tmp_path / "holdings.csv"
    csv_file.write_text("symbol,quantity\nAAPL,10\nMSFT,5\n")

    with open(csv_file, "rb") as f:
        response = client.post(
            "/portfolios",
            data={
                "portfolio_name": "Test Portfolio",
                "client_name": "Test Client",
                "mandate": "balanced",
            },
            files={"holdings_files": ("holdings.csv", f, "text/csv")},
        )
    assert response.status_code == 202
    data = response.json()
    assert "portfolio_id" in data
    assert data["portfolio_name"] == "Test Portfolio"
    assert data["status"] == "created"


def test_get_portfolio(client, tmp_path):
    """Test getting portfolio information."""
    csv_file = tmp_path / "holdings.csv"
    csv_file.write_text("symbol,quantity\nAAPL,10\n")

    with open(csv_file, "rb") as f:
        create_response = client.post(
            "/portfolios",
            data={"portfolio_name": "Test Portfolio", "client_name": "Test Client"},
            files={"holdings_files": ("holdings.csv", f, "text/csv")},
        )
    portfolio_id = create_response.json()["portfolio_id"]

    response = client.get(f"/portfolios/{portfolio_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["portfolio_id"] == portfolio_id


def test_get_portfolio_status(client, tmp_path):
    """Test getting portfolio status."""
    csv_file = tmp_path / "holdings.csv"
    csv_file.write_text("symbol,quantity\nAAPL,10\n")

    with open(csv_file, "rb") as f:
        create_response = client.post(
            "/portfolios",
            data={"portfolio_name": "Test Portfolio"},
            files={"holdings_files": ("holdings.csv", f, "text/csv")},
        )
    portfolio_id = create_response.json()["portfolio_id"]

    response = client.get(f"/portfolios/{portfolio_id}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["portfolio_id"] == portfolio_id
    assert "status" in data
