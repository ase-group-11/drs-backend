# # File: app/services/otp_service.py
# """
# OTP service - UPDATED to disable rate limiting in testing mode
# """

# import secrets
# from typing import Optional
# import logging

# from app.db.redis_client import get_redis_client
# from app.core.config import settings

# logger = logging.getLogger(__name__)


# def generate_otp(length: int = None) -> str:
#     """Generate a random numeric OTP code."""
#     if length is None:
#         length = settings.OTP_LENGTH
    
#     min_value = 10 ** (length - 1)
#     max_value = (10 ** length) - 1
    
#     otp_number = secrets.randbelow(max_value - min_value + 1) + min_value
#     otp = str(otp_number)
    
#     logger.debug(f"🎲 Generated OTP: {otp[:2]}**** (length: {len(otp)})")
#     return otp


# def _get_otp_key(phone_number: str) -> str:
#     """Get Redis key for OTP storage."""
#     return f"otp:{phone_number}"


# def _get_rate_limit_key(phone_number: str) -> str:
#     """Get Redis key for rate limiting."""
#     return f"otp_rate:{phone_number}"


# async def store_otp(phone_number: str, otp: str, expiry_seconds: int = None) -> bool:
#     """Store OTP in Redis with TTL."""
#     if expiry_seconds is None:
#         expiry_seconds = settings.OTP_EXPIRY_SECONDS
    
#     logger.info(f"💾 Storing OTP in Redis for {phone_number} (TTL: {expiry_seconds}s)")
    
#     try:
#         redis = await get_redis_client()
#         key = _get_otp_key(phone_number)
        
#         await redis.setex(key, expiry_seconds, otp)
#         logger.debug(f"✅ OTP stored: key={key}, otp={otp[:2]}****")
#         return True
        
#     except Exception as e:
#         logger.error(f"❌ Failed to store OTP in Redis: {e}")
#         raise


# async def verify_otp(phone_number: str, otp: str) -> bool:
#     """Verify OTP code."""
#     logger.info(f"🔍 Verifying OTP for {phone_number}")
    
#     try:
#         redis = await get_redis_client()
#         key = _get_otp_key(phone_number)
        
#         stored_otp = await redis.get(key)
        
#         if not stored_otp:
#             logger.warning(f"❌ OTP not found or expired for {phone_number}")
#             return False
        
#         logger.debug(f"🔍 Comparing OTPs: provided={otp[:2]}****, stored={stored_otp[:2]}****")
        
#         if stored_otp == otp:
#             logger.info(f"✅ OTP verified successfully")
#             await redis.delete(key)
#             logger.debug("🗑️  OTP deleted from Redis")
#             return True
        
#         logger.warning(f"❌ OTP mismatch")
#         return False
        
#     except Exception as e:
#         logger.error(f"❌ OTP verification error: {e}")
#         return False


# async def check_rate_limit(
#     phone_number: str,
#     max_attempts: int = 3,
#     window_seconds: int = 3600
# ) -> bool:
#     """
#     Check if user has exceeded OTP request rate limit.
    
#     UPDATED: Skips rate limiting in testing environment.
#     """
#     # Skip rate limiting in testing mode
#     if settings.ENVIRONMENT == "testing":
#         logger.debug(f"🧪 TESTING mode - Rate limiting DISABLED")
#         return True
    
#     logger.debug(f"🔍 Checking rate limit for {phone_number} (max: {max_attempts}/hour)")
    
#     try:
#         redis = await get_redis_client()
#         key = _get_rate_limit_key(phone_number)
        
#         count_str = await redis.get(key)
#         current_count = int(count_str) if count_str else 0
        
#         logger.debug(f"Current OTP requests: {current_count}/{max_attempts}")
        
#         if current_count >= max_attempts:
#             logger.warning(f"❌ Rate limit exceeded: {current_count} requests")
#             return False
        
#         if current_count == 0:
#             await redis.setex(key, window_seconds, "1")
#             logger.debug("✅ First OTP request, counter initialized")
#         else:
#             await redis.incr(key)
#             logger.debug(f"✅ Counter incremented to {current_count + 1}")
        
#         return True
        
#     except Exception as e:
#         logger.error(f"❌ Rate limit check error: {e}")
#         return True


# async def send_otp_code(phone_number: str) -> Optional[str]:
#     """
#     Generate OTP, store it, and send via SMS.
    
#     UPDATED: No rate limiting in testing mode.
#     """
#     logger.info(f"📱 Starting OTP send process for {phone_number}")
#     logger.info(f"🌍 Environment: {settings.ENVIRONMENT}")
    
#     try:
#         # Step 1: Check rate limit (skipped in testing)
#         logger.debug("🔍 Step 1: Checking rate limit...")
#         can_send = await check_rate_limit(phone_number)
#         if not can_send:
#             logger.warning(f"❌ Rate limit exceeded for {phone_number}")
#             return None
#         logger.debug("✅ Rate limit OK")
        
