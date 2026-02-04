#!/usr/bin/env python3
"""
Clear OTP rate limits from Redis

Usage:
    python clear_rate_limits.py +918125019220
    python clear_rate_limits.py all
"""

import sys
import asyncio

async def clear_rate_limit(phone_number: str):
    """Clear rate limit for specific phone number."""
    sys.path.insert(0, '.')
    
    from app.db.redis_client import get_redis_client
    
    redis = await get_redis_client()
    key = f"otp_rate:{phone_number}"
    
    deleted = await redis.delete(key)
    
    if deleted:
        print(f"✅ Rate limit cleared for {phone_number}")
    else:
        print(f"⚠️  No rate limit found for {phone_number}")
    
    await redis.aclose()


async def clear_all_rate_limits():
    """Clear all rate limits."""
    sys.path.insert(0, '.')
    
    from app.db.redis_client import get_redis_client
    
    redis = await get_redis_client()
    
    # Find all rate limit keys
    keys = []
    cursor = 0
    
    while True:
        cursor, batch = await redis.scan(cursor, match="otp_rate:*", count=100)
        keys.extend(batch)
        if cursor == 0:
            break
    
    if keys:
        deleted = await redis.delete(*keys)
        print(f"✅ Cleared {deleted} rate limits")
    else:
        print("⚠️  No rate limits found")
    
    await redis.aclose()


async def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python clear_rate_limits.py +918125019220")
        print("  python clear_rate_limits.py all")
        return
    
    arg = sys.argv[1]
    
    if arg.lower() == "all":
        await clear_all_rate_limits()
    else:
        await clear_rate_limit(arg)


if __name__ == "__main__":
    asyncio.run(main())