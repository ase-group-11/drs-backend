from fastapi import Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import SessionLocal
from app.models import User
from app.core.cache import redis_client

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> User:
    """
    Authentication dependency using OTP-based session tokens.

    Flow:
    1. Client includes header: Authorization: Bearer <session_token>
    2. Validate session_token exists in Redis
    3. Extract user_id from Redis value
    4. Return User object from database

    Note: This is temporary auth until JWT is implemented in Phase 2
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Parse Bearer token
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise ValueError("Invalid authentication scheme")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate session token in Redis
    session_key = f"session:{token}"
    user_id_str = redis_client.get(session_key)

    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user from database
    user_id = int(user_id_str)
    user = db.query(User).filter(User.user_id == user_id).first()

    if not user:
        # Cleanup invalid session
        redis_client.delete(session_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user