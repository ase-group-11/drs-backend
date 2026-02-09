# File: app/tests/unit/test_user_service.py
"""
Unit tests for user service.

Tests ensure:
1. User registration sends OTP
2. Registration verification creates user
3. Duplicate phone/email handling
4. User login sends OTP
5. Login verification returns tokens
6. Invalid user/OTP handling
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


@pytest.mark.asyncio
async def test_register_user_sends_otp(mock_db_session):
    """Test that user registration generates and sends OTP."""
    # Arrange
    from app.services.user_service import UserService
    
    # Create service with mock session
    service = UserService(mock_db_session)
    
    phone_number = "+1234567890"
    full_name = "John Doe"
    email = "john@example.com"
    
    # Mock the repository methods directly on the service's repository
    with patch.object(service.user_repo, 'phone_exists', return_value=False), \
         patch.object(service.user_repo, 'email_exists', return_value=False), \
         patch('app.services.user_service.send_otp_code', return_value="123456") as mock_send_otp:
        
        # Act
        result = await service.register_user(phone_number, full_name, email)
        
        # Assert
        assert result is not None
        assert "message" in result
        assert phone_number in result["message"]
        mock_send_otp.assert_called_once_with(phone_number)


@pytest.mark.asyncio
async def test_register_user_duplicate_phone_raises_error(mock_db_session):
    """Test that registering with existing phone number raises error."""
    # Arrange
    from app.services.user_service import UserService
    
    service = UserService(mock_db_session)
    phone_number = "+1234567890"
    
    # Mock phone_exists to return True
    with patch.object(service.user_repo, 'phone_exists', return_value=True):
        
        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            await service.register_user(phone_number, "John Doe")
        
        assert "already registered" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_verify_registration_creates_user_and_returns_tokens(mock_db_session):
    """Test that OTP verification creates user and returns JWT tokens."""
    # Arrange
    from app.services.user_service import UserService
    from app.models.user import User
    from app.models.enums import UserStatus
    from datetime import datetime, UTC
    
    service = UserService(mock_db_session)
    phone_number = "+1234567890"
    otp = "123456"
    
    # Create mock user
    mock_user = User(
        id="test-user-id",
        phone_number=phone_number,
        full_name="John Doe",
        status=UserStatus.ACTIVE
    )
    mock_user.created_at = datetime.now(UTC)
    
    with patch('app.services.user_service.verify_otp', return_value=True), \
         patch.object(service.user_repo, 'create', return_value=mock_user), \
         patch('app.services.user_service.create_access_token', return_value="access_token_123"), \
         patch('app.services.user_service.create_refresh_token', return_value="refresh_token_456"):
        
        # Act
        result = await service.verify_registration(phone_number, otp, "John Doe")
        
        # Assert
        assert result is not None
        assert "user" in result
        assert "tokens" in result
        assert result["user"]["id"] == "test-user-id"
        assert result["tokens"]["access_token"] == "access_token_123"


@pytest.mark.asyncio
async def test_verify_registration_invalid_otp_raises_error(mock_db_session):
    """Test that invalid OTP raises error."""
    # Arrange
    from app.services.user_service import UserService
    
    service = UserService(mock_db_session)
    
    with patch('app.services.user_service.verify_otp') as mock_verify_otp:
        mock_verify_otp.return_value = False  # Invalid OTP
        
        # Act & Assert
        with pytest.raises(Exception) as exc_info:
            await service.verify_registration("+1234567890", "wrong_otp", "John Doe")
        
        assert "invalid" in str(exc_info.value).lower() or \
               "expired" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_login_user_sends_otp(mock_db_session):
    """Test that user login sends OTP."""
    # Arrange
    from app.services.user_service import UserService
    from app.models.user import User
    from app.models.enums import UserStatus
    
    service = UserService(mock_db_session)
    phone_number = "+1234567890"
    
    # Mock active user
    mock_user = User(
        id="test-id",
        phone_number=phone_number,
        full_name="John Doe",
        status=UserStatus.ACTIVE
    )
    
    with patch.object(service.user_repo, 'get_active_user_by_phone', return_value=mock_user), \
         patch('app.services.user_service.send_otp_code', return_value="123456") as mock_send_otp:
        
        # Act
        result = await service.login_user(phone_number)
        
        # Assert
        assert result is not None
        assert "message" in result
        mock_send_otp.assert_called_once_with(phone_number)


@pytest.mark.asyncio
async def test_login_user_not_found_raises_error(mock_db_session):
    """Test that login with non-existent user raises error."""
    # Arrange
    from app.services.user_service import UserService
    
    service = UserService(mock_db_session)
    
    with patch.object(service.user_repo, 'get_active_user_by_phone', return_value=None):
        
        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            await service.login_user("+1234567890")
        
        assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_verify_login_returns_tokens(mock_db_session):
    """Test that login verification returns JWT tokens."""
    # Arrange
    from app.services.user_service import UserService
    from app.models.user import User
    from app.models.enums import UserStatus
    from datetime import datetime, UTC
    
    service = UserService(mock_db_session)
    phone_number = "+1234567890"
    otp = "123456"
    
    mock_user = User(
        id="test-id",
        phone_number=phone_number,
        full_name="John Doe",
        status=UserStatus.ACTIVE
    )
    mock_user.created_at = datetime.now(UTC)
    
    with patch('app.services.user_service.verify_otp', return_value=True), \
         patch.object(service.user_repo, 'get_active_user_by_phone', return_value=mock_user), \
         patch('app.services.user_service.create_access_token', return_value="access_123"), \
         patch('app.services.user_service.create_refresh_token', return_value="refresh_456"):
        
        # Act
        result = await service.verify_login(phone_number, otp)
        
        # Assert
        assert result is not None
        assert "user" in result
        assert "tokens" in result
        assert result["tokens"]["access_token"] == "access_123"


@pytest.mark.asyncio
async def test_get_user_by_id(mock_db_session):
    """Test getting user by ID."""
    # Arrange
    from app.services.user_service import UserService
    from app.models.user import User
    
    service = UserService(mock_db_session)
    user_id = "test-id"
    
    mock_user = User(
        id=user_id,
        phone_number="+1234567890",
        full_name="John Doe"
    )
    
    with patch.object(service.user_repo, 'get_by_id', return_value=mock_user):
        
        # Act
        result = await service.get_user_by_id(user_id)
        
        # Assert
        assert result == mock_user
        assert result.id == user_id