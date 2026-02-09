# File: tests/conftest.py
"""
Pytest configuration and shared fixtures.

Provides:
- Mock settings for testing
- Database fixtures
- Redis fixtures
- Test client fixtures
"""

import pytest
import os
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """
    Set up test environment variables before any tests run.
    This runs once per test session.
    """
    os.environ["ENVIRONMENT"] = "testing"
    os.environ["DEBUG"] = "false"
    os.environ["DATABASE_URL"] = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    os.environ["REDIS_URL"] = "redis://localhost:6379/1"
    os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-testing-minimum-32-characters-long-required"
    os.environ["JWT_ALGORITHM"] = "HS256"
    os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "30"
    os.environ["REFRESH_TOKEN_EXPIRE_DAYS"] = "7"
    os.environ["TWILIO_ACCOUNT_SID"] = "test_account_sid"
    os.environ["TWILIO_AUTH_TOKEN"] = "test_auth_token"
    os.environ["TWILIO_PHONE_NUMBER"] = "+1234567890"
    os.environ["OTP_EXPIRY_SECONDS"] = "300"
    os.environ["OTP_LENGTH"] = "6"
    
    yield
    
    # Cleanup after all tests
    # (In practice, environment variables persist, but this is good practice)


@pytest.fixture
def mock_settings():
    """
    Provide a mock Settings object for testing.
    Can be customized per test.
    """
    from app.core.config import Settings
    return Settings()


@pytest.fixture
def mock_db_session():
    """
    Provide a mock database session for unit tests.
    Avoids actual database connections in isolated unit tests.
    """
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.execute = AsyncMock()
    session.scalar = AsyncMock()
    session.add = MagicMock()
    session.delete = MagicMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()  # Added for user service tests
    
    return session


@pytest.fixture
def mock_redis_client():
    """
    Provide a mock Redis client for unit tests.
    Avoids actual Redis connections in isolated unit tests.
    """
    client = AsyncMock()
    client.set = AsyncMock()
    client.get = AsyncMock()
    client.delete = AsyncMock()
    client.exists = AsyncMock()
    client.expire = AsyncMock()
    client.setex = AsyncMock()
    
    return client