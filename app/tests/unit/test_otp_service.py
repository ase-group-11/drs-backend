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
    """Test that OTP is stored with correct TTL via set_with_expiry."""
    from app.services.otp_service import store_otp

    phone_number = "+1234567890"
    otp = "123456"

    with patch('app.services.otp_service.set_with_expiry', new_callable=AsyncMock) as mock_set:
        mock_set.return_value = True

        # Act
        await store_otp(phone_number, otp)

        # Assert
        mock_set.assert_called_once()
        call_args = mock_set.call_args[0]

        # Check key format
        assert call_args[0].startswith("otp:")
        assert phone_number in call_args[0]

        # Check OTP value
        assert call_args[1] == otp

        # Check expiry (settings.OTP_EXPIRY_SECONDS, default 300)
        assert call_args[2] > 0


@pytest.mark.asyncio
async def test_verify_otp_success():
    """Test successful OTP verification."""
    from app.services.otp_service import verify_otp

    phone_number = "+1234567890"
    otp = "123456"

    with patch('app.services.otp_service.get_value', new_callable=AsyncMock) as mock_get, \
         patch('app.services.otp_service.delete_key', new_callable=AsyncMock) as mock_del:
        mock_get.return_value = otp
        mock_del.return_value = 1

        # Act
        is_valid = await verify_otp(phone_number, otp)

        # Assert
        assert is_valid is True
        # Should delete OTP after successful verification
        mock_del.assert_called_once()


@pytest.mark.asyncio
async def test_verify_otp_invalid_code():
    """Test OTP verification with wrong code."""
    from app.services.otp_service import verify_otp

    phone_number = "+1234567890"
    correct_otp = "123456"
    wrong_otp = "654321"

    with patch('app.services.otp_service.get_value', new_callable=AsyncMock) as mock_get, \
         patch('app.services.otp_service.delete_key', new_callable=AsyncMock) as mock_del:
        mock_get.return_value = correct_otp

        # Act
        is_valid = await verify_otp(phone_number, wrong_otp)

        # Assert
        assert is_valid is False
        # Should NOT delete OTP on failed verification
        mock_del.assert_not_called()


@pytest.mark.asyncio
async def test_verify_otp_expired():
    """Test OTP verification when OTP has expired."""
    from app.services.otp_service import verify_otp

    phone_number = "+1234567890"
    otp = "123456"

    with patch('app.services.otp_service.get_value', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None  # OTP expired/not found

        # Act
        is_valid = await verify_otp(phone_number, otp)

        # Assert
        assert is_valid is False


@pytest.mark.asyncio
async def test_get_otp_key_format():
    """Test that OTP Redis key is formatted correctly."""
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
    """Test that send_otp generates OTP and stores it (in testing env, SMS is skipped)."""
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
    from app.services.otp_service import check_rate_limit

    phone_number = "+9990000001"  # unique phone to avoid interference

    with patch('app.services.otp_service.get_value', new_callable=AsyncMock) as mock_get, \
         patch('app.services.otp_service.set_with_expiry', new_callable=AsyncMock) as mock_set:
        mock_get.return_value = "2"  # 2 OTPs sent recently
        mock_set.return_value = True

        # Act - below limit
        from unittest.mock import patch as _patch
        with _patch('app.services.otp_service.settings') as mock_settings:
            mock_settings.ENVIRONMENT = "production"  # force rate limiting on
            mock_settings.OTP_EXPIRY_SECONDS = 300
            mock_settings.OTP_LENGTH = 6
            can_send = await check_rate_limit(phone_number, max_attempts=3)

        # Assert
        assert can_send is True  # 2 < 3, can still send

        # Test when limit reached
        mock_get.return_value = "3"
        with _patch('app.services.otp_service.settings') as mock_settings:
            mock_settings.ENVIRONMENT = "production"
            mock_settings.OTP_EXPIRY_SECONDS = 300
            mock_settings.OTP_LENGTH = 6
            can_send = await check_rate_limit(phone_number, max_attempts=3)
        assert can_send is False  # 3 >= 3, cannot send
