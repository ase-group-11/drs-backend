# File: app/api/v1/auth_updated.py
"""
Authentication API endpoints (UPDATED).

CHANGES:
- verify_registration now only requires phone_number and OTP
- Registration data is retrieved from Redis cache

Provides:
- User registration (send OTP)
- Registration verification (create account + get tokens)
- User login (send OTP)
- Login verification (get tokens)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.user_service import UserService
from app.schemas.auth import (
    UserRegisterRequest,
    OTPVerifyRequest,
    UserLoginRequest,
    MessageResponse,
    AuthResponse,
)
from app.schemas.common import ResponseBase

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Register new user",
    description="Send OTP to phone number for user registration"
)
async def register_user(
    request: UserRegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Register a new user by sending OTP to their phone number.
    
    Steps:
    1. Validate phone number format (E.164)
    2. Check if phone/email already registered
    3. Store registration data in Redis cache
    4. Generate 6-digit OTP
    5. Send OTP via SMS (Twilio)
    6. Store OTP in Redis with 5-minute expiry
    
    Rate Limit: 3 OTP requests per hour per phone number
    """
    service = UserService(db)
    
    try:
        result = await service.register_user(
            phone_number=request.phone_number,
            full_name=request.full_name,
            email=request.email
        )
        return MessageResponse(**result)
        
    except ValueError as e:
        # Phone/email already exists
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.exception("Register failed") 
        # OTP sending failed or other error
        if "rate limit" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many OTP requests. Please try again later."
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again."
        )


@router.post(
    "/register/verify",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Verify registration OTP",
    description="Verify OTP and create user account"
)
async def verify_registration(
    request: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Verify OTP and complete user registration.
    
    UPDATED: No longer requires full_name and email in request.
    These are retrieved from Redis cache.
    
    Steps:
    1. Verify OTP from Redis
    2. Retrieve registration data (full_name, email) from Redis cache
    3. Create user account (status: ACTIVE)
    4. Delete registration cache
    5. Generate JWT access token (30 min expiry)
    6. Generate JWT refresh token (7 days expiry)
    7. Delete OTP from Redis (one-time use)
    """
    service = UserService(db)
    
    try:
        result = await service.verify_registration(
            phone_number=request.phone_number,
            otp=request.otp
        )
        return AuthResponse(**result)
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post(
    "/login",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="User login",
    description="Send OTP to phone number for login"
)
async def login_user(
    request: UserLoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Initiate user login by sending OTP.
    
    Steps:
    1. Check if user exists and is active
    2. Generate 6-digit OTP
    3. Send OTP via SMS
    4. Store OTP in Redis with 5-minute expiry
    """
    service = UserService(db)
    
    try:
        result = await service.login_user(request.phone_number)
        return MessageResponse(**result)
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        if "rate limit" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many OTP requests. Please try again later."
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again."
        )


@router.post(
    "/login/verify",
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify login OTP",
    description="Verify OTP and get authentication tokens"
)
async def verify_login(
    request: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Verify OTP and complete user login.
    
    Steps:
    1. Verify OTP from Redis
    2. Get active user
    3. Generate JWT access token
    4. Generate JWT refresh token
    5. Delete OTP from Redis
    """
    service = UserService(db)
    
    try:
        result = await service.verify_login(
            phone_number=request.phone_number,
            otp=request.otp
        )
        return AuthResponse(**result)
        
    except ValueError as e:
        # Check if it's user not found or invalid OTP
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e)
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get(
    "/health",
    response_model=ResponseBase,
    summary="Health check",
    description="Check if authentication service is healthy"
)
async def health_check():
    """
    Health check endpoint for authentication service.
    """
    return ResponseBase(message="Authentication service is healthy")
