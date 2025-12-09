import redis
import logging
import time
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)

class InMemoryRedis:
    """
    A temporary in-memory replacement for Redis.
    Used when a real Redis server is not available during development.
    """
    def __init__(self):
        self.store = {}
        # Log this as a warning so it stands out in the logs
        logger.warning("="*60)
        logger.warning("⚠️  REDIS CONNECTION FAILED. USING IN-MEMORY FALLBACK.")
        logger.warning("    Data will be lost when you restart the server.")
        logger.warning("="*60)

    def setex(self, name: str, time_seconds: int, value: str):
        """Sets a key with an expiration time."""
        expiry = time.time() + time_seconds
        self.store[name] = {"value": value, "expiry": expiry}
        
        # --- LOG TO CONSOLE & FILE ---
        # Using logger.info ensures this is saved to app.log for later debugging
        logger.info(f"👉 [MOCK SMS] To: {name} | OTP: {value} | Expires: {time_seconds}s")
        
        return True

    def get(self, name: str) -> Optional[str]:
        """Gets a key, returning None if it doesn't exist or has expired."""
        data = self.store.get(name)
        if not data:
            return None
        
        # Check if expired
        if time.time() > data["expiry"]:
            del self.store[name]
            return None
            
        return data["value"]

    def delete(self, name: str):
        """Deletes a key."""
        if name in self.store:
            del self.store[name]
            return 1
        return 0

def get_redis_client():
    try:
        # Attempt to connect to Real Redis with a 1-second timeout
        client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=1
        )
        client.ping() # Force a connection check
        logger.info("✅ Connected to Real Redis")
        return client
    except redis.ConnectionError:
        # Fallback if Redis is down
        return InMemoryRedis()

# Initialize the client (Real or Mock)
redis_client = get_redis_client()