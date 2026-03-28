# File: app/api/v1/emergency_team_auth.py
"""
Emergency Team Authentication API — UC1

Different from citizen auth — ERT members use password (not OTP) to login.
Registration still requires OTP to verify phone number ownership.

Flow:
  Registration: POST /emergency-team/register        → send OTP
                POST /emergency-team/register/verify → verify OTP, create account
  Login:        POST /emergency-team/login           → password check, return JWT (no OTP)
  Account:      POST /emergency-team/change-password → update password (JWT required)
                POST /emergency-team/deactivate/{id} → deactivate account (admin only)
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.auth.dependencies import get_current_team_member
from app.services.emergency_team_service import EmergencyTeamService
from app.schemas.emergency_team import (
    EmergencyTeamRegisterRequest,
    EmergencyTeamLoginRequest,
    EmergencyTeamAuthResponse,
    ChangePasswordRequest,
)
from app.schemas.auth import OTPVerifyRequest, MessageResponse
from app.db.models.enums import EmergencyTeamRole, Department

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emergency-team", tags=["ERT Auth — UC1"])


# ─────────────────────────────────────────────────────────────────────────────
# Registration (step 1 + step 2)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Register ERT member — step 1: send OTP",
)
async def register_team_member(
    request: EmergencyTeamRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Validates the phone number and password, stores registration data in Redis,
    and sends a 6-digit OTP via Twilio SMS.
    Rate limited to 3 requests per hour per phone number.
    Returns: message confirming OTP was sent.
    """
    service = EmergencyTeamService(db)
    try:
        from app.utils.enum_utils import coerce_enum
        role       = coerce_enum(EmergencyTeamRole, request.role)
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

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.exception("ERT registration step 1 failed")
        if "rate limit" in str(exc).lower():
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
    summary="Register ERT member — step 2: verify OTP and create account",
)
async def verify_registration(
    request: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Verifies the OTP, retrieves registration data from Redis, creates the
    team member account, and returns JWT tokens.
    OTP is single-use and expires after 5 minutes.
    Returns: team member profile + access_token + refresh_token.
    """
    service = EmergencyTeamService(db)
    try:
        result = await service.verify_registration(
            phone_number=request.phone_number,
            otp=request.otp,
        )
        return EmergencyTeamAuthResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.exception("ERT registration step 2 failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Verification failed. Please try again.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Login (password-based — no OTP)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=EmergencyTeamAuthResponse,
    status_code=status.HTTP_200_OK,
    summary="Login ERT member — password-based (no OTP)",
)
async def login_team_member(
    request: EmergencyTeamLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticates an ERT member using email/phone + password.
    No OTP required — password verification only.
    Returns: team member profile + access_token (1 year) + refresh_token.
    """
    service = EmergencyTeamService(db)
    try:
        result = await service.login_team_member(
            identifier=request.identifier,
            password=request.password,
        )
        return EmergencyTeamAuthResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    except Exception as exc:
        logger.exception("ERT login failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed. Please try again.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Account management
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/change-password",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Change password (requires JWT)",
)
async def change_password(
    request: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Changes the password for the currently authenticated team member.
    Requires the current password to be correct before accepting the new one.
    Requires: emergency team Bearer token.
    """
    service = EmergencyTeamService(db)
    try:
        result = await service.change_password(
            team_member_id=current_user["id"],
            current_password=request.current_password,
            new_password=request.new_password,
        )
        return MessageResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/deactivate/{team_member_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Deactivate a team member account (Admin)",
)
async def deactivate_team_member(
    team_member_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Soft-deactivates a team member account (sets status=INACTIVE).
    They can no longer log in but their data is retained for audit.
    Requires: emergency team Bearer token (admin role enforced in service).
    """
    service = EmergencyTeamService(db)
    try:
        result = await service.deactivate_team_member(
            team_member_id=team_member_id,
            requesting_user_id=current_user["id"],
        )
        return MessageResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except Exception as exc:
        logger.exception("Deactivation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Deactivation failed. Please try again.",
        )