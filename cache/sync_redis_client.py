# Sync Redis client for disaster reporting (uses sync database)
import redis
from typing import Optional
from app.core.config import settings

# Simple sync Redis client
try:
    redis_client = redis.Redis(
        host=settings.REDIS_HOST if hasattr(settings, 'REDIS_HOST') else 'localhost',
        port=settings.REDIS_PORT if hasattr(settings, 'REDIS_PORT') else 6379,
        db=settings.REDIS_DB if hasattr(settings, 'REDIS_DB') else 0,
        decode_responses=True
    )
    # Test connection
    redis_client.ping()
except Exception as e:
    print(f"Redis connection failed: {e}")
    # Fallback to in-memory dict
    class DictRedis:
        def __init__(self):
            self._store = {}

        def get(self, key):
            return self._store.get(key)

        def set(self, key, value):
            self._store[key] = value
            return True

        def setex(self, key, time, value):
            self._store[key] = value
            return True

        def delete(self, key):
            return self._store.pop(key, None) is not None

    redis_client = DictRedis()
