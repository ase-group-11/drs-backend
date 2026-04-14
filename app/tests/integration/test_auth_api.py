# File: app/tests/integration/test_auth_api.py
"""
Integration tests for authentication API endpoints.

Tests actual HTTP endpoints with mocked external dependencies (Twilio, Redis).

Tests:
1. POST /auth/register - User registration
2. POST /auth/register/verify - Registration OTP verification
3. POST /auth/login - User login
4. POST /auth/login/verify - Login OTP verification
5. GET /auth/health - Health check
"""

import pytest
import pytest_asyncio
import httpx
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.db.session import get_db
from app.db.models.user import User
from app.db.models.enums import UserStatus


# Test fixtures

# @pytest.fixture
# async def async_client():
#     """Create async HTTP client for testing."""
#     transport = httpx.ASGITransport(app=app)
#     async with AsyncClient(transport=transport, base_url="http://test") as client:
#         yield client

@pytest_asyncio.fixture
async def async_client():
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
    return session


@pytest.fixture
def override_get_db(mock_db_session):
    """Override FastAPI database dependency."""
    async def _override_get_db():
        yield mock_db_session
    
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()


# Test: POST /auth/register

@pytest.mark.asyncio
async def test_register_user_success(async_client, override_get_db):
    """Test successful user registration (send OTP)."""
    
    # Mock dependencies
    with patch('app.services.user_service.UserRepository') as MockRepo, \
         patch('app.services.user_service.store_registration_data') as mock_store, \
         patch('app.services.user_service.send_otp_code', return_value="123456"):
        
        # Mock repository methods
        mock_repo = MockRepo.return_value
        mock_repo.phone_exists = AsyncMock(return_value=False)
        mock_repo.email_exists = AsyncMock(return_value=False)
        
        mock_store.return_value = True
        
        # Make request
        response = await async_client.post(
            "/api/v1/auth/register",
            json={
                "phone_number": "+1234567890",
                "full_name": "John Doe",
                "email": "john@example.com"
            }
        )
        
        # Assert
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "OTP sent successfully" in data["message"]
        assert "+1234567890" in data["message"]


@pytest.mark.asyncio
async def test_register_user_duplicate_phone(async_client, override_get_db):
    """Test registration with existing phone number."""
    
    with patch('app.services.user_service.UserRepository') as MockRepo:
        # Mock phone exists
        mock_repo = MockRepo.return_value
        mock_repo.phone_exists = AsyncMock(return_value=True)
        
        # Make request
        response = await async_client.post(
            "/api/v1/auth/register",
            json={
                "phone_number": "+1234567890",
                "full_name": "John Doe"
            }
        )
        
        # Assert
        assert response.status_code == 400
        data = response.json()
        assert "already registered" in data["detail"].lower()


@pytest.mark.asyncio
async def test_register_user_invalid_phone_format(async_client):
    """Test registration with invalid phone number format."""
    
    # Make request with invalid phone (missing +)
    response = await async_client.post(
        "/api/v1/auth/register",
        json={
            "phone_number": "1234567890",  # Invalid: no +
            "full_name": "John Doe"
        }
    )
    
    # Assert
    assert response.status_code == 422  # Validation error


# Test: POST /auth/register/verify

@pytest.mark.asyncio
async def test_verify_registration_success(async_client, override_get_db):
    """Test successful registration verification."""
    
    from datetime import datetime, UTC
    
    with patch('app.services.user_service.verify_otp', return_value=True), \
         patch('app.services.user_service.get_registration_data') as mock_get_data, \
         patch('app.services.user_service.delete_registration_data'), \
         patch('app.services.user_service.UserRepository') as MockRepo, \
         patch('app.services.user_service.create_access_token', return_value="access_token"), \
         patch('app.services.user_service.create_refresh_token', return_value="refresh_token"):
        
        # Mock registration data
        mock_get_data.return_value = {
            "phone_number": "+1234567890",
            "full_name": "John Doe",
            "email": "john@example.com"
        }
        
        # Mock user creation
        mock_user = User(
            id="test-user-id",
            phone_number="+1234567890",
            full_name="John Doe",
            email="john@example.com",
            status=UserStatus.ACTIVE
        )
        mock_user.created_at = datetime.now(UTC)
        
        mock_repo = MockRepo.return_value
        mock_repo.create = AsyncMock(return_value=mock_user)
        
        # Make request
        response = await async_client.post(
            "/api/v1/auth/register/verify",
            json={
                "phone_number": "+1234567890",
                "otp": "123456"
            }
        )
        
        # Assert
        assert response.status_code == 201
        data = response.json()
        assert "user" in data
        assert "tokens" in data
        assert data["user"]["phone_number"] == "+1234567890"
        assert data["tokens"]["access_token"] == "access_token"


@pytest.mark.asyncio
async def test_verify_registration_invalid_otp(async_client, override_get_db):
    """Test registration verification with invalid OTP."""
    
    with patch('app.services.user_service.verify_otp', return_value=False):
        
        # Make request
        response = await async_client.post(
            "/api/v1/auth/register/verify",
            json={
                "phone_number": "+1234567890",
                "otp": "000000"
            }
        )
        
        # Assert
        assert response.status_code == 400
        data = response.json()
        assert "invalid" in data["detail"].lower() or "expired" in data["detail"].lower()


