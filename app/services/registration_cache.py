# File: app/services/registration_cache.py
"""
Registration data cache service - WITH FALLBACK SUPPORT

UPDATED: Uses fallback-enabled Redis helpers from redis_client_FALLBACK.py
Works even when Redis is down!
"""

from typing import Optional, Dict, Any
import json
import logging

# Use fallback-enabled helper functions
from cache.redis_client import (
    set_with_expiry,
    get_value,
    delete_key
)

logger = logging.getLogger(__name__)


def _get_registration_cache_key(phone_number: str) -> str:
    """
    Get Redis key for registration cache.
    
    Args:
        phone_number: User's phone number
        
    Returns:
        str: Redis key in format "reg_cache:+1234567890"
    """
    return f"reg_cache:{phone_number}"


# async def store_registration_data(
#     phone_number: str,
#     full_name: str,
#     email: Optional[str] = None,
#     expiry_seconds: int = None
# ) -> bool:
#     """
#     Store registration data temporarily (with automatic fallback).
    
#     Uses set_with_expiry which automatically handles:
#     - Redis if available
#     - In-memory cache if Redis is down
#     """
#     if expiry_seconds is None:
#         # Default: 10 minutes (longer than OTP expiry to allow retry)
#         expiry_seconds = 600
    
#     logger.info(f"💾 Storing registration data for {phone_number}")
    
#     try:
#         key = _get_registration_cache_key(phone_number)
        
#         # Store as JSON
#         data = {
#             "phone_number": phone_number,
#             "full_name": full_name,
#             "email": email
#         }
        
#         json_data = json.dumps(data)
        
#         # Automatically uses fallback if Redis is down
#         await set_with_expiry(key, json_data, expiry_seconds)
#         logger.debug(f"✅ Registration data stored (Redis or fallback)")
        
#         return True
        
#     except Exception as e:
#         logger.error(f"❌ Failed to store registration data: {e}")
#         raise


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
    Store registration data temporarily (with automatic fallback).
    
    UPDATED: Now supports both user and emergency team registration data.
    
    Uses set_with_expiry which automatically handles:
    - Redis if available
    - In-memory cache if Redis is down
    """
    if expiry_seconds is None:
        expiry_seconds = 600
    
    logger.info(f"💾 Storing registration data for {phone_number}")
    
    try:
        key = _get_registration_cache_key(phone_number)
        
        # Store as JSON with all fields
        data = {
            "phone_number": phone_number,
            "full_name": full_name,
            "email": email
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
        
        # Automatically uses fallback if Redis is down
        await set_with_expiry(key, json_data, expiry_seconds)
        logger.debug(f"✅ Registration data stored (Redis or fallback)")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to store registration data: {e}")
        raise


async def get_registration_data(phone_number: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve registration data (with automatic fallback).
    
    Uses get_value which automatically handles:
    - Redis if available
    - In-memory cache if Redis is down
    """
    logger.info(f"🔍 Retrieving registration data for {phone_number}")
    
    try:
        key = _get_registration_cache_key(phone_number)
        
        # Automatically uses fallback if Redis is down
        json_data = await get_value(key)
        
        if not json_data:
            logger.warning(f"⚠️  No registration data found for {phone_number}")
            return None
        
        data = json.loads(json_data)
        logger.debug(f"✅ Retrieved data: {data.get('full_name')}")
        return data
        
    except json.JSONDecodeError:
        logger.error(f"❌ Invalid JSON in registration cache for {phone_number}")
        return None
    except Exception as e:
        logger.error(f"❌ Failed to retrieve registration data: {e}")
        return None


async def delete_registration_data(phone_number: str) -> bool:
    """
    Delete registration data (with automatic fallback).
    
    Uses delete_key which automatically handles:
    - Redis if available
    - In-memory cache if Redis is down
    """
    logger.info(f"🗑️  Deleting registration data for {phone_number}")
    
    try:
        key = _get_registration_cache_key(phone_number)
        
        # Automatically uses fallback if Redis is down
        await delete_key(key)
        logger.debug("✅ Registration data deleted")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to delete registration data: {e}")
        return False


# async def update_registration_data(
#     phone_number: str,
#     full_name: Optional[str] = None,
#     email: Optional[str] = None
# ) -> bool:
#     """
#     Update existing registration data (with automatic fallback).
    
#     Useful if user wants to modify data before verification.
#     """
#     logger.info(f"🔄 Updating registration data for {phone_number}")
    
#     try:
#         # Get existing data
#         data = await get_registration_data(phone_number)
        
#         if not data:
#             logger.warning(f"⚠️  No existing data to update for {phone_number}")
#             return False
        
#         # Update fields
#         if full_name is not None:
#             data["full_name"] = full_name
#         if email is not None:
#             data["email"] = email
        
#         # Store updated data
#         await store_registration_data(
#             phone_number=data["phone_number"],
#             full_name=data["full_name"],
#             email=data.get("email")
#         )
        
#         logger.debug("✅ Registration data updated")
#         return True
        
#     except Exception as e:
#         logger.error(f"❌ Failed to update registration data: {e}")
#         return False


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
    Update existing registration data (with automatic fallback).
    
    UPDATED: Supports emergency team fields.
    """
    logger.info(f"🔄 Updating registration data for {phone_number}")
    
    try:
        # Get existing data
        data = await get_registration_data(phone_number)
        
        if not data:
            logger.warning(f"⚠️  No existing data to update for {phone_number}")
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
        
        logger.debug("✅ Registration data updated")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to update registration data: {e}")
        return False