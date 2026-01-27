# File: app/db/redis_client.py
"""
Redis client configuration using redis-py async.

Provides:
- Async Redis client with connection pooling
- Singleton-like pattern for connection reuse
- OTP storage with TTL support
- Session management capabilities
"""

from typing import Optional
import redis.asyncio as redis
from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool

from app.core.config import settings


# Global connection pool (singleton pattern)
_redis_pool: Optional[ConnectionPool] = None
_redis_client: Optional[Redis] = None


async def create_redis_client() -> Redis:
    """
    Create Redis client with connection pooling.
    
    Configuration:
    - Async Redis client using redis-py async
    - Connection pooling for performance
    - Decode responses to strings automatically
    
    Returns:
        Redis: Async Redis client instance
    """
    global _redis_pool, _redis_client
    
    if _redis_pool is None:
        _redis_pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=10,
        )
    
    if _redis_client is None:
        _redis_client = Redis(connection_pool=_redis_pool)
    
    return _redis_client


async def get_redis_client() -> Redis:
    """
    Get Redis client instance (singleton pattern).
    
    This function ensures we reuse the same connection pool
    across the application for performance.
    
    Returns:
        Redis: Async Redis client
        
    Usage:
        redis = await get_redis_client()
        await redis.set("key", "value")
        value = await redis.get("key")
    """
    return await create_redis_client()


async def close_redis_connection():
    """
    Close Redis connection and cleanup connection pool.
    
    Should be called on application shutdown.
    """
    global _redis_pool, _redis_client
    
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
    
    if _redis_pool:
        await _redis_pool.aclose()
        _redis_pool = None


# Helper functions for common Redis operations

async def set_with_expiry(key: str, value: str, expiry_seconds: int) -> bool:
    """
    Set a key-value pair with automatic expiry.
    
    Args:
        key: Redis key
        value: Value to store
        expiry_seconds: Expiry time in seconds
        
    Returns:
        bool: True if successful
        
    Usage:
        # Store OTP that expires in 5 minutes
        await set_with_expiry("otp:1234567890", "123456", 300)
    """
    redis_client = await get_redis_client()
    return await redis_client.setex(key, expiry_seconds, value)


async def get_value(key: str) -> Optional[str]:
    """
    Get value for a key.
    
    Args:
        key: Redis key
        
    Returns:
        Optional[str]: Value if exists, None otherwise
    """
    redis_client = await get_redis_client()
    return await redis_client.get(key)


async def delete_key(key: str) -> int:
    """
    Delete a key from Redis.
    
    Args:
        key: Redis key to delete
        
    Returns:
        int: Number of keys deleted (0 or 1)
    """
    redis_client = await get_redis_client()
    return await redis_client.delete(key)


async def key_exists(key: str) -> bool:
    """
    Check if a key exists in Redis.
    
    Args:
        key: Redis key
        
    Returns:
        bool: True if exists, False otherwise
    """
    redis_client = await get_redis_client()
    exists_count = await redis_client.exists(key)
    return exists_count > 0


async def get_ttl(key: str) -> int:
    """
    Get remaining TTL for a key.
    
    Args:
        key: Redis key
        
    Returns:
        int: TTL in seconds, -1 if no expiry, -2 if key doesn't exist
    """
    redis_client = await get_redis_client()
    return await redis_client.ttl(key)