#         # Step 2: Generate OTP
#         logger.debug("🎲 Step 2: Generating OTP...")
#         otp = generate_otp()
#         logger.debug(f"✅ OTP generated: {otp[:2]}****")
        
#         # Step 3: Store in Redis
#         logger.debug("💾 Step 3: Storing OTP in Redis...")
#         await store_otp(phone_number, otp)
#         logger.debug("✅ OTP stored")
        
#         # Step 4: Send via SMS
#         logger.info("📤 Step 4: Sending SMS...")
#         from app.services.twilio_service import send_sms
        
#         message = f"Your ASE verification code is: {otp}. Valid for {settings.OTP_EXPIRY_SECONDS // 60} minutes."
#         logger.debug(f"📝 Message: {message[:30]}...")
        
#         success = await send_sms(phone_number, message)
        
#         if not success:
#             logger.error("❌ SMS sending failed")
#             redis = await get_redis_client()
#             await redis.delete(_get_otp_key(phone_number))
#             logger.debug("🗑️  OTP deleted due to SMS failure")
#             raise Exception("SMS service failed to send OTP")
        
#         logger.info(f"✅ OTP sent successfully to {phone_number}")
#         return otp
        
#     except Exception as e:
#         logger.exception(f"❌ send_otp_code failed")
#         raise


# async def delete_otp(phone_number: str) -> bool:
#     """Delete OTP from Redis."""
#     logger.debug(f"🗑️  Deleting OTP for {phone_number}")
    
#     try:
#         redis = await get_redis_client()
#         key = _get_otp_key(phone_number)
#         await redis.delete(key)
#         logger.debug("✅ OTP deleted")
#         return True
        
#     except Exception as e:
#         logger.error(f"❌ Failed to delete OTP: {e}")
#         return False


# async def clear_rate_limit(phone_number: str) -> bool:
#     """
#     Clear rate limit for a phone number.
    
#     Useful for testing or admin overrides.
#     """
#     logger.info(f"🗑️  Clearing rate limit for {phone_number}")
    
#     try:
#         redis = await get_redis_client()
#         key = _get_rate_limit_key(phone_number)
#         await redis.delete(key)
#         logger.info("✅ Rate limit cleared")
#         return True
        
#     except Exception as e:
#         logger.error(f"❌ Failed to clear rate limit: {e}")
#         return False


# File: app/services/otp_service.py
"""
OTP service - UPDATED to use Redis fallback helpers

This version uses the helper functions from redis_client_FALLBACK.py
which automatically handle fallback to in-memory cache when Redis is down.
"""

import secrets
from typing import Optional
import logging

# Use the fallback-enabled helper functions
from redis.redis_client import (
    set_with_expiry,
    get_value,
    delete_key,
    key_exists
)
from app.core.config import settings

logger = logging.getLogger(__name__)


def generate_otp(length: int = None) -> str:
    """Generate a random numeric OTP code."""
    if length is None:
        length = settings.OTP_LENGTH
    
    min_value = 10 ** (length - 1)
    max_value = (10 ** length) - 1
    
    otp_number = secrets.randbelow(max_value - min_value + 1) + min_value
    otp = str(otp_number)
    
    logger.debug(f"🎲 Generated OTP: {otp[:2]}**** (length: {len(otp)})")
    return otp


def _get_otp_key(phone_number: str) -> str:
    """Get Redis key for OTP storage."""
    return f"otp:{phone_number}"


def _get_rate_limit_key(phone_number: str) -> str:
    """Get Redis key for rate limiting."""
    return f"otp_rate:{phone_number}"


async def store_otp(phone_number: str, otp: str, expiry_seconds: int = None) -> bool:
    """
    Store OTP with automatic Redis fallback.
    
    Uses set_with_expiry which handles:
    - Redis if available
    - In-memory cache if Redis down
    """
    if expiry_seconds is None:
        expiry_seconds = settings.OTP_EXPIRY_SECONDS
    
    logger.info(f"💾 Storing OTP for {phone_number} (TTL: {expiry_seconds}s)")
    
    try:
        key = _get_otp_key(phone_number)
        # This automatically uses fallback if Redis is down
        result = await set_with_expiry(key, otp, expiry_seconds)
        logger.debug(f"✅ OTP stored: {otp[:2]}****")
        return result
        
    except Exception as e:
        logger.error(f"❌ Failed to store OTP: {e}")
        raise


