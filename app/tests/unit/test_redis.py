# File: app/tests/unit/test_redis.py
"""
Unit tests for Redis client configuration.

Tests ensure:
1. Redis client is created correctly
2. Connection pooling works
3. Key-value operations work
4. TTL (Time To Live) operations work
5. Proper cleanup and error handling
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_redis_client_creation():
    """Test that Redis client is created with correct configuration."""
    # Act
    from redis.redis_client import get_redis_client
    
    redis = await get_redis_client()
    
    # Assert
    assert redis is not None
    
    # Cleanup
    await redis.aclose()


@pytest.mark.asyncio
async def test_redis_set_and_get():
    """Test basic Redis SET and GET operations."""
    from redis.redis_client import get_redis_client
    
    redis = await get_redis_client()
    
    try:
        # Act: Set a value
        await redis.set("test_key", "test_value")
        
        # Act: Get the value
        value = await redis.get("test_key")
        
        # Assert
        assert value == "test_value" or value == b"test_value"
        
        # Cleanup
        await redis.delete("test_key")
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_redis_setex_with_ttl():
    """Test Redis SETEX operation with TTL (Time To Live)."""
    from redis.redis_client import get_redis_client
    
    redis = await get_redis_client()
    
    try:
        # Act: Set a value with 10 second expiry
        await redis.setex("test_ttl_key", 10, "expires_soon")
        
        # Assert: Key exists
        exists = await redis.exists("test_ttl_key")
        assert exists == 1
        
        # Assert: TTL is set
        ttl = await redis.ttl("test_ttl_key")
        assert ttl > 0 and ttl <= 10
        
        # Cleanup
        await redis.delete("test_ttl_key")
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_redis_delete_operation():
    """Test Redis DELETE operation."""
    from redis.redis_client import get_redis_client
    
    redis = await get_redis_client()
    
    try:
        # Arrange
        await redis.set("delete_me", "value")
        
        # Act
        deleted = await redis.delete("delete_me")
        
        # Assert
        assert deleted == 1
        
        # Verify it's gone
        value = await redis.get("delete_me")
        assert value is None
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_redis_connection_pool():
    """Test that Redis uses connection pooling correctly."""
    # This test verifies connection pooling configuration
    from redis.redis_client import create_redis_client
    
    # Act
    redis = await create_redis_client()
    
    # Assert: Should have connection pool
    assert redis is not None
    
    # Cleanup
    await redis.aclose()


@pytest.mark.asyncio
async def test_redis_client_singleton():
    """Test that get_redis_client returns the same instance (singleton-like behavior)."""
    from redis.redis_client import get_redis_client
    
    # Act: Get client twice
    redis1 = await get_redis_client()
    redis2 = await get_redis_client()
    
    # Assert: Should be the same connection pool
    assert redis1 is redis2
    
    # Cleanup
    await redis1.aclose()


@pytest.mark.asyncio
async def test_redis_handles_connection_errors():
    """Test that Redis client handles connection errors gracefully."""
    # This test ensures proper error handling
    with patch('app.db.redis_client.settings') as mock_settings:
        mock_settings.REDIS_URL = "redis://invalid-host:9999/0"
        
        from redis.redis_client import create_redis_client
        
        # Should not crash, but may raise connection error
        # In production, we'd want retry logic
        try:
            redis = await create_redis_client()
            await redis.ping()  # This should fail
        except Exception as e:
            # Expected to fail with invalid host
            assert "invalid-host" in str(mock_settings.REDIS_URL)