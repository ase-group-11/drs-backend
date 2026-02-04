# File: app/tests/integration/test_emergency_team_api.py
"""
Integration tests for emergency team authentication API endpoints.

Tests actual HTTP endpoints with mocked dependencies.

Tests:
1. POST /emergency-team/register - Team member registration
2. POST /emergency-team/login - Team member login
3. POST /emergency-team/change-password - Password change
4. GET /emergency-team/health - Health check
"""

import pytest
import pytest_asyncio
import httpx
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch
from datetime import datetime, UTC

from app.main import app
from app.db.session import get_db
from app.models.emergency_team import EmergencyTeam
from app.models.enums import UserStatus, EmergencyTeamRole, Department


# Test fixtures

# @pytest.fixture
# async def async_client():
#     """Create async HTTP client for testing."""
#     async with AsyncClient(app=app, base_url="http://test") as client:
#         yield client

@pytest_asyncio.fixture
async def async_client():
    transport = httpx.ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# @pytest.fixture
# async def mock_db_session():
#     """Create mock database session."""
#     session = AsyncMock()
#     session.commit = AsyncMock()
#     session.rollback = AsyncMock()
#     session.close = AsyncMock()
#     session.refresh = AsyncMock()
#     session.flush = AsyncMock()
#     return session

@pytest.fixture
def mock_db_session():
    """Create mock database session."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    return session

@pytest.fixture
def override_get_db(mock_db_session):
    """Override FastAPI database dependency."""
    async def _override_get_db():
        yield mock_db_session
    
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()


# Test: POST /emergency-team/register

@pytest.mark.asyncio
async def test_register_team_member_success(async_client, override_get_db):
    """Test successful team member registration."""
    
    with patch('app.services.emergency_team_service.EmergencyTeamRepository') as MockRepo, \
         patch('app.services.emergency_team_service.hash_password', return_value="hashed_pass"):
        
        # Mock repository
        mock_team_member = EmergencyTeam(
            id="test-id",
            phone_number="+1234567890",
            password_hash="hashed_pass",
            full_name="John Doe",
            email="john.doe@emergency.ie",
            role=EmergencyTeamRole.STAFF,
            department=Department.MEDICAL,
            status=UserStatus.ACTIVE
        )
        mock_team_member.created_at = datetime.now(UTC)
        
        mock_repo = MockRepo.return_value
        mock_repo.phone_exists = AsyncMock(return_value=False)
        mock_repo.email_exists = AsyncMock(return_value=False)
        mock_repo.employee_id_exists = AsyncMock(return_value=False)
        mock_repo.create = AsyncMock(return_value=mock_team_member)
        
        # Make request
        response = await async_client.post(
            "/api/v1/emergency-team/register",
            json={
                "phone_number": "+1234567890",
                "password": "SecurePass123",
                "full_name": "John Doe",
                "email": "john.doe@emergency.ie",
                "role": "staff",
                "department": "medical",
                "employee_id": "EMP001"
            }
        )
        
        # Assert
        assert response.status_code == 201
        data = response.json()
        assert "message" in data
        assert "team_member" in data
        assert data["team_member"]["email"] == "john.doe@emergency.ie"


@pytest.mark.asyncio
async def test_register_team_member_duplicate_email(async_client, override_get_db):
    """Test registration with existing email."""
    
    with patch('app.services.emergency_team_service.EmergencyTeamRepository') as MockRepo:
        
        mock_repo = MockRepo.return_value
        mock_repo.phone_exists = AsyncMock(return_value=False)
        mock_repo.email_exists = AsyncMock(return_value=True)
        
        # Make request
        response = await async_client.post(
            "/api/v1/emergency-team/register",
            json={
                "phone_number": "+1234567890",
                "password": "SecurePass123",
                "full_name": "John Doe",
                "email": "john.doe@emergency.ie",
                "role": "staff",
                "department": "medical"
            }
        )
        
        # Assert
        assert response.status_code == 400
        assert "already registered" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_register_team_member_weak_password(async_client):
    """Test registration with weak password."""
    
    # Make request with weak password (no uppercase)
    response = await async_client.post(
        "/api/v1/emergency-team/register",
        json={
            "phone_number": "+1234567890",
            "password": "weakpass123",  # No uppercase
            "full_name": "John Doe",
            "email": "john.doe@emergency.ie",
            "role": "staff",
            "department": "medical"
        }
    )
    
    # Assert
    assert response.status_code == 422
    assert "uppercase" in response.json()["detail"][0]["msg"].lower()


@pytest.mark.asyncio
async def test_register_team_member_invalid_role(async_client):
    """Test registration with invalid role."""
    
    response = await async_client.post(
        "/api/v1/emergency-team/register",
        json={
            "phone_number": "+1234567890",
            "password": "SecurePass123",
            "full_name": "John Doe",
            "email": "john.doe@emergency.ie",
            "role": "invalid_role",
            "department": "medical"
        }
    )
    
    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_team_member_invalid_department(async_client):
    """Test registration with invalid department."""
    
    response = await async_client.post(
        "/api/v1/emergency-team/register",
        json={
            "phone_number": "+1234567890",
            "password": "SecurePass123",
            "full_name": "John Doe",
            "email": "john.doe@emergency.ie",
            "role": "staff",
            "department": "invalid_dept"
        }
    )
    
    # Assert
    assert response.status_code == 422


# Test: POST /emergency-team/login

@pytest.mark.asyncio
async def test_login_team_member_with_email_success(async_client, override_get_db):
    """Test successful login with email."""
    
    with patch('app.services.emergency_team_service.EmergencyTeamRepository') as MockRepo, \
         patch('app.services.emergency_team_service.verify_password', return_value=True), \
         patch('app.services.emergency_team_service.create_access_token', return_value="access_token"), \
         patch('app.services.emergency_team_service.create_refresh_token', return_value="refresh_token"):
        
        # Mock team member
        mock_team_member = EmergencyTeam(
            id="test-id",
            phone_number="+1234567890",
            password_hash="hashed_pass",
            full_name="John Doe",
            email="john.doe@emergency.ie",
            role=EmergencyTeamRole.STAFF,
            department=Department.MEDICAL,
            status=UserStatus.ACTIVE
        )
        mock_team_member.created_at = datetime.now(UTC)
        
        mock_repo = MockRepo.return_value
        mock_repo.get_active_team_member_by_email = AsyncMock(return_value=mock_team_member)
        
        # Make request
        response = await async_client.post(
            "/api/v1/emergency-team/login",
            json={
                "email": "john.doe@emergency.ie",
                "password": "SecurePass123"
            }
        )
        
        # Assert
        assert response.status_code == 200
        data = response.json()
        assert "team_member" in data
        assert "tokens" in data
        assert data["tokens"]["access_token"] == "access_token"


@pytest.mark.asyncio
async def test_login_team_member_with_phone_success(async_client, override_get_db):
    """Test successful login with phone number."""
    
    with patch('app.services.emergency_team_service.EmergencyTeamRepository') as MockRepo, \
         patch('app.services.emergency_team_service.verify_password', return_value=True), \
         patch('app.services.emergency_team_service.create_access_token', return_value="access_token"), \
         patch('app.services.emergency_team_service.create_refresh_token', return_value="refresh_token"):
        
        mock_team_member = EmergencyTeam(
            id="test-id",
            phone_number="+1234567890",
            password_hash="hashed_pass",
            full_name="John Doe",
            email="john.doe@emergency.ie",
            role=EmergencyTeamRole.STAFF,
            department=Department.MEDICAL,
            status=UserStatus.ACTIVE
        )
        mock_team_member.created_at = datetime.now(UTC)
        
        mock_repo = MockRepo.return_value
        mock_repo.get_active_team_member_by_phone = AsyncMock(return_value=mock_team_member)
        
        # Make request
        response = await async_client.post(
            "/api/v1/emergency-team/login",
            json={
                "phone_number": "+1234567890",
                "password": "SecurePass123"
            }
        )
        
        # Assert
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_login_team_member_invalid_credentials(async_client, override_get_db):
    """Test login with invalid credentials."""
    
    with patch('app.services.emergency_team_service.EmergencyTeamRepository') as MockRepo:
        
        mock_repo = MockRepo.return_value
        mock_repo.get_active_team_member_by_email = AsyncMock(return_value=None)
        
        # Make request
        response = await async_client.post(
            "/api/v1/emergency-team/login",
            json={
                "email": "john.doe@emergency.ie",
                "password": "WrongPassword"
            }
        )
        
        # Assert
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_team_member_wrong_password(async_client, override_get_db):
    """Test login with correct email but wrong password."""
    
    with patch('app.services.emergency_team_service.EmergencyTeamRepository') as MockRepo, \
         patch('app.services.emergency_team_service.verify_password', return_value=False):
        
        mock_team_member = EmergencyTeam(
            id="test-id",
            phone_number="+1234567890",
            password_hash="hashed_pass",
            full_name="John Doe",
            email="john.doe@emergency.ie",
            role=EmergencyTeamRole.STAFF,
            department=Department.MEDICAL,
            status=UserStatus.ACTIVE
        )
        
        mock_repo = MockRepo.return_value
        mock_repo.get_active_team_member_by_email = AsyncMock(return_value=mock_team_member)
        
        # Make request
        response = await async_client.post(
            "/api/v1/emergency-team/login",
            json={
                "email": "john.doe@emergency.ie",
                "password": "WrongPassword"
            }
        )
        
        # Assert
        assert response.status_code == 401


# Test: GET /emergency-team/health

@pytest.mark.asyncio
async def test_health_check(async_client):
    """Test health check endpoint."""
    
    response = await async_client.get("/api/v1/emergency-team/health")
    
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "healthy" in data["message"].lower()


# Test: Password validation edge cases

@pytest.mark.asyncio
async def test_password_validation_no_uppercase(async_client):
    """Test password without uppercase letter."""
    
    response = await async_client.post(
        "/api/v1/emergency-team/register",
        json={
            "phone_number": "+1234567890",
            "password": "weakpass123",
            "full_name": "John Doe",
            "email": "john.doe@emergency.ie",
            "role": "staff",
            "department": "medical"
        }
    )
    
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_password_validation_no_digit(async_client):
    """Test password without digit."""
    
    response = await async_client.post(
        "/api/v1/emergency-team/register",
        json={
            "phone_number": "+1234567890",
            "password": "WeakPassword",
            "full_name": "John Doe",
            "email": "john.doe@emergency.ie",
            "role": "staff",
            "department": "medical"
        }
    )
    
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_password_validation_too_short(async_client):
    """Test password too short."""
    
    response = await async_client.post(
        "/api/v1/emergency-team/register",
        json={
            "phone_number": "+1234567890",
            "password": "Pass1",
            "full_name": "John Doe",
            "email": "john.doe@emergency.ie",
            "role": "staff",
            "department": "medical"
        }
    )
    
    assert response.status_code == 422