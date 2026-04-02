# File: app/api/v1/emergency_team_auth.py
"""
Emergency Team Authentication API — UC1

Login flow (2-step MFA):
  Step 1  POST /emergency-team/login          → verify email + password → send OTP
  Step 2  POST /emergency-team/login/verify   → verify OTP → return JWT tokens

Registration:
  Step 1  POST /emergency-team/register        → send OTP
  Step 2  POST /emergency-team/register/verify → verify OTP, create account

Account management:
  POST /emergency-team/change-password         → update password (JWT required)
  POST /emergency-team/deactivate/{id}         → deactivate account (admin only)

Forgot Password:
  Step 1  POST /emergency-team/forgot-password  → verify email, get temp password
  Step 2  POST /emergency-team/reset-password   → verify temp password, set new password
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
    EmergencyTeamLoginInitResponse,
    EmergencyTeamLoginVerifyRequest,
    EmergencyTeamLoginResendOTPRequest,
    EmergencyTeamAuthResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from app.schemas.auth import OTPVerifyRequest, MessageResponse
from app.db.models.enums import EmergencyTeamRole, Department

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emergency-team", tags=["ERT Auth — UC1"])


# ─────────────────────────────────────────────────────────────────────────────
# Registration
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
    Validates phone number and password, stores registration data in Redis,
    and sends a 6-digit OTP via SMS.
    Rate limited to 3 requests per hour per phone number.
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
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many OTP requests. Please try again later.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Registration failed. Please try again.")


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
    """
    service = EmergencyTeamService(db)
    try:
        result = await service.verify_team_member_registration(
            phone_number=request.phone_number,
            otp=request.otp,
        )
        return EmergencyTeamAuthResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:
        logger.exception("ERT registration step 2 failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Verification failed. Please try again.")


# ─────────────────────────────────────────────────────────────────────────────
# Login — 2-step MFA
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=EmergencyTeamLoginInitResponse,
    status_code=status.HTTP_200_OK,
    summary="Login ERT member — step 1: verify email + password, send OTP",
)
async def login_team_member(
    request: EmergencyTeamLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticates email + password.
    On success, sends a one-time OTP to the member's registered phone number.
    Returns a login_token the client must use in step 2.
    The phone number is never exposed — the server resolves it internally.
    """
    service = EmergencyTeamService(db)
    try:
        result = await service.login_team_member(
            email=request.email,
            password=request.password,
        )
        return EmergencyTeamLoginInitResponse(**result)
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("rate_limit:"):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail.replace("rate_limit:", "").strip())
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
    except Exception:
        logger.exception("ERT login step 1 failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Login failed. Please try again.")


@router.post(
    "/login/verify",
    response_model=EmergencyTeamAuthResponse,
    status_code=status.HTTP_200_OK,
    summary="Login ERT member — step 2: verify OTP and receive JWT tokens",
)
async def verify_login_otp(
    request: EmergencyTeamLoginVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Completes the 2-step login by verifying the OTP sent in step 1.
    Requires:
    - login_token: the token returned by step 1
    - otp:         the 6-digit code received via SMS
    The phone number is resolved server-side from the login_token.
    Returns: team member profile + access_token + refresh_token.
    OTP is single-use and expires after 5 minutes.
    """
    service = EmergencyTeamService(db)
    try:
        result = await service.verify_login_otp(
            login_token=request.login_token,
            otp=request.otp,
        )
        return EmergencyTeamAuthResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    except Exception:
        logger.exception("ERT login step 2 failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="OTP verification failed. Please try again.")


@router.post(
    "/login/resend-otp",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend login OTP — use when SMS was not received",
)
async def resend_login_otp(
    request: EmergencyTeamLoginResendOTPRequest,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyTeamService(db)
    try:
        result = await service.resend_login_otp(login_token=request.login_token)
        return MessageResponse(**result)
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("rate_limit:"):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail.replace("rate_limit:", "").strip())
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    except Exception:
        logger.exception("Login OTP resend failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to resend OTP. Please try again.")


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
            team_member_id=current_user["user_id"],
            old_password=request.old_password,
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
    service = EmergencyTeamService(db)
    try:
        result = await service.deactivate_team_member(
            team_member_id=team_member_id,
            requesting_user_id=current_user["user_id"],
        )
        return MessageResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except Exception:
        logger.exception("Deactivation failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Deactivation failed. Please try again.")


# ─────────────────────────────────────────────────────────────────────────────
# Forgot Password — no auth required (user is locked out)
# ─────────────────────────────────────────────────────────────────────────────
 
@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Forgot password — step 1: request temporary password by email",
)
async def forgot_password(
    request: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Step 1 of the forgot password flow. No authentication required.
 
    - Verifies the email exists in the database
    - Generates a secure temporary password (valid 15 minutes, single-use)
    - Sends the temporary password to the user's registered email via SendGrid
    - Always returns a generic success message (no email enumeration)
 
    Postman test flow:
      1. POST /emergency-team/forgot-password  → check your email for temp password
      2. POST /emergency-team/reset-password   → submit email + temp_password + new_password
      3. POST /emergency-team/login            → log in with new password
    """
    service = EmergencyTeamService(db)
    try:
        result = await service.forgot_password_request(email=request.email)
        return MessageResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:
        logger.exception("forgot_password failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process request. Please try again.",
        )
 
 
@router.post(
    "/reset-password",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Forgot password — step 2: verify temp password and set new password",
)
async def reset_password(
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Step 2 of the forgot password flow. No authentication required.
 
    - Verifies the temporary password (single-use, expires in 15 minutes)
    - Sets the new password in the database
    - After success the user can log in normally with the new password
 
    Errors:
    - 400: email not found, temp password invalid, or temp password expired
    """
    service = EmergencyTeamService(db)
    try:
        result = await service.reset_password_with_temp(
            email=request.email,
            temp_password=request.temp_password,
            new_password=request.new_password,
        )
        return MessageResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:
        logger.exception("reset_password failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset password. Please try again.",
        )