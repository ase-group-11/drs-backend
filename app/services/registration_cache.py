# # File: app/services/registration_cache.py
# """
# Registration data cache service.

# Temporarily stores registration data in Redis between OTP send and verification.
# This solves the problem of needing full_name and email during OTP verification.

# Flow:
# 1. User submits registration (phone, name, email) → Store in Redis → Send OTP
# 2. User submits OTP → Retrieve data from Redis → Create account → Delete cache
# """

# from typing import Optional, Dict, Any
# import json

# from app.db.redis_client import get_redis_client
# from app.core.config import settings


# def _get_registration_cache_key(phone_number: str) -> str:
#     """
#     Get Redis key for registration cache.
    
#     Args:
#         phone_number: User's phone number
        
#     Returns:
#         str: Redis key in format "reg_cache:+1234567890"
#     """
#     return f"reg_cache:{phone_number}"


# async def store_registration_data(
#     phone_number: str,
#     full_name: str,
#     email: Optional[str] = None,
#     expiry_seconds: int = None
# ) -> bool:
#     """
#     Store registration data temporarily in Redis.
    
#     Data is stored until OTP verification or expiry (whichever comes first).
    
#     Args:
#         phone_number: User's phone number
#         full_name: User's full name
#         email: Optional email address
#         expiry_seconds: TTL in seconds (default: 10 minutes)
        
#     Returns:
#         bool: True if stored successfully
        
#     Example:
#         >>> await store_registration_data(
#         ...     "+1234567890",
#         ...     "John Doe",
#         ...     "john@example.com"
#         ... )
#         True
#     """
#     if expiry_seconds is None:
#         # Default: 10 minutes (longer than OTP expiry to allow retry)
#         expiry_seconds = 600
    
#     redis = await get_redis_client()
#     key = _get_registration_cache_key(phone_number)
    
#     # Store as JSON
#     data = {
#         "phone_number": phone_number,
#         "full_name": full_name,
#         "email": email
#     }
    
#     json_data = json.dumps(data)
#     await redis.setex(key, expiry_seconds, json_data)
    
#     return True


# async def get_registration_data(phone_number: str) -> Optional[Dict[str, Any]]:
#     """
#     Retrieve registration data from Redis.
    
#     Args:
#         phone_number: User's phone number
        
#     Returns:
#         Optional[Dict]: Registration data if found, None otherwise
        
#     Example:
#         >>> data = await get_registration_data("+1234567890")
#         >>> if data:
#         ...     print(data["full_name"])
#         "John Doe"
#     """
#     redis = await get_redis_client()
#     key = _get_registration_cache_key(phone_number)
    
#     json_data = await redis.get(key)
    
#     if not json_data:
#         return None
    
#     try:
#         return json.loads(json_data)
#     except json.JSONDecodeError:
#         return None


# async def delete_registration_data(phone_number: str) -> bool:
#     """
#     Delete registration data from Redis.
    
#     Called after successful account creation.
    
#     Args:
#         phone_number: User's phone number
        
#     Returns:
#         bool: True if deleted
#     """
#     redis = await get_redis_client()
#     key = _get_registration_cache_key(phone_number)
#     await redis.delete(key)
#     return True


# async def update_registration_data(
#     phone_number: str,
#     full_name: Optional[str] = None,
#     email: Optional[str] = None
# ) -> bool:
#     """
#     Update existing registration data.
    
#     Useful if user wants to modify data before verification.
    
#     Args:
#         phone_number: User's phone number
#         full_name: Updated full name (optional)
#         email: Updated email (optional)
        
#     Returns:
#         bool: True if updated, False if data not found
#     """
#     # Get existing data
#     data = await get_registration_data(phone_number)
    
#     if not data:
#         return False
    
#     # Update fields
#     if full_name is not None:
#         data["full_name"] = full_name
#     if email is not None:
#         data["email"] = email
    
#     # Store updated data
#     await store_registration_data(
#         phone_number=data["phone_number"],
#         full_name=data["full_name"],
#         email=data.get("email")
#     )
    
#     return True


# File: app/services/registration_cache.py
"""
Registration data cache service.

UPDATED: Now supports both user and emergency team registration data.

Temporarily stores registration data in Redis between OTP send and verification.
"""

from typing import Optional, Dict, Any
import json
import logging

from app.db.redis_client import get_redis_client
from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_registration_cache_key(phone_number: str) -> str:
    """Get Redis key for registration cache."""
    return f"reg_cache:{phone_number}"


