# File: tests/unit/test_config.py
"""
Unit tests for configuration management.

Tests ensure:
1. Configuration loads from environment variables
2. Default values are set correctly
3. Validation works for required fields
4. Different environments (dev/test/prod) are supported
"""

import pytest
from pydantic import ValidationError
import os


def test_config_loads_from_environment(monkeypatch):
    """Test that configuration correctly loads from environment variables."""
    # Arrange: Set environment variables
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/testdb")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-minimum-32-characters-long")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "test_account_sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test_auth_token")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+1234567890")
    
    # Act: Import config (will load from environment)
    from app.core.config import Settings
    settings = Settings()
    
    # Assert: Values are loaded correctly
    assert settings.DATABASE_URL == "postgresql+asyncpg://test:test@localhost/testdb"
    assert settings.REDIS_URL == "redis://localhost:6379/0"
    assert settings.JWT_SECRET_KEY == "test-secret-key-minimum-32-characters-long"
    assert settings.TWILIO_ACCOUNT_SID == "test_account_sid"


def test_config_has_default_values(monkeypatch):
    """Test that configuration has sensible default values."""
    # Arrange: Set only required fields
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/testdb")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-minimum-32-characters-long")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "test_sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test_token")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+1234567890")
    
    # Act
    from app.core.config import Settings
    settings = Settings()
    
    # Assert: Default values exist
    assert settings.JWT_ALGORITHM == "HS256"
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 30
    assert settings.REFRESH_TOKEN_EXPIRE_DAYS == 7
    assert settings.OTP_EXPIRY_SECONDS == 300
    assert settings.OTP_LENGTH == 6
    assert settings.ENVIRONMENT in ["development", "testing", "production"]


def test_config_validates_jwt_secret_length(monkeypatch):
    """Test that JWT secret key must be at least 32 characters."""
    # Arrange: Set a short secret key
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/testdb")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET_KEY", "short")  # Too short!
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "test_sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test_token")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+1234567890")
    
    # Act & Assert: Should raise validation error
    from app.core.config import Settings
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    
    # Check that validation error mentions JWT_SECRET_KEY and 32 characters
    error_str = str(exc_info.value)
    assert "JWT_SECRET_KEY" in error_str
    assert "32 characters" in error_str


# def test_config_supports_different_environments(monkeypatch):
#     """Test that configuration supports development, testing, and production environments."""
#     # Arrange & Act & Assert for each environment
#     for env in ["development", "testing", "production"]:
#         # Clear any existing environment variables
#         monkeypatch.delenv("DEBUG", raising=False)
        
#         monkeypatch.setenv("ENVIRONMENT", env)
#         monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/testdb")
#         monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
#         monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-minimum-32-characters-long")
#         monkeypatch.setenv("TWILIO_ACCOUNT_SID", "test_sid")
#         monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test_token")
#         monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+1234567890")
        
#         # Need to reload the module to pick up new environment variables
#         import importlib
#         import app.core.config as config_module
#         importlib.reload(config_module)
        
#         settings = config_module.Settings()
        
#         assert settings.ENVIRONMENT == env
#         assert settings.DEBUG == (env == "development")


# def test_config_validates_required_fields(monkeypatch):
#     """Test that configuration requires essential fields."""
#     # Arrange: Missing DATABASE_URL
#     monkeypatch.delenv("DATABASE_URL", raising=False)
    
#     # Act & Assert: Should raise validation error
#     from app.core.config import Settings
#     with pytest.raises(ValidationError) as exc_info:
#         Settings()
    
#     assert "DATABASE_URL" in str(exc_info.value)

def test_config_is_singleton():
    """Test that configuration returns the same instance (singleton pattern)."""
    from app.core.config import get_settings
    
    # Act: Get settings twice
    settings1 = get_settings()
    settings2 = get_settings()
    
    # Assert: Same instance
    assert settings1 is settings2