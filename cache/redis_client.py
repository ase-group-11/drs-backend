


# File: app/db/redis_client.py
"""
Redis client configuration with fallback and logging.

UPDATED:
- Graceful fallback when Redis fails
- Comprehensive logging to file
- In-memory cache as fallback
"""

from typing import Optional
import redis.asyncio as redis
from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool
import logging
from datetime import datetime

from app.core.config import settings

# Setup logging
logger = logging.getLogger(__name__)

# Global connection pool (singleton pattern)
_redis_pool: Optional[ConnectionPool] = None
_redis_client: Optional[Redis] = None
_redis_available: bool = True

# In-memory fallback cache (when Redis is down)
_fallback_cache: dict = {}


async def create_redis_client() -> Redis:
    """
    Create Redis client with connection pooling.
    
    Configuration:
    - Async Redis client using redis-py async
    - Connection pooling for performance
    - Decode responses to strings automatically
    - Fallback to in-memory cache if Redis unavailable
    
    Returns:
        Redis: Async Redis client instance
    """
    global _redis_pool, _redis_client, _redis_available
    
    if _redis_pool is None:
        try:
            _redis_pool = ConnectionPool.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=10,
            )
            logger.info("✅ Redis connection pool created")
        except Exception as e:
            logger.error(f"Failed to create Redis pool: {e}")
            logger.warning("Falling back to in-memory cache")
            _redis_available = False
    
    if _redis_client is None and _redis_pool is not None:
        try:
            _redis_client = Redis(connection_pool=_redis_pool)
            # Test connection
            await _redis_client.ping()
            _redis_available = True
            logger.info("Redis client connected successfully")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            logger.warning("Using in-memory fallback cache")
            _redis_available = False
    
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
        try:
            await _redis_client.aclose()
            logger.info("✅ Redis client closed")
        except Exception as e:
            logger.error(f"❌ Error closing Redis client: {e}")
        _redis_client = None
    
    if _redis_pool:
        try:
            await _redis_pool.aclose()
            logger.info("✅ Redis pool closed")
        except Exception as e:
            logger.error(f"❌ Error closing Redis pool: {e}")
        _redis_pool = None


# Helper functions with fallback support

