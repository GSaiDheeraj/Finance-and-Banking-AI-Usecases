"""
Tests for the FinDoc Intelligence API endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from fin_doc_intel.api.main import app


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


def test_create_project(client):
    """Test creating a new project."""
    response = client.post(
        "/projects",
        data={
            "project_name": "Test Project",
            "description": "A test project for unit testing"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "project_id" in data
    assert data["project_name"] == "Test Project"
    assert data["status"] == "processing"


def test_get_project(client):
    """Test getting project information."""
    # First create a project
    create_response = client.post(
        "/projects",
        data={"project_name": "Test Project"}
    )
    project_id = create_response.json()["project_id"]
    
    # Then get the project
    response = client.get(f"/projects/{project_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == project_id


def test_get_project_status(client):
    """Test getting project status."""
    # First create a project
    create_response = client.post(
        "/projects",
        data={"project_name": "Test Project"}
    )
    project_id = create_response.json()["project_id"]
    
    # Then get the status
    response = client.get(f"/projects/{project_id}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == project_id
    assert "status" in data
