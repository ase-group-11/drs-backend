# File: app/api/v1/emergency_team_auth.py
"""
Emergency Team Authentication API endpoints.

UPDATED:
- Registration uses OTP (send OTP → verify OTP → create account)
- Login uses password (email/phone + password)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.db.session import get_db
from app.services.emergency_team_service import EmergencyTeamService
from app.schemas.emergency_team import (
    EmergencyTeamRegisterRequest,
    EmergencyTeamLoginRequest,
    EmergencyTeamAuthResponse,
    ChangePasswordRequest,
)
from app.schemas.auth import OTPVerifyRequest, MessageResponse
from app.schemas.common import ResponseBase
from app.db.models.enums import EmergencyTeamRole, Department

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emergency-team", tags=["Emergency Team Auth"])


@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Register emergency team member (Send OTP)",
    description="Send OTP to phone number for emergency team registration"
)
async def register_team_member(
    request: EmergencyTeamRegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Register a new emergency team member (send OTP).
    
    UPDATED: Now sends OTP instead of creating account immediately.
    
    Steps:
    1. Validate phone number, email, employee_id uniqueness
    2. Validate password strength
    3. Hash password with Argon2
    4. Store registration data (including hashed password) in Redis cache
    5. Generate and send OTP via SMS
    6. Return success message
    
    **Password Requirements:**
    - At least 8 characters
    - Contains uppercase letter
    - Contains lowercase letter
    - Contains digit
    
    **Request Body:**
    - phone_number: E.164 format (e.g., +1234567890)
    - password: Strong password
    - full_name: Team member's full name
    - email: Email address
    - role: admin | manager | staff
    - department: medical | police | fire | it
    - employee_id: Optional employee ID
    
    **Response:**
    - message: Success message with instructions to verify OTP
    
    **Errors:**
    - 400: Phone/email/employee_id already exists
    - 422: Validation error (weak password, invalid format)
    - 500: Failed to send OTP
    """
    service = EmergencyTeamService(db)
    
    try:
        # Convert string enums to actual enum types
        role = EmergencyTeamRole(request.role)
        department = Department(request.department)
        
        result = await service.register_team_member(
            phone_number=request.phone_number,
            password=request.password,
            full_name=request.full_name,
            email=request.email,
            role=role,
            department=department,
            employee_id=request.employee_id
        )
        
        return MessageResponse(**result)
        
    except ValueError as e:
        # Duplicate phone/email/employee_id or enum conversion error
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.exception("Emergency team registration failed")
        if "rate limit" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many OTP requests. Please try again later."
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed. Please try again."
        )


@router.post(
    "/register/verify",
    response_model=EmergencyTeamAuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Verify emergency team registration OTP",
    description="Verify OTP and create emergency team account"
)
async def verify_team_member_registration(
    request: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Verify OTP and complete emergency team registration.
    
    NEW ENDPOINT: Verifies OTP and creates the account.
    
    Steps:
    1. Verify OTP from Redis
    2. Retrieve registration data from Redis cache
    3. Create emergency team member account (status: ACTIVE)
    4. Delete registration cache
    5. Generate JWT access token (30 min expiry)
    6. Generate JWT refresh token (7 days expiry)
    7. Return team member data + tokens
    
    **Request Body:**
    - phone_number: Phone number that received OTP
    - otp: 6-digit OTP code
    
    **Response:**
    - team_member: Team member profile data
    - tokens: Access and refresh tokens
    
    **Errors:**
    - 400: Invalid or expired OTP
    - 400: Registration data not found (need to register again)
    """
    service = EmergencyTeamService(db)
    
    try:
        result = await service.verify_team_member_registration(
            phone_number=request.phone_number,
            otp=request.otp
        )
        
        return EmergencyTeamAuthResponse(**result)
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.exception("Emergency team verification failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Verification failed. Please try again."
        )


@router.post(
    "/login",
    response_model=EmergencyTeamAuthResponse,
    status_code=status.HTTP_200_OK,
    summary="Emergency team login",
    description="Login with email/phone + password"
)
async def login_team_member(
    request: EmergencyTeamLoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Login emergency team member with password.
    
    UNCHANGED: Still uses password-based authentication.
    
    Steps:
    1. Find team member by email or phone number
    2. Verify password with Argon2
    3. Check account is active
    4. Generate JWT access token (30 min expiry)
    5. Generate JWT refresh token (7 days expiry)
    6. Return team member data + tokens
    
    **Request Body:**
    - email: Email address (use email OR phone_number)
    - phone_number: Phone number (use email OR phone_number)
    - password: Password
    
    **Response:**
    - team_member: Team member profile data
    - tokens: Access and refresh tokens
    
    **Errors:**
    - 401: Invalid credentials
    - 403: Account not active
    """
    service = EmergencyTeamService(db)
    
    try:
        # Login with email or phone number
        if request.email:
            result = await service.login_team_member(
                email=request.email,
                password=request.password
            )
        else:
            result = await service.login_team_member_by_phone(
                phone_number=request.phone_number,
                password=request.password
            )
        
        return EmergencyTeamAuthResponse(**result)
        
    except ValueError as e:
        # Invalid credentials or account not active
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )


@router.post(
    "/change-password",
    response_model=ResponseBase,
    status_code=status.HTTP_200_OK,
    summary="Change password",
    description="Change team member password"
)
async def change_password(
    request: ChangePasswordRequest,
    team_member_id: str,  # In production, get from JWT token
    db: AsyncSession = Depends(get_db)
):
    """
    Change team member password.
    
    Steps:
    1. Verify old password
    2. Validate new password strength
    3. Hash new password with Argon2
    4. Update password in database
    
    **Request Body:**
    - old_password: Current password
    - new_password: New password (must meet strength requirements)
    
    **Response:**
    - message: Success message
    
    **Errors:**
    - 400: Old password incorrect
    - 404: Team member not found
    - 422: New password too weak
    
    Note: In production, team_member_id should be extracted from JWT token,
    not passed as parameter.
    """
    service = EmergencyTeamService(db)
    
    try:
        result = await service.change_password(
            team_member_id=team_member_id,
            old_password=request.old_password,
            new_password=request.new_password
        )
        
        return ResponseBase(**result)
        
    except ValueError as e:
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
    description="Check if emergency team service is healthy"
)
async def health_check():
    """Health check endpoint for emergency team service."""
    return ResponseBase(message="Emergency team service is healthy")


@router.post(
    "/deactivate/{team_member_id}",
    response_model=ResponseBase,
    summary="Deactivate team member",
    description="Deactivate a team member account (Admin only)"
)
async def deactivate_team_member(
    team_member_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Deactivate team member account.
    
    Note: Should be protected with admin-only authorization in production.
    """
    service = EmergencyTeamService(db)
    
    try:
        result = await service.deactivate_team_member(team_member_id)
        return ResponseBase(**result)
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )