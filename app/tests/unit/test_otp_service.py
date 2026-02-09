# File: app/tests/unit/test_otp_service.py
"""
Unit tests for OTP service.

Tests ensure:
1. OTP generation creates valid codes
2. OTP storage in Redis with TTL
3. OTP verification works correctly
4. Expired OTPs are rejected
5. Invalid OTPs are rejected
6. Rate limiting prevents abuse
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import re


@pytest.mark.asyncio
async def test_generate_otp_creates_valid_code():
    """Test that OTP generation creates a valid numeric code."""
    # Arrange
    from app.services.otp_service import generate_otp
    
    # Act
    otp = generate_otp()
    
    # Assert
    assert otp is not None
    assert isinstance(otp, str)
    assert len(otp) == 6  # Default OTP length
    assert otp.isdigit()  # All digits
    assert int(otp) >= 100000  # Minimum 6-digit number
    assert int(otp) <= 999999  # Maximum 6-digit number


@pytest.mark.asyncio
async def test_generate_otp_custom_length():
    """Test OTP generation with custom length."""
    # Arrange
    from app.services.otp_service import generate_otp
    
    # Act
    otp_4 = generate_otp(length=4)
    otp_8 = generate_otp(length=8)
    
    # Assert
    assert len(otp_4) == 4
    assert len(otp_8) == 8
    assert otp_4.isdigit()
    assert otp_8.isdigit()


@pytest.mark.asyncio
async def test_store_otp_in_redis():
    """Test that OTP is stored in Redis with correct TTL."""
    # Arrange
    from app.services.otp_service import store_otp
    
    phone_number = "+1234567890"
    otp = "123456"
    
    # Mock Redis client
    with patch('app.services.otp_service.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_redis.return_value = mock_client
        
        # Act
        await store_otp(phone_number, otp)
        
        # Assert
        mock_client.setex.assert_called_once()
        call_args = mock_client.setex.call_args
        
        # Check key format
        assert call_args[0][0].startswith("otp:")
        assert phone_number in call_args[0][0]
        
        # Check expiry (should be 5 minutes = 300 seconds by default)
        assert call_args[0][1] == 300
        
        # Check OTP value
        assert otp in call_args[0][2]


@pytest.mark.asyncio
async def test_verify_otp_success():
    """Test successful OTP verification."""
    # Arrange
    from app.services.otp_service import verify_otp
    
    phone_number = "+1234567890"
    otp = "123456"
    
    # Mock Redis to return the OTP
    with patch('app.services.otp_service.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_client.get.return_value = otp
        mock_redis.return_value = mock_client
        
        # Act
        is_valid = await verify_otp(phone_number, otp)
        
        # Assert
        assert is_valid is True
        # Should delete OTP after successful verification
        mock_client.delete.assert_called_once()


@pytest.mark.asyncio
async def test_verify_otp_invalid_code():
    """Test OTP verification with wrong code."""
    # Arrange
    from app.services.otp_service import verify_otp
    
    phone_number = "+1234567890"
    correct_otp = "123456"
    wrong_otp = "654321"
    
    # Mock Redis to return different OTP
    with patch('app.services.otp_service.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_client.get.return_value = correct_otp
        mock_redis.return_value = mock_client
        
        # Act
        is_valid = await verify_otp(phone_number, wrong_otp)
        
        # Assert
        assert is_valid is False
        # Should NOT delete OTP on failed verification
        mock_client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_verify_otp_expired():
    """Test OTP verification when OTP has expired."""
    # Arrange
    from app.services.otp_service import verify_otp
    
    phone_number = "+1234567890"
    otp = "123456"
    
    # Mock Redis to return None (OTP expired/not found)
    with patch('app.services.otp_service.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_client.get.return_value = None
        mock_redis.return_value = mock_client
        
        # Act
        is_valid = await verify_otp(phone_number, otp)
        
        # Assert
        assert is_valid is False


@pytest.mark.asyncio
async def test_get_otp_key_format():
    """Test that OTP Redis key is formatted correctly."""
    # Arrange
    from app.services.otp_service import _get_otp_key
    
    phone_number = "+1234567890"
    
    # Act
    key = _get_otp_key(phone_number)
    
    # Assert
    assert key.startswith("otp:")
    assert phone_number in key
    assert key == f"otp:{phone_number}"


@pytest.mark.asyncio
async def test_send_otp_generates_and_stores():
    """Test that send_otp generates OTP and stores it."""
    # Arrange
    from app.services.otp_service import send_otp_code
    
    phone_number = "+1234567890"
    
    # In testing environment, SMS is mocked automatically
    # Act
    otp = await send_otp_code(phone_number)
    
    # Assert
    assert otp is not None
    assert len(otp) == 6
    assert otp.isdigit()
    
    # Verify OTP was stored in Redis (can retrieve it)
    from app.services.otp_service import verify_otp
    is_valid = await verify_otp(phone_number, otp)
    assert is_valid is True


@pytest.mark.asyncio
async def test_rate_limiting_prevents_spam():
    """Test that rate limiting prevents OTP spam."""
    # Arrange
    from app.services.otp_service import check_rate_limit
    
    phone_number = "+1234567890"
    
    # Mock Redis
    with patch('app.services.otp_service.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_client.get.return_value = "2"  # 2 OTPs sent recently
        mock_redis.return_value = mock_client
        
        # Act
        can_send = await check_rate_limit(phone_number, max_attempts=3)
        
        # Assert
        assert can_send is True  # 2 < 3, can still send
        
        # Test when limit reached
        mock_client.get.return_value = "3"
        can_send = await check_rate_limit(phone_number, max_attempts=3)
        assert can_send is False  # 3 >= 3, cannot send