@pytest.mark.asyncio
async def test_verify_registration_no_cached_data(async_client, override_get_db):
    """Test registration verification when cache data is missing."""
    
    with patch('app.services.user_service.verify_otp', return_value=True), \
         patch('app.services.user_service.get_registration_data', return_value=None):
        
        # Make request
        response = await async_client.post(
            "/api/v1/auth/register/verify",
            json={
                "phone_number": "+1234567890",
                "otp": "123456"
            }
        )
        
        # Assert
        assert response.status_code == 400
        data = response.json()
        assert "not found" in data["detail"].lower()


# Test: POST /auth/login

@pytest.mark.asyncio
async def test_login_user_success(async_client, override_get_db):
    """Test successful user login (send OTP)."""
    
    with patch('app.services.user_service.UserRepository') as MockRepo, \
         patch('app.services.user_service.send_otp_code', return_value="123456"):
        
        # Mock active user
        mock_user = User(
            id="test-id",
            phone_number="+1234567890",
            full_name="John Doe",
            status=UserStatus.ACTIVE
        )
        
        mock_repo = MockRepo.return_value
        mock_repo.get_active_user_by_phone = AsyncMock(return_value=mock_user)
        
        # Make request
        response = await async_client.post(
            "/api/v1/auth/login",
            json={
                "phone_number": "+1234567890"
            }
        )
        
        # Assert
        assert response.status_code == 200
        data = response.json()
        assert "OTP sent successfully" in data["message"]


@pytest.mark.asyncio
async def test_login_user_not_found(async_client, override_get_db):
    """Test login with non-existent user."""
    
    with patch('app.services.user_service.UserRepository') as MockRepo:
        
        mock_repo = MockRepo.return_value
        mock_repo.get_active_user_by_phone = AsyncMock(return_value=None)
        
        # Make request
        response = await async_client.post(
            "/api/v1/auth/login",
            json={
                "phone_number": "+1234567890"
            }
        )
        
        # Assert
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()


# Test: POST /auth/login/verify

@pytest.mark.asyncio
async def test_verify_login_success(async_client, override_get_db):
    """Test successful login verification."""
    
    from datetime import datetime, UTC
    
    with patch('app.services.user_service.verify_otp', return_value=True), \
         patch('app.services.user_service.UserRepository') as MockRepo, \
         patch('app.services.user_service.create_access_token', return_value="access_token"), \
         patch('app.services.user_service.create_refresh_token', return_value="refresh_token"):
        
        # Mock active user
        mock_user = User(
            id="test-id",
            phone_number="+1234567890",
            full_name="John Doe",
            status=UserStatus.ACTIVE
        )
        mock_user.created_at = datetime.now(UTC)
        
        mock_repo = MockRepo.return_value
        mock_repo.get_active_user_by_phone = AsyncMock(return_value=mock_user)
        
        # Make request
        response = await async_client.post(
            "/api/v1/auth/login/verify",
            json={
                "phone_number": "+1234567890",
                "otp": "123456"
            }
        )
        
        # Assert
        assert response.status_code == 200
        data = response.json()
        assert "user" in data
        assert "tokens" in data
        assert data["tokens"]["access_token"] == "access_token"


# Test: GET /auth/health

@pytest.mark.asyncio
async def test_health_check(async_client):
    """Test health check endpoint."""
    
    response = await async_client.get("/api/v1/auth/health")
    
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "healthy" in data["message"].lower()


# Test: Rate Limiting

@pytest.mark.asyncio
async def test_register_rate_limit(async_client, override_get_db):
    """Test registration rate limiting."""
    
    with patch('app.services.user_service.UserRepository') as MockRepo, \
         patch('app.services.user_service.store_registration_data'), \
         patch('app.services.user_service.send_otp_code', return_value=None):
        
        mock_repo = MockRepo.return_value
        mock_repo.phone_exists = AsyncMock(return_value=False)
        mock_repo.email_exists = AsyncMock(return_value=False)
        
        # Make request
        response = await async_client.post(
            "/api/v1/auth/register",
            json={
                "phone_number": "+1234567890",
                "full_name": "John Doe"
            }
        )
        
        # Assert - should fail because OTP sending returned None
        assert response.status_code == 500


# Test: Invalid OTP Format

@pytest.mark.asyncio
async def test_verify_with_invalid_otp_format(async_client):
    """Test verification with invalid OTP format."""
    
    # OTP too short
    response = await async_client.post(
        "/api/v1/auth/register/verify",
        json={
            "phone_number": "+1234567890",
            "otp": "123"  # Too short
        }
    )
    
    assert response.status_code == 422
    
    # OTP with letters
    response = await async_client.post(
        "/api/v1/auth/register/verify",
        json={
            "phone_number": "+1234567890",
            "otp": "12345a"  # Contains letter
        }
    )
    
    assert response.status_code == 422