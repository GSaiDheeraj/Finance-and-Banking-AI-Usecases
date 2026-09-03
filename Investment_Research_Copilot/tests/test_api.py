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
    assert data["status"] == "healthy"
    assert "timestamp" in data


def test_create_portfolio(client):
    """Test creating a new portfolio."""
    response = client.post(
        "/portfolios",
        json={
            "portfolio_name": "Test Portfolio",
            "client_name": "Test Client",
            "description": "A test portfolio for unit testing"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "portfolio_id" in data
    assert data["portfolio_name"] == "Test Portfolio"
    assert data["status"] == "processing"


def test_get_portfolio(client):
    """Test getting portfolio information."""
    # First create a portfolio
    create_response = client.post(
        "/portfolios",
        json={"portfolio_name": "Test Portfolio", "client_name": "Test Client"}
    )
    portfolio_id = create_response.json()["portfolio_id"]
    
    # Then get the portfolio
    response = client.get(f"/portfolios/{portfolio_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["portfolio_id"] == portfolio_id


def test_get_portfolio_status(client):
    """Test getting portfolio status."""
    # First create a portfolio
    create_response = client.post(
        "/portfolios",
        json={"portfolio_name": "Test Portfolio"}
    )
    portfolio_id = create_response.json()["portfolio_id"]
    
    # Then get the status
    response = client.get(f"/portfolios/{portfolio_id}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["portfolio_id"] == portfolio_id
    assert "status" in data
