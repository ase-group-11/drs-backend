# File: app/auth/jwt_handler.py
"""
JWT token handler for authentication.

Provides:
- Access token creation (short-lived, ~30 minutes)
- Refresh token creation (long-lived, ~7 days)
- Token validation and decoding
- User ID extraction from tokens

Security features:
- HS256 algorithm (HMAC with SHA-256)
- Token expiration
- Type validation (access vs refresh)
- Signature verification
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from jose import JWTError, jwt

from app.core.config import settings


def create_access_token(
    user_id: str,
    user_type: str,
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create JWT access token.
    
    Access tokens are short-lived (default 30 minutes) and used
    for authentication on each API request.
    
    Args:
        user_id: Unique identifier for the user
        user_type: Type of user ("user" or "emergency_team")
        expires_delta: Optional custom expiration time
        
    Returns:
        str: Encoded JWT access token
        
    Example:
        >>> token = create_access_token(user_id="123", user_type="user")
        >>> # Use token in Authorization header: Bearer {token}
    """
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    
    payload = {
        "sub": user_id,  # Subject (user ID)
        "type": "access",  # Token type
        "user_type": user_type,  # User or emergency team
        "exp": expire,  # Expiration time
        "iat": datetime.utcnow(),  # Issued at time
    }
    
    encoded_jwt = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )
    
    return encoded_jwt


def create_refresh_token(
    user_id: str,
    user_type: str,
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create JWT refresh token.
    
    Refresh tokens are long-lived (default 7 days) and used
    to obtain new access tokens without re-authentication.
    
    Args:
        user_id: Unique identifier for the user
        expires_delta: Optional custom expiration time
        
    Returns:
        str: Encoded JWT refresh token
        
    Example:
        >>> token = create_refresh_token(user_id="123")
        >>> # Store in secure HTTP-only cookie or secure storage
    """
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
    
    payload = {
        "sub": user_id,
        "type": "refresh",  # Refresh token type
        "user_type": user_type,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    
    encoded_jwt = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )
    
    return encoded_jwt


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Decode and validate JWT token.
    
    Validates:
    - Signature (uses SECRET_KEY)
    - Expiration time
    - Token structure
    
    Args:
        token: JWT token string
        
    Returns:
        Optional[Dict]: Token payload if valid, None if invalid/expired
        
    Example:
        >>> payload = decode_token(token)
        >>> if payload:
        >>>     user_id = payload["sub"]
        >>>     token_type = payload["type"]
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        # Token is invalid, expired, or malformed
        return None


def get_user_id_from_token(token: str) -> Optional[str]:
    """
    Extract user ID from JWT token.
    
    Args:
        token: JWT token string
        
    Returns:
        Optional[str]: User ID if valid token, None otherwise
        
    Example:
        >>> user_id = get_user_id_from_token(token)
        >>> if user_id:
        >>>     user = await get_user(user_id)
    """
    payload = decode_token(token)
    if payload:
        return payload.get("sub")
    return None


def verify_token_type(token: str, expected_type: str) -> bool:
    """
    Verify that token is of expected type (access or refresh).
    
    Args:
        token: JWT token string
        expected_type: Expected token type ("access" or "refresh")
        
    Returns:
        bool: True if token is valid and of expected type
        
    Example:
        >>> # Ensure token is refresh token before issuing new access token
        >>> if verify_token_type(token, "refresh"):
        >>>     new_access_token = create_access_token(user_id, user_type)
    """
    payload = decode_token(token)
    if payload:
        return payload.get("type") == expected_type
    return False