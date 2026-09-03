"""
Tests for the Fraud Detection API endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from onboarding_risk.api.main import app


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


def test_create_case_documents(client):
    """Test creating a new case with documents."""
    response = client.post(
        "/cases",
        data={
            "client_name": "Test Client",
            "case_type": "documents",
            "description": "A test case for unit testing"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "case_id" in data
    assert data["client_name"] == "Test Client"
    assert data["case_type"] == "documents"
    assert data["status"] == "queued"


def test_create_case_name_search(client):
    """Test creating a new case with name search."""
    response = client.post(
        "/cases",
        data={
            "client_name": "John Doe",
            "case_type": "name_search"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "case_id" in data
    assert data["client_name"] == "John Doe"
    assert data["case_type"] == "name_search"


def test_get_case(client):
    """Test getting case information."""
    # First create a case
    create_response = client.post(
        "/cases",
        data={"client_name": "Test Client", "case_type": "documents"}
    )
    case_id = create_response.json()["case_id"]

    # Then get the case
    response = client.get(f"/cases/{case_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["case_id"] == case_id


def test_get_case_status(client):
    """Test getting case status."""
    # First create a case
    create_response = client.post(
        "/cases",
        data={"client_name": "Test Client", "case_type": "documents"}
    )
    case_id = create_response.json()["case_id"]
    
    # Then get the status
    response = client.get(f"/cases/{case_id}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["case_id"] == case_id
    assert "status" in data
