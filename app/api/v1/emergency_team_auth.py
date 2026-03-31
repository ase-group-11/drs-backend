# File: app/api/v1/emergency_team_auth.py
"""
Emergency Team Authentication API endpoints.

Endpoints:
- POST /register                → send OTP for registration
- POST /register/verify         → verify OTP, create account
- POST /login                   → password-based login
- POST /change-password         → change password (JWT required)
- GET  /health                  → health check
- POST /deactivate/{id}         → deactivate account (admin)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.db.session import get_db
from app.auth.dependencies import get_current_team_member
from app.services.emergency_team_service import EmergencyTeamService
from app.schemas.emergency_team import (
    EmergencyTeamRegisterRequest,
    EmergencyTeamLoginRequest,
    EmergencyTeamLoginInitResponse,
    EmergencyTeamLoginVerifyRequest,
    EmergencyTeamAuthResponse,
    EmergencyTeamLoginResendOTPRequest,
    ChangePasswordRequest,
)
from app.schemas.auth import OTPVerifyRequest, MessageResponse
from app.schemas.common import ResponseBase
from app.db.models.enums import EmergencyTeamRole, Department
from typing import Dict, Any

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emergency-team", tags=["Emergency Team Auth"])


# ══════════════════════════════════════════════════════════════════════════════
# REGISTRATION
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Register emergency team member (Send OTP)",
    description="Send OTP to phone number for emergency team registration",
)
async def register_team_member(
    request: EmergencyTeamRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyTeamService(db)
    try:
        from app.utils.enum_utils import coerce_enum
        role = coerce_enum(EmergencyTeamRole, request.role)
        department = coerce_enum(Department, request.department)

        result = await service.register_team_member(
            phone_number=request.phone_number,
            password=request.password,
            full_name=request.full_name,
            email=request.email,
            role=role,
            department=department,
            employee_id=request.employee_id,
        )
        return MessageResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception("Emergency team registration failed")
        if "rate limit" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many OTP requests. Please try again later.",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed. Please try again.",
        )


@router.post(
    "/register/verify",
    response_model=EmergencyTeamAuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Verify emergency team registration OTP",
    description="Verify OTP and create emergency team account",
)
async def verify_team_member_registration(
    request: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyTeamService(db)
    try:
        result = await service.verify_team_member_registration(
            phone_number=request.phone_number,
            otp=request.otp,
        )
        return EmergencyTeamAuthResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        logger.exception("Emergency team verification failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Verification failed. Please try again.",
        )


# ══════════════════════════════════════════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/login",
    response_model=EmergencyTeamLoginInitResponse,
    status_code=status.HTTP_200_OK,
    summary="Emergency team login",
    description="Login with email/phone + password",
)
async def login_team_member(
    request: EmergencyTeamLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyTeamService(db)
    try:
        if request.email:
            result = await service.login_team_member(
                email=request.email, password=request.password
            )
        else:
            result = await service.login_team_member_by_phone(
                phone_number=request.phone_number, password=request.password
            )
        return EmergencyTeamAuthResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# CHANGE PASSWORD  (authenticated — JWT required)
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/change-password",
    response_model=ResponseBase,
    status_code=status.HTTP_200_OK,
    summary="Change password (authenticated)",
    description=(
        "Change the currently logged-in team member's password. "
        "Requires a valid Bearer token and the correct current password."
    ),
)
async def change_password(
    request: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Change password for the authenticated team member.

    - **Bearer token required** — the team member ID is read from the JWT,
      so no user can change another member's password.
    - **old_password** must match the currently stored hash.
    - **new_password** must satisfy: 8+ chars, uppercase, lowercase, digit.

    Errors:
    - 400: old_password is incorrect
    - 403: caller is not an emergency team member
    - 404: team member record not found (should not happen with a valid token)
    """
    team_member_id = current_user["user_id"]
    service = EmergencyTeamService(db)

    try:
        result = await service.change_password(
            team_member_id=team_member_id,
            old_password=request.old_password,
            new_password=request.new_password,
        )
        return ResponseBase(**result)

    except ValueError as e:
        detail = str(e)
        if "not found" in detail.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    except Exception:
        logger.exception("change_password failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not change password. Please try again.",
        )


# ══════════════════════════════════════════════════════════════════════════════
# MISC
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/health",
    response_model=ResponseBase,
    summary="Health check",
    description="Check if emergency team service is healthy",
)
async def health_check():
    """Health check endpoint for emergency team service."""
    return ResponseBase(message="Emergency team service is healthy")


@router.post(
    "/deactivate/{team_member_id}",
    response_model=ResponseBase,
    summary="Deactivate team member",
    description="Deactivate a team member account (Admin only)",
)
async def deactivate_team_member(
    team_member_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Deactivate team member account.

    Requires emergency team Bearer token.
    In a full RBAC implementation this would be restricted to ADMIN role.
    """
    service = EmergencyTeamService(db)
    try:
        result = await service.deactivate_team_member(team_member_id)
        return ResponseBase(**result)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
