# File: app/tests/integration/test_disaster_reports_api.py
"""
Integration tests for Disaster Report API endpoints.

Tests critical functionality:
1. POST /disasters/report - Submit disaster report (success, unauthenticated, invalid data)
2. GET /disasters/my-reports - Get user's disaster reports
3. GET /disasters/report/{report_id} - Get specific disaster report (success, forbidden)
"""

import pytest
import pytest_asyncio
import httpx
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import uuid

from app.main import app
from app.db.session import get_db
from app.dependencies import get_current_user
from app.db.models.user import User
from app.db.models.disaster_report import DisasterReport
from app.db.models.enums import UserStatus, UserRole, DisasterType, Severity, ReportStatus


# Test fixtures

@pytest_asyncio.fixture
async def async_client():
    """Create async HTTP client for testing."""
    transport = httpx.ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def mock_db_session():
    """Create mock database session."""
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_current_user():
    """Create a mock authenticated user."""
    user = User()
    user.id = str(uuid.uuid4())
    user.phone_number = "+353871234567"
    user.full_name = "Test User"
    user.email = "test@example.com"
    user.status = UserStatus.ACTIVE
    user.role = UserRole.RESIDENT
    user.created_at = datetime.utcnow()
    user.updated_at = datetime.utcnow()
    return user


@pytest.fixture
def override_get_db(mock_db_session):
    """Override FastAPI database dependency."""
    async def _override_get_db():
        yield mock_db_session

    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def override_get_current_user(mock_current_user):
    """Override FastAPI get_current_user dependency."""
    async def _override_get_current_user():
        return mock_current_user

    app.dependency_overrides[get_current_user] = _override_get_current_user
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def sample_disaster_report_request():
    """Sample disaster report request data."""
    return {
        "location_address": "123 O'Connell Street, Dublin 1, Ireland",
        "location_latitude": 53.3498,
        "location_longitude": -6.2603,
        "disaster_type": "fire",
        "severity": "high",
        "description": "Large fire at commercial building with smoke visible from across the city",
        "media_urls": ["https://example.com/photo1.jpg", "https://example.com/photo2.jpg"],
        "people_affected": 25,
        "multiple_casualties": True,
        "structural_damage": True,
        "hazmat_involved": False,
        "road_blocked": True
    }


# Test: POST /disasters/report

@pytest.mark.asyncio
async def test_submit_disaster_report_success(
    async_client,
    override_get_db,
    override_get_current_user,
    sample_disaster_report_request,
    mock_current_user
):
    """Test successful disaster report submission."""

    with patch('app.repositories.disaster_report_repository.DisasterReportRepository.create') as mock_create:
        # Mock created report
        mock_report = DisasterReport()
        mock_report.id = str(uuid.uuid4())
        mock_report.user_id = mock_current_user.id
        mock_report.location_address = sample_disaster_report_request["location_address"]
        mock_report.location_latitude = sample_disaster_report_request["location_latitude"]
        mock_report.location_longitude = sample_disaster_report_request["location_longitude"]
        mock_report.disaster_type = DisasterType.FIRE
        mock_report.severity = Severity.HIGH
        mock_report.description = sample_disaster_report_request["description"]
        mock_report.media_urls = sample_disaster_report_request["media_urls"]
        mock_report.people_affected = sample_disaster_report_request["people_affected"]
        mock_report.multiple_casualties = sample_disaster_report_request["multiple_casualties"]
        mock_report.structural_damage = sample_disaster_report_request["structural_damage"]
        mock_report.hazmat_involved = sample_disaster_report_request["hazmat_involved"]
        mock_report.road_blocked = sample_disaster_report_request["road_blocked"]
        mock_report.status = ReportStatus.SUBMITTED
        mock_report.assigned_to_id = None
        mock_report.assigned_department = None
        mock_report.response_time = None
        mock_report.resolved_time = None
        mock_report.resolution_notes = None
        mock_report.created_at = datetime.utcnow()
        mock_report.updated_at = datetime.utcnow()

        mock_create.return_value = mock_report

        # Make request
        response = await async_client.post(
            "/api/v1/disasters/report",
            json=sample_disaster_report_request
        )

        # Assert
        assert response.status_code == 201
        data = response.json()
        assert data["user_id"] == mock_current_user.id
        assert data["disaster_type"] == "fire"
        assert data["severity"] == "high"
        assert data["status"] == "submitted"
        assert data["people_affected"] == 25
        assert data["multiple_casualties"] is True


