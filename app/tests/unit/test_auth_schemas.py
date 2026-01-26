# File: app/tests/unit/test_auth_schemas.py
"""
Unit tests for authentication schemas.

Tests ensure:
1. Valid data passes validation
2. Invalid data raises ValidationError
3. Phone number format validation works
4. Email validation works
5. OTP format validation works
"""

import pytest
from pydantic import ValidationError


def test_user_register_request_valid():
    """Test valid user registration request."""
    from app.schemas.auth import UserRegisterRequest
    
    # Act
    request = UserRegisterRequest(
        phone_number="+1234567890",
        full_name="John Doe",
        email="john@example.com"
    )
    
    # Assert
    assert request.phone_number == "+1234567890"
    assert request.full_name == "John Doe"
    assert request.email == "john@example.com"


def test_user_register_request_invalid_phone():
    """Test registration with invalid phone number format."""
    from app.schemas.auth import UserRegisterRequest
    
    # Act & Assert
    with pytest.raises(ValidationError) as exc_info:
        UserRegisterRequest(
            phone_number="1234567890",  # Missing +
            full_name="John Doe"
        )
    
    assert "phone_number" in str(exc_info.value).lower()


def test_user_register_request_invalid_email():
    """Test registration with invalid email format."""
    from app.schemas.auth import UserRegisterRequest
    
    # Act & Assert
    with pytest.raises(ValidationError) as exc_info:
        UserRegisterRequest(
            phone_number="+1234567890",
            full_name="John Doe",
            email="invalid-email"  # No @ or domain
        )
    
    assert "email" in str(exc_info.value).lower()


def test_user_register_request_without_email():
    """Test registration without optional email."""
    from app.schemas.auth import UserRegisterRequest
    
    # Act
    request = UserRegisterRequest(
        phone_number="+1234567890",
        full_name="John Doe"
    )
    
    # Assert
    assert request.email is None


def test_otp_verify_request_valid():
    """Test valid OTP verification request."""
    from app.schemas.auth import OTPVerifyRequest
    
    # Act
    request = OTPVerifyRequest(
        phone_number="+1234567890",
        otp="123456"
    )
    
    # Assert
    assert request.phone_number == "+1234567890"
    assert request.otp == "123456"


def test_otp_verify_request_invalid_otp_length():
    """Test OTP verification with wrong length."""
    from app.schemas.auth import OTPVerifyRequest
    
    # Act & Assert - Too short
    with pytest.raises(ValidationError):
        OTPVerifyRequest(
            phone_number="+1234567890",
            otp="123"
        )
    
    # Too long
    with pytest.raises(ValidationError):
        OTPVerifyRequest(
            phone_number="+1234567890",
            otp="1234567"
        )


def test_otp_verify_request_non_numeric():
    """Test OTP verification with non-numeric code."""
    from app.schemas.auth import OTPVerifyRequest
    
    # Act & Assert
    with pytest.raises(ValidationError) as exc_info:
        OTPVerifyRequest(
            phone_number="+1234567890",
            otp="12345a"
        )
    
    assert "digit" in str(exc_info.value).lower()


def test_user_login_request_valid():
    """Test valid login request."""
    from app.schemas.auth import UserLoginRequest
    
    # Act
    request = UserLoginRequest(phone_number="+1234567890")
    
    # Assert
    assert request.phone_number == "+1234567890"


def test_token_response_structure():
    """Test token response structure."""
    from app.schemas.auth import TokenResponse
    
    # Act
    response = TokenResponse(
        access_token="access123",
        refresh_token="refresh456",
        expires_in=1800
    )
    
    # Assert
    assert response.access_token == "access123"
    assert response.refresh_token == "refresh456"
    assert response.token_type == "bearer"
    assert response.expires_in == 1800


def test_user_response_from_dict():
    """Test user response can be created from dictionary."""
    from app.schemas.auth import UserResponse
    
    # Act
    response = UserResponse(
        id="550e8400-e29b-41d4-a716-446655440000",
        phone_number="+1234567890",
        full_name="John Doe",
        email="john@example.com",
        status="active",
        created_at="2024-01-15T10:30:00Z"
    )
    
    # Assert
    assert response.id == "550e8400-e29b-41d4-a716-446655440000"
    assert response.phone_number == "+1234567890"
    assert response.status == "active"


def test_auth_response_combines_user_and_tokens():
    """Test complete auth response."""
    from app.schemas.auth import AuthResponse, UserResponse, TokenResponse
    
    # Arrange
    user = UserResponse(
        id="550e8400-e29b-41d4-a716-446655440000",
        phone_number="+1234567890",
        full_name="John Doe",
        email="john@example.com",
        status="active",
        created_at="2024-01-15T10:30:00Z"
    )
    
    tokens = TokenResponse(
        access_token="access123",
        refresh_token="refresh456",
        expires_in=1800
    )
    
    # Act
    response = AuthResponse(user=user, tokens=tokens)
    
    # Assert
    assert response.user.phone_number == "+1234567890"
    assert response.tokens.access_token == "access123"


def test_message_response():
    """Test simple message response."""
    from app.schemas.auth import MessageResponse
    
    # Act
    response = MessageResponse(message="OTP sent successfully")
    
    # Assert
    assert response.message == "OTP sent successfully"


def test_error_response():
    """Test error response structure."""
    from app.schemas.auth import ErrorResponse
    
    # Act
    response = ErrorResponse(
        error="ValidationError",
        message="Invalid input",
        details={"field": "phone_number"}
    )
    
    # Assert
    assert response.error == "ValidationError"
    assert response.message == "Invalid input"
    assert response.details["field"] == "phone_number"


def test_phone_number_e164_validation():
    """Test E.164 phone number validation edge cases."""
    from app.schemas.auth import UserLoginRequest
    
    # Valid formats
    valid_numbers = [
        "+1234567890",      # USA
        "+447911123456",    # UK
        "+8613800138000",   # China
        "+254712345678",    # Kenya
    ]
    
    for number in valid_numbers:
        request = UserLoginRequest(phone_number=number)
        assert request.phone_number == number
    
    # Invalid formats
    invalid_numbers = [
        "1234567890",       # No +
        "+0123456789",      # Starts with 0
        "+12345",           # Too short
        "++1234567890",     # Double +
        "+1 234 567 890",   # Spaces
        "+1-234-567-890",   # Dashes
    ]
    
    for number in invalid_numbers:
        with pytest.raises(ValidationError):
            UserLoginRequest(phone_number=number)