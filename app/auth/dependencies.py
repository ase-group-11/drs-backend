# File: app/auth/dependencies.py
"""
Authentication dependencies for FastAPI routes.

Usage in routes:
    from app.auth.dependencies import get_current_user, get_current_team_member

    @router.get("/protected")
    async def protected_route(current_user = Depends(get_current_user)):
        return {"user_id": current_user["user_id"]}

    @router.post("/admin-only")
    async def admin_route(current_user = Depends(get_current_team_member)):
        return {"team_member_id": current_user["user_id"]}
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Dict, Any

from app.auth.jwt_handler import decode_token

# This extracts "Bearer <token>" from Authorization header
bearer_scheme = HTTPBearer(auto_error=True)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> Dict[str, Any]:
    """
    Validate Bearer token and return current user info.

    Works for BOTH regular users and emergency team members.
    Returns:
        {
            "user_id": "uuid",
            "user_type": "user" or "emergency_team",
            "token_type": "access"
        }

    Raises:
        401 if token is missing, invalid, or expired
    """
    token = credentials.credentials

    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Must be an access token (not refresh)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type. Access token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "user_id": payload.get("sub"),
        "user_type": payload.get("user_type"),
        "token_type": payload.get("type"),
    }


async def get_current_team_member(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Validate that the current user is an emergency team member.

    Use this for admin/team-only endpoints (dispatch, escalate, resolve, etc.)

    Raises:
        403 if user is not an emergency team member
    """
    if current_user.get("user_type") != "emergency_team":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Emergency team access required.",
        )
    return current_user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
) -> Dict[str, Any]:
    """
    Optional auth — returns user info if token provided, None if not.

    Use for endpoints that work with or without auth (e.g., public map view).
    """
    if not credentials:
        return None

    payload = decode_token(credentials.credentials)
    if not payload:
        return None

    return {
        "user_id": payload.get("sub"),
        "user_type": payload.get("user_type"),
    }