async def verify_otp(phone_number: str, otp: str) -> bool:
    """
    Verify OTP with automatic Redis fallback.
    
    Uses get_value which handles:
    - Redis if available
    - In-memory cache if Redis down
    """
    logger.info(f"🔍 Verifying OTP for {phone_number}")
    
    try:
        key = _get_otp_key(phone_number)
        
        # Get stored OTP (with automatic fallback)
        stored_otp = await get_value(key)
        
        if not stored_otp:
            logger.warning(f"❌ OTP not found or expired for {phone_number}")
            return False
        
        logger.debug(f"🔍 Comparing OTPs: provided={otp[:2]}****, stored={stored_otp[:2]}****")
        
        if stored_otp == otp:
            logger.info(f"✅ OTP verified successfully")
            # Delete OTP (one-time use)
            await delete_key(key)
            logger.debug("🗑️  OTP deleted")
            return True
        
        logger.warning(f"❌ OTP mismatch")
        return False
        
    except Exception as e:
        logger.error(f"❌ OTP verification error: {e}")
        return False


async def check_rate_limit(
    phone_number: str,
    max_attempts: int = 3,
    window_seconds: int = 3600
) -> bool:
    """
    Check rate limit with automatic fallback.
    
    UPDATED: Skips rate limiting in testing environment.
    Uses get_value and set_with_expiry for automatic fallback.
    """
    # Skip rate limiting in testing mode
    if settings.ENVIRONMENT == "testing":
        logger.debug(f"🧪 TESTING mode - Rate limiting DISABLED")
        return True
    
    logger.debug(f"🔍 Checking rate limit for {phone_number} (max: {max_attempts}/hour)")
    
    try:
        key = _get_rate_limit_key(phone_number)
        
        # Get current count (with automatic fallback)
        count_str = await get_value(key)
        current_count = int(count_str) if count_str else 0
        
        logger.debug(f"Current OTP requests: {current_count}/{max_attempts}")
        
        if current_count >= max_attempts:
            logger.warning(f"❌ Rate limit exceeded: {current_count} requests")
            return False
        
        # Increment counter
        if current_count == 0:
            await set_with_expiry(key, "1", window_seconds)
            logger.debug("✅ First OTP request, counter initialized")
        else:
            new_count = str(current_count + 1)
            await set_with_expiry(key, new_count, window_seconds)
            logger.debug(f"✅ Counter incremented to {new_count}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Rate limit check error: {e}")
        # Allow on error to not block users
        return True


async def send_otp_code(phone_number: str) -> Optional[str]:
    """
    Generate OTP, store it, and send via SMS.
    
    UPDATED: Uses fallback-enabled Redis helpers.
    Works even when Redis is down!
    """
    logger.info(f"📱 Starting OTP send process for {phone_number}")
    logger.info(f"🌍 Environment: {settings.ENVIRONMENT}")
    
    try:
        # Step 1: Check rate limit (skipped in testing, uses fallback)
        logger.debug("🔍 Step 1: Checking rate limit...")
        can_send = await check_rate_limit(phone_number)
        if not can_send:
            logger.warning(f"❌ Rate limit exceeded for {phone_number}")
            return None
        logger.debug("✅ Rate limit OK")
        
        # Step 2: Generate OTP
        logger.debug("🎲 Step 2: Generating OTP...")
        otp = generate_otp()
        logger.debug(f"✅ OTP generated: {otp[:2]}****")
        
        # Step 3: Store in Redis (with automatic fallback)
        logger.debug("💾 Step 3: Storing OTP...")
        await store_otp(phone_number, otp)
        logger.debug("✅ OTP stored (Redis or fallback cache)")
        
        # Step 4: Send via SMS
        logger.info("📤 Step 4: Sending SMS...")
        from app.services.twilio_service import send_sms
        
        message = f"Your ASE verification code is: {otp}. Valid for {settings.OTP_EXPIRY_SECONDS // 60} minutes."
        logger.debug(f"📝 Message: {message[:30]}...")
        
        success = await send_sms(phone_number, message)
        
        if not success:
            logger.error("❌ SMS sending failed")
            # Delete OTP if SMS fails
            await delete_key(_get_otp_key(phone_number))
            logger.debug("🗑️  OTP deleted due to SMS failure")
            raise Exception("SMS service failed to send OTP")
        
        logger.info(f"✅ OTP sent successfully to {phone_number}")
        return otp
        
    except Exception as e:
        logger.exception(f"❌ send_otp_code failed")
        raise


async def delete_otp(phone_number: str) -> bool:
    """Delete OTP (with automatic fallback)."""
    logger.debug(f"🗑️  Deleting OTP for {phone_number}")
    
    try:
        key = _get_otp_key(phone_number)
        await delete_key(key)
        logger.debug("✅ OTP deleted")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to delete OTP: {e}")
        return False


async def clear_rate_limit(phone_number: str) -> bool:
    """
    Clear rate limit for a phone number.
    
    Useful for testing or admin overrides.
    """
    logger.info(f"🗑️  Clearing rate limit for {phone_number}")
    
    try:
        key = _get_rate_limit_key(phone_number)
        await delete_key(key)
        logger.info("✅ Rate limit cleared")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to clear rate limit: {e}")
        return False