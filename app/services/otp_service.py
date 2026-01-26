# File: app/services/otp_service.py
"""
OTP (One-Time Password) service for user authentication.

Provides:
- OTP generation (6-digit codes)
- OTP storage in Redis with TTL
- OTP verification
- Rate limiting to prevent abuse
- Integration with Twilio SMS service
"""

import secrets
from typing import Optional

from app.db.redis_client import get_redis_client
from app.core.config import settings


def generate_otp(length: int = None) -> str:
    """
    Generate a random numeric OTP code.
    
    Uses secrets module for cryptographically strong random numbers.
    
    Args:
        length: OTP length (default from settings, typically 6)
        
    Returns:
        str: Numeric OTP code
        
    Example:
        >>> otp = generate_otp()
        >>> print(otp)
        '123456'
    """
    if length is None:
        length = settings.OTP_LENGTH
    
    # Generate random number with specified length
    # Range: 10^(length-1) to 10^length - 1
    # For 6 digits: 100000 to 999999
    min_value = 10 ** (length - 1)
    max_value = (10 ** length) - 1
    
    otp_number = secrets.randbelow(max_value - min_value + 1) + min_value
    
    return str(otp_number)


def _get_otp_key(phone_number: str) -> str:
    """
    Get Redis key for OTP storage.
    
    Args:
        phone_number: User's phone number
        
    Returns:
        str: Redis key in format "otp:+1234567890"
    """
    return f"otp:{phone_number}"


def _get_rate_limit_key(phone_number: str) -> str:
    """
    Get Redis key for rate limiting.
    
    Args:
        phone_number: User's phone number
        
    Returns:
        str: Redis key in format "otp_rate:+1234567890"
    """
    return f"otp_rate:{phone_number}"


async def store_otp(phone_number: str, otp: str, expiry_seconds: int = None) -> bool:
    """
    Store OTP in Redis with TTL.
    
    Args:
        phone_number: User's phone number
        otp: OTP code to store
        expiry_seconds: TTL in seconds (default from settings, typically 300 = 5 min)
        
    Returns:
        bool: True if stored successfully
        
    Example:
        >>> await store_otp("+1234567890", "123456")
        True
    """
    if expiry_seconds is None:
        expiry_seconds = settings.OTP_EXPIRY_SECONDS
    
    redis = await get_redis_client()
    key = _get_otp_key(phone_number)
    
    # Store OTP with expiry
    await redis.setex(key, expiry_seconds, otp)
    
    return True


async def verify_otp(phone_number: str, otp: str) -> bool:
    """
    Verify OTP code.
    
    Checks if the provided OTP matches the one stored in Redis.
    If valid, the OTP is deleted (one-time use).
    
    Args:
        phone_number: User's phone number
        otp: OTP code to verify
        
    Returns:
        bool: True if OTP is valid, False otherwise
        
    Example:
        >>> is_valid = await verify_otp("+1234567890", "123456")
        >>> if is_valid:
        >>>     print("OTP verified!")
    """
    redis = await get_redis_client()
    key = _get_otp_key(phone_number)
    
    # Get stored OTP
    stored_otp = await redis.get(key)
    
    if not stored_otp:
        # OTP expired or not found
        return False
    
    # Compare OTPs
    if stored_otp == otp:
        # Valid OTP - delete it (one-time use)
        await redis.delete(key)
        return True
    
    # Invalid OTP
    return False


async def check_rate_limit(
    phone_number: str,
    max_attempts: int = 3,
    window_seconds: int = 3600
) -> bool:
    """
    Check if user has exceeded OTP request rate limit.
    
    Prevents abuse by limiting OTP requests per time window.
    
    Args:
        phone_number: User's phone number
        max_attempts: Maximum OTP requests allowed in time window
        window_seconds: Time window in seconds (default 1 hour)
        
    Returns:
        bool: True if under limit (can send), False if exceeded
        
    Example:
        >>> can_send = await check_rate_limit("+1234567890")
        >>> if not can_send:
        >>>     raise Exception("Too many OTP requests, please try later")
    """
    redis = await get_redis_client()
    key = _get_rate_limit_key(phone_number)
    
    # Get current count
    count_str = await redis.get(key)
    current_count = int(count_str) if count_str else 0
    
    if current_count >= max_attempts:
        # Rate limit exceeded
        return False
    
    # Increment counter
    if current_count == 0:
        # First request - set with expiry
        await redis.setex(key, window_seconds, "1")
    else:
        # Increment existing counter
        await redis.incr(key)
    
    return True


async def send_otp_code(phone_number: str) -> Optional[str]:
    """
    Generate OTP, store it, and send via SMS.
    
    This is the main function to send an OTP to a user.
    
    Args:
        phone_number: User's phone number
        
    Returns:
        Optional[str]: OTP code if successful, None if rate limited
        
    Raises:
        Exception: If SMS sending fails
        
    Example:
        >>> otp = await send_otp_code("+1234567890")
        >>> if otp:
        >>>     print(f"OTP {otp} sent successfully")
    """
    # Check rate limit
    can_send = await check_rate_limit(phone_number)
    if not can_send:
        return None
    
    # Generate OTP
    otp = generate_otp()
    
    # Store in Redis
    await store_otp(phone_number, otp)
    
    # Send via SMS (import here to avoid circular dependency)
    from app.services.twilio_service import send_sms
    
    message = f"Your ASE verification code is: {otp}. Valid for {settings.OTP_EXPIRY_SECONDS // 60} minutes."
    
    success = await send_sms(phone_number, message)
    
    if not success:
        # Failed to send SMS - delete OTP
        redis = await get_redis_client()
        await redis.delete(_get_otp_key(phone_number))
        raise Exception("Failed to send OTP via SMS")
    
    return otp


async def delete_otp(phone_number: str) -> bool:
    """
    Delete OTP from Redis.
    
    Useful for manual OTP invalidation.
    
    Args:
        phone_number: User's phone number
        
    Returns:
        bool: True if deleted
    """
    redis = await get_redis_client()
    key = _get_otp_key(phone_number)
    await redis.delete(key)
    return True