async def store_registration_data(
    phone_number: str,
    full_name: str,
    email: Optional[str] = None,
    # Emergency team specific fields
    password_hash: Optional[str] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    employee_id: Optional[str] = None,
    expiry_seconds: int = None
) -> bool:
    """
    Store registration data temporarily in Redis.
    
    UPDATED: Now supports emergency team registration data.
    
    Data is stored until OTP verification or expiry (whichever comes first).
    
    Args:
        phone_number: User's phone number
        full_name: User's full name
        email: Optional email address
        password_hash: Optional hashed password (for emergency team)
        role: Optional role (for emergency team)
        department: Optional department (for emergency team)
        employee_id: Optional employee ID (for emergency team)
        expiry_seconds: TTL in seconds (default: 10 minutes)
        
    Returns:
        bool: True if stored successfully
    """
    if expiry_seconds is None:
        # Default: 10 minutes (longer than OTP expiry to allow retry)
        expiry_seconds = 600
    
    logger.info(f"💾 Storing registration data for {phone_number}")
    
    try:
        redis = await get_redis_client()
        key = _get_registration_cache_key(phone_number)
        
        # Store as JSON with all fields
        data = {
            "phone_number": phone_number,
            "full_name": full_name,
            "email": email,
        }
        
        # Add emergency team specific fields if provided
        if password_hash:
            data["password_hash"] = password_hash
        if role:
            data["role"] = role
        if department:
            data["department"] = department
        if employee_id:
            data["employee_id"] = employee_id
        
        json_data = json.dumps(data)
        await redis.setex(key, expiry_seconds, json_data)
        
        logger.debug(f"✅ Registration data stored for {phone_number}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to store registration data: {e}")
        raise


async def get_registration_data(phone_number: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve registration data from Redis.
    
    Works for both user and emergency team registration.
    
    Args:
        phone_number: User's phone number
        
    Returns:
        Optional[Dict]: Registration data if found, None otherwise
    """
    logger.debug(f"💾 Retrieving registration data for {phone_number}")
    
    try:
        redis = await get_redis_client()
        key = _get_registration_cache_key(phone_number)
        
        json_data = await redis.get(key)
        
        if not json_data:
            logger.warning(f"❌ No registration data found for {phone_number}")
            return None
        
        data = json.loads(json_data)
        logger.debug(f"✅ Registration data retrieved for {phone_number}")
        return data
        
    except json.JSONDecodeError:
        logger.error(f"❌ Invalid JSON in registration cache for {phone_number}")
        return None
    except Exception as e:
        logger.error(f"❌ Failed to retrieve registration data: {e}")
        return None


async def delete_registration_data(phone_number: str) -> bool:
    """
    Delete registration data from Redis.
    
    Called after successful account creation.
    
    Args:
        phone_number: User's phone number
        
    Returns:
        bool: True if deleted
    """
    logger.debug(f"🗑️  Deleting registration data for {phone_number}")
    
    try:
        redis = await get_redis_client()
        key = _get_registration_cache_key(phone_number)
        await redis.delete(key)
        logger.debug(f"✅ Registration data deleted for {phone_number}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to delete registration data: {e}")
        return False


async def update_registration_data(
    phone_number: str,
    full_name: Optional[str] = None,
    email: Optional[str] = None,
    password_hash: Optional[str] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    employee_id: Optional[str] = None
) -> bool:
    """
    Update existing registration data.
    
    UPDATED: Supports emergency team fields.
    
    Useful if user wants to modify data before verification.
    
    Args:
        phone_number: User's phone number
        full_name: Updated full name (optional)
        email: Updated email (optional)
        password_hash: Updated password hash (optional)
        role: Updated role (optional)
        department: Updated department (optional)
        employee_id: Updated employee ID (optional)
        
    Returns:
        bool: True if updated, False if data not found
    """
    # Get existing data
    data = await get_registration_data(phone_number)
    
    if not data:
        return False
    
    # Update fields that are provided
    if full_name is not None:
        data["full_name"] = full_name
    if email is not None:
        data["email"] = email
    if password_hash is not None:
        data["password_hash"] = password_hash
    if role is not None:
        data["role"] = role
    if department is not None:
        data["department"] = department
    if employee_id is not None:
        data["employee_id"] = employee_id
    
    # Store updated data
    await store_registration_data(
        phone_number=data["phone_number"],
        full_name=data["full_name"],
        email=data.get("email"),
        password_hash=data.get("password_hash"),
        role=data.get("role"),
        department=data.get("department"),
        employee_id=data.get("employee_id")
    )
    
    return True