@pytest.mark.asyncio
async def test_submit_disaster_report_unauthenticated(async_client, sample_disaster_report_request):
    """Test disaster report submission without authentication."""

    # Make request without authentication override
    response = await async_client.post(
        "/api/v1/disasters/report",
        json=sample_disaster_report_request
    )

    # Assert
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_submit_disaster_report_invalid_data(
    async_client,
    override_get_db,
    override_get_current_user
):
    """Test disaster report submission with invalid data."""

    # Missing required fields
    invalid_request = {
        "location_address": "123 Street",
        "disaster_type": "fire"
        # Missing severity, description, etc.
    }

    response = await async_client.post(
        "/api/v1/disasters/report",
        json=invalid_request
    )

    # Assert validation error
    assert response.status_code == 422


# Test: GET /disasters/my-reports

@pytest.mark.asyncio
async def test_get_my_reports_success(
    async_client,
    override_get_db,
    override_get_current_user,
    mock_current_user
):
    """Test getting user's disaster reports."""

    with patch('app.repositories.disaster_report_repository.DisasterReportRepository.get_user_reports') as mock_get, \
         patch('app.repositories.disaster_report_repository.DisasterReportRepository.count_user_reports') as mock_count:

        # Mock report
        mock_report = DisasterReport()
        mock_report.id = str(uuid.uuid4())
        mock_report.user_id = mock_current_user.id
        mock_report.disaster_type = DisasterType.FIRE
        mock_report.severity = Severity.HIGH
        mock_report.description = "Test disaster"
        mock_report.location_address = "Test Street"
        mock_report.status = ReportStatus.SUBMITTED
        mock_report.people_affected = 10
        mock_report.multiple_casualties = False
        mock_report.structural_damage = False
        mock_report.hazmat_involved = False
        mock_report.road_blocked = False
        mock_report.created_at = datetime.utcnow()
        mock_report.updated_at = datetime.utcnow()

        mock_get.return_value = [mock_report]
        mock_count.return_value = 1

        response = await async_client.get("/api/v1/disasters/my-reports")

        assert response.status_code == 200
        data = response.json()
        assert "reports" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert data["total"] == 1
        assert len(data["reports"]) == 1
        assert data["reports"][0]["id"] == mock_report.id


# Test: GET /disasters/report/{report_id}

@pytest.mark.asyncio
async def test_get_report_by_id_success(
    async_client,
    override_get_db,
    override_get_current_user,
    mock_current_user
):
    """Test getting specific disaster report by ID."""

    report_id = str(uuid.uuid4())

    with patch('app.repositories.disaster_report_repository.DisasterReportRepository.get_by_id') as mock_get:
        # Mock report owned by current user
        mock_report = DisasterReport()
        mock_report.id = report_id
        mock_report.user_id = mock_current_user.id
        mock_report.disaster_type = DisasterType.FIRE
        mock_report.severity = Severity.HIGH
        mock_report.description = "Test disaster"
        mock_report.location_address = "Test Street"
        mock_report.status = ReportStatus.SUBMITTED
        mock_report.people_affected = 10
        mock_report.multiple_casualties = False
        mock_report.structural_damage = False
        mock_report.hazmat_involved = False
        mock_report.road_blocked = False
        mock_report.created_at = datetime.utcnow()
        mock_report.updated_at = datetime.utcnow()

        mock_get.return_value = mock_report

        response = await async_client.get(f"/api/v1/disasters/report/{report_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == report_id
        assert data["user_id"] == mock_current_user.id


@pytest.mark.asyncio
async def test_get_report_by_id_forbidden(
    async_client,
    override_get_db,
    override_get_current_user,
    mock_current_user
):
    """Test getting disaster report owned by another user."""

    report_id = str(uuid.uuid4())
    other_user_id = str(uuid.uuid4())

    with patch('app.repositories.disaster_report_repository.DisasterReportRepository.get_by_id') as mock_get:
        # Mock report owned by different user
        mock_report = DisasterReport()
        mock_report.id = report_id
        mock_report.user_id = other_user_id  # Different user
        mock_report.disaster_type = DisasterType.FIRE
        mock_report.severity = Severity.HIGH
        mock_report.description = "Test disaster"
        mock_report.location_address = "Test Street"
        mock_report.status = ReportStatus.SUBMITTED
        mock_report.people_affected = 10
        mock_report.multiple_casualties = False
        mock_report.structural_damage = False
        mock_report.hazmat_involved = False
        mock_report.road_blocked = False
        mock_report.created_at = datetime.utcnow()
        mock_report.updated_at = datetime.utcnow()

        mock_get.return_value = mock_report

        response = await async_client.get(f"/api/v1/disasters/report/{report_id}")

        assert response.status_code == 403