async def set_with_expiry(key: str, value: str, expiry_seconds: int) -> bool:
    """
    Set a key-value pair with automatic expiry.
    
    Falls back to in-memory cache if Redis unavailable.
    
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
    try:
        redis_client = await get_redis_client()
        
        if _redis_available and redis_client:
            result = await redis_client.setex(key, expiry_seconds, value)
            logger.debug(f"✅ Redis SET: {key} (TTL: {expiry_seconds}s)")
            return result
        else:
            # Fallback to in-memory cache
            _fallback_cache[key] = {
                "value": value,
                "expires_at": datetime.now().timestamp() + expiry_seconds
            }
            logger.warning(f"⚠️  Fallback SET: {key} (in-memory)")
            logger.info(f"📝 Logged to: {settings.LOG_FILE}")
            return True
            
    except Exception as e:
        logger.error(f"❌ Redis SET failed for {key}: {e}")
        logger.warning(f"⚠️  Using fallback cache for {key}")
        
        # Fallback
        _fallback_cache[key] = {
            "value": value,
            "expires_at": datetime.now().timestamp() + expiry_seconds
        }
        return True


async def get_value(key: str) -> Optional[str]:
    """
    Get value for a key.
    
    Falls back to in-memory cache if Redis unavailable.
    
    Args:
        key: Redis key
        
    Returns:
        Optional[str]: Value if exists, None otherwise
    """
    try:
        redis_client = await get_redis_client()
        
        if _redis_available and redis_client:
            value = await redis_client.get(key)
            logger.debug(f"✅ Redis GET: {key} = {value}")
            return value
        else:
            # Fallback to in-memory cache
            if key in _fallback_cache:
                cached = _fallback_cache[key]
                # Check expiry
                if datetime.now().timestamp() < cached["expires_at"]:
                    logger.warning(f"⚠️  Fallback GET: {key} (in-memory)")
                    return cached["value"]
                else:
                    # Expired
                    del _fallback_cache[key]
                    logger.debug(f"🗑️  Fallback expired: {key}")
                    return None
            return None
            
    except Exception as e:
        logger.error(f"❌ Redis GET failed for {key}: {e}")
        
        # Fallback
        if key in _fallback_cache:
            cached = _fallback_cache[key]
            if datetime.now().timestamp() < cached["expires_at"]:
                logger.warning(f"⚠️  Using fallback for {key}")
                return cached["value"]
            else:
                del _fallback_cache[key]
        return None


async def delete_key(key: str) -> int:
    """
    Delete a key from Redis.
    
    Falls back to in-memory cache if Redis unavailable.
    
    Args:
        key: Redis key to delete
        
    Returns:
        int: Number of keys deleted (0 or 1)
    """
    try:
        redis_client = await get_redis_client()
        
        if _redis_available and redis_client:
            result = await redis_client.delete(key)
            logger.debug(f"🗑️  Redis DELETE: {key}")
            return result
        else:
            # Fallback
            if key in _fallback_cache:
                del _fallback_cache[key]
                logger.warning(f"⚠️  Fallback DELETE: {key}")
                return 1
            return 0
            
    except Exception as e:
        logger.error(f"❌ Redis DELETE failed for {key}: {e}")
        
        # Fallback
        if key in _fallback_cache:
            del _fallback_cache[key]
            return 1
        return 0


async def key_exists(key: str) -> bool:
    """
    Check if a key exists in Redis.
    
    Falls back to in-memory cache if Redis unavailable.
    
    Args:
        key: Redis key
        
    Returns:
        bool: True if exists, False otherwise
    """
    try:
        redis_client = await get_redis_client()
        
        if _redis_available and redis_client:
            exists_count = await redis_client.exists(key)
            return exists_count > 0
        else:
            # Fallback
            if key in _fallback_cache:
                cached = _fallback_cache[key]
                # Check expiry
                if datetime.now().timestamp() < cached["expires_at"]:
                    return True
                else:
                    del _fallback_cache[key]
            return False
            
    except Exception as e:
        logger.error(f"❌ Redis EXISTS failed for {key}: {e}")
        
        # Fallback
        if key in _fallback_cache:
            cached = _fallback_cache[key]
            if datetime.now().timestamp() < cached["expires_at"]:
                return True
            else:
                del _fallback_cache[key]
        return False


async def get_ttl(key: str) -> int:
    """
    Get remaining TTL for a key.
    
    Falls back to in-memory cache if Redis unavailable.
    
    Args:
        key: Redis key
        
    Returns:
        int: TTL in seconds, -1 if no expiry, -2 if key doesn't exist
    """
    try:
        redis_client = await get_redis_client()
        
        if _redis_available and redis_client:
            return await redis_client.ttl(key)
        else:
            # Fallback
            if key in _fallback_cache:
                cached = _fallback_cache[key]
                remaining = int(cached["expires_at"] - datetime.now().timestamp())
                return max(remaining, 0)
            return -2  # Key doesn't exist
            
    except Exception as e:
        logger.error(f"❌ Redis TTL failed for {key}: {e}")
        
        # Fallback
        if key in _fallback_cache:
            cached = _fallback_cache[key]
            remaining = int(cached["expires_at"] - datetime.now().timestamp())
            return max(remaining, 0)
        return -2


async def check_redis_health() -> dict:
    """
    Check Redis health status.
    
    Returns:
        dict: Health status information
    """
    try:
        redis_client = await get_redis_client()
        
        if _redis_available and redis_client:
            await redis_client.ping()
            return {
                "status": "healthy",
                "available": True,
                "fallback_active": False,
                "fallback_cache_size": 0
            }
        else:
            return {
                "status": "degraded",
                "available": False,
                "fallback_active": True,
                "fallback_cache_size": len(_fallback_cache)
            }
            
    except Exception as e:
        logger.error(f"❌ Redis health check failed: {e}")
        return {
            "status": "unhealthy",
            "available": False,
            "fallback_active": True,
            "fallback_cache_size": len(_fallback_cache),
            "error": str(e)
        }