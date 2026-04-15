# File: app/tests/unit/test_jwt_handler.py
"""
Unit tests for JWT token handling.

Tests ensure:
1. Access token generation works
2. Refresh token generation works
3. Token validation works
4. Expired tokens are rejected
5. Invalid tokens are rejected
6. Token payload is correctly encoded/decoded
"""

import pytest
from datetime import datetime, timedelta
import time


def test_create_access_token():
    """Test that access token is created with correct payload."""
    # Arrange
    from app.auth.jwt_handler import create_access_token
    
    user_id = "123"
    user_type = "user"  # or "emergency_team"
    
    # Act
    token = create_access_token(user_id=user_id, user_type=user_type)
    
    # Assert
    assert token is not None
    assert isinstance(token, str)
    assert len(token) > 50  # JWT tokens are long strings


def test_create_refresh_token():
    """Test that refresh token is created."""
    # Arrange
    from app.auth.jwt_handler import create_refresh_token

    user_id = "123"

    # Act
    token = create_refresh_token(user_id=user_id, user_type="user")
    
    # Assert
    assert token is not None
    assert isinstance(token, str)
    assert len(token) > 50


def test_decode_access_token_valid():
    """Test that valid access token can be decoded."""
    # Arrange
    from app.auth.jwt_handler import create_access_token, decode_token
    
    user_id = "123"
    user_type = "user"
    
    token = create_access_token(user_id=user_id, user_type=user_type)
    
    # Act
    payload = decode_token(token)
    
    # Assert
    assert payload is not None
    assert payload["sub"] == user_id
    assert payload["type"] == "access"
    assert payload["user_type"] == user_type
    assert "exp" in payload  # Expiration time
    assert "iat" in payload  # Issued at time


def test_decode_refresh_token_valid():
    """Test that valid refresh token can be decoded."""
    # Arrange
    from app.auth.jwt_handler import create_refresh_token, decode_token
    
    user_id = "456"
    token = create_refresh_token(user_id=user_id, user_type="user")
    
    # Act
    payload = decode_token(token)
    
    # Assert
    assert payload is not None
    assert payload["sub"] == user_id
    assert payload["type"] == "refresh"


def test_decode_token_expired():
    """Test that expired token is rejected."""
    # Arrange
    from app.auth.jwt_handler import create_access_token, decode_token
    
    # Create token that expired 1 second ago
    token = create_access_token(
        user_id="123",
        user_type="user",
        expires_delta=timedelta(seconds=-1)  # Negative delta = already expired
    )
    
    # Act
    payload = decode_token(token)
    
    # Assert
    assert payload is None  # Expired token should return None


def test_decode_token_invalid_signature():
    """Test that token with invalid signature is rejected."""
    # Arrange
    from app.auth.jwt_handler import decode_token
    
    # A token with invalid signature
    invalid_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.signature"
    
    # Act
    payload = decode_token(invalid_token)
    
    # Assert
    assert payload is None


def test_decode_token_malformed():
    """Test that malformed token is rejected."""
    # Arrange
    from app.auth.jwt_handler import decode_token
    
    malformed_token = "this.is.not.a.valid.jwt.token"
    
    # Act
    payload = decode_token(malformed_token)
    
    # Assert
    assert payload is None


def test_token_contains_expiration():
    """Test that tokens have expiration time."""
    # Arrange
    from app.auth.jwt_handler import create_access_token, decode_token
    
    token = create_access_token(user_id="123", user_type="user")
    
    # Act
    payload = decode_token(token)
    
    # Assert
    assert "exp" in payload
    exp_timestamp = payload["exp"]
    
    # Expiration should be in the future
    current_time = datetime.utcnow().timestamp()
    assert exp_timestamp > current_time


def test_access_token_custom_expiry():
    """Test that custom expiry time works for access tokens."""
    # Arrange
    from app.auth.jwt_handler import create_access_token, decode_token
    
    custom_expiry = timedelta(minutes=5)
    token = create_access_token(
        user_id="123",
        user_type="user",
        expires_delta=custom_expiry
    )
    
    # Act
    payload = decode_token(token)
    
    # Assert
    exp_timestamp = payload["exp"]
    iat_timestamp = payload["iat"]
    
    # Difference should be approximately 5 minutes (300 seconds)
    time_diff = exp_timestamp - iat_timestamp
    assert 290 < time_diff < 310  # Allow small variance


def test_token_type_field():
    """Test that access and refresh tokens have different type fields."""
    # Arrange
    from app.auth.jwt_handler import (
        create_access_token,
        create_refresh_token,
        decode_token
    )
    
    access_token = create_access_token(user_id="123", user_type="user")
    refresh_token = create_refresh_token(user_id="123", user_type="user")
    
    # Act
    access_payload = decode_token(access_token)
    refresh_payload = decode_token(refresh_token)
    
    # Assert
    assert access_payload["type"] == "access"
    assert refresh_payload["type"] == "refresh"


def test_get_user_id_from_token():
    """Test helper function to extract user ID from token."""
    # Arrange
    from app.auth.jwt_handler import create_access_token, get_user_id_from_token
    
    user_id = "test-user-123"
    token = create_access_token(user_id=user_id, user_type="user")
    
    # Act
    extracted_user_id = get_user_id_from_token(token)
    
    # Assert
    assert extracted_user_id == user_id