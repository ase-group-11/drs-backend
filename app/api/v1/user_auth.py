# File: app/api/v1/user_auth.py
"""
Citizen Authentication API — UC1

Two-step OTP flow:
  1. POST /auth/register      → validate phone, send OTP via Twilio
  2. POST /auth/register/verify → verify OTP, create account, return JWT

  1. POST /auth/login         → check user exists, send OTP
  2. POST /auth/login/verify  → verify OTP, return JWT

Rate limit: 3 OTP requests per hour per phone number (enforced in service).
Registration data (name, email) is stored in Redis between steps 1 and 2 —
the verify endpoint reads from cache, not from the request body.
"""

import logging
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.user_service import UserService
from app.auth.jwt_handler import decode_token, create_access_token
from app.core.config import settings
from app.schemas.auth import (
    UserRegisterRequest,
    OTPVerifyRequest,
    UserLoginRequest,
    MessageResponse,
    AuthResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Citizen Auth — UC1"])


# ─────────────────────────────────────────────────────────────────────────────
# Token refresh schema (standalone — not in schemas/auth.py)
# ─────────────────────────────────────────────────────────────────────────────

class TokenRefreshRequest(BaseModel):
    refresh_token: str


class TokenRefreshResponse(BaseModel):
    access_token: str
    token_type:   str
    expires_in:   int  # seconds


# ─────────────────────────────────────────────────────────────────────────────
# Registration (step 1 + step 2)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Register — step 1: send OTP to phone",
)
async def register_user(
    request: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Validates the phone number, stores registration data in Redis,
    and sends a 6-digit OTP via Twilio SMS.
    Rate limited to 3 requests per hour per phone number.
    Returns: message confirming OTP was sent.
    """
    service = UserService(db)
    try:
        result = await service.register_user(
            phone_number=request.phone_number,
            full_name=request.full_name,
            email=request.email,
        )
        return MessageResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.exception("Registration step 1 failed")
        if "rate limit" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many OTP requests. Please try again later.",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )


@router.post(
    "/register/verify",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register — step 2: verify OTP and create account",
)
async def verify_registration(
    request: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Verifies the OTP, retrieves registration data from Redis cache,
    creates the user account (status=ACTIVE), and returns JWT tokens.
    OTP is single-use and expires after 5 minutes.
    Returns: user profile + access_token + refresh_token.
    """
    service = UserService(db)
    try:
        result = await service.verify_registration(
            phone_number=request.phone_number,
            otp=request.otp,
        )
        return AuthResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Login (step 1 + step 2)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Login — step 1: send OTP to phone",
)
async def login_user(
    request: UserLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Checks the user exists and is ACTIVE, then sends a 6-digit OTP via Twilio.
    Returns 404 if the phone number is not registered.
    Returns: message confirming OTP was sent.
    """
    service = UserService(db)
    try:
        result = await service.login_user(request.phone_number)
        return MessageResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        if "rate limit" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many OTP requests. Please try again later.",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )


@router.post(
    "/login/verify",
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    summary="Login — step 2: verify OTP and get tokens",
)
async def verify_login(
    request: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Verifies the OTP and returns fresh JWT tokens.
    OTP is single-use and expires after 5 minutes.
    Returns: user profile + access_token (1 year) + refresh_token.
    """
    service = UserService(db)
    try:
        result = await service.verify_login(
            phone_number=request.phone_number,
            otp=request.otp,
        )
        return AuthResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))