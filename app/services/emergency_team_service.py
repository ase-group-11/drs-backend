# File: app/services/emergency_team_service.py
"""
Emergency team service for emergency responders.

Login flow (2-step MFA):
  Step 1 — POST /emergency-team/login
           Verify email + password.
           On success, send OTP to the member's registered phone number.
           Return {message, phone_number}.

  Step 2 — POST /emergency-team/login/verify
           Verify the OTP received via SMS.
           On success, return JWT tokens + team member data.

Registration flow (unchanged):
  Step 1 — POST /emergency-team/register       → send OTP
  Step 2 — POST /emergency-team/register/verify → verify OTP, create account
"""

from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.repositories.emergency_team_repository import EmergencyTeamRepository
from app.services.otp_service import send_otp_code, verify_otp
from app.services.registration_cache import (
    store_registration_data,
    get_registration_data,
    delete_registration_data
)
from app.auth.password_handler import hash_password, verify_password
from app.auth.jwt_handler import create_access_token, create_refresh_token
from app.db.models.emergency_team import EmergencyTeam
from app.db.models.enums import UserStatus, EmergencyTeamRole, Department
from app.core.config import settings

# Cache helpers used directly for the login-pending flag
from cache.redis_client import set_with_expiry, get_value, delete_key

logger = logging.getLogger(__name__)

# Redis key prefix that marks a phone number as "password already verified,
# awaiting OTP confirmation".  Kept separate from the registration cache so
# the two flows never collide.
_LOGIN_PENDING_PREFIX = "ert_login_pending:"


def _login_pending_key(phone_number: str) -> str:
    return f"{_LOGIN_PENDING_PREFIX}{phone_number}"


class EmergencyTeamService:
    """Emergency team service for authentication and team management."""

    def __init__(self, session: AsyncSession):
        """Initialize emergency team service."""
        self.session = session
        self.team_repo = EmergencyTeamRepository(session)

    # ─────────────────────────────────────────────────────────────────────────
    # Registration (step 1 + step 2)  — UNCHANGED
    # ─────────────────────────────────────────────────────────────────────────

    async def register_team_member(
        self,
        phone_number: str,
        password: str,
        full_name: str,
        email: str,
        role: EmergencyTeamRole,
        department: Department,
        employee_id: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Register a new emergency team member — step 1: send OTP.

        Steps:
        1. Validate uniqueness (phone, email, employee_id)
        2. Hash password
        3. Store registration data in Redis cache
        4. Generate and send OTP to phone_number
        5. Return success message
        """
        logger.info(f"📝 Starting emergency team registration for {phone_number}")

        try:
            if await self.team_repo.phone_exists(phone_number):
                raise ValueError(f"Phone number {phone_number} is already registered")

            if await self.team_repo.email_exists(email):
                raise ValueError(f"Email {email} is already registered")

            if employee_id and await self.team_repo.employee_id_exists(employee_id):
                raise ValueError(f"Employee ID {employee_id} is already registered")

            password_hash = hash_password(password)

            await store_registration_data(
                phone_number=phone_number,
                full_name=full_name,
                email=email,
                password_hash=password_hash,
                role=role.value,
                department=department.value,
                employee_id=employee_id
            )

            otp = await send_otp_code(phone_number)
            if not otp:
                await delete_registration_data(phone_number)
                raise Exception("OTP service returned None")

            logger.info(f"✅ Registration OTP sent to {phone_number}")
            return {
                "message": f"OTP sent successfully to {phone_number}. Please verify to complete registration.",
                "phone_number": phone_number
            }

        except ValueError:
            raise
        except Exception:
            logger.exception("❌ Emergency team registration failed")
            raise

    async def verify_team_member_registration(
        self,
        phone_number: str,
        otp: str
    ) -> Dict[str, Any]:
        """
        Verify OTP and create emergency team member account — step 2.

        Steps:
        1. Verify OTP
        2. Retrieve registration data from Redis
        3. Create team member account (status: ACTIVE)
        4. Delete registration cache
        5. Generate JWT tokens
        6. Return team member data + tokens
        """
        logger.info(f"🔐 Verifying registration OTP for {phone_number}")

        try:
            is_valid = await verify_otp(phone_number, otp)
            if not is_valid:
                raise ValueError("Invalid or expired OTP")

            reg_data = await get_registration_data(phone_number)
            if not reg_data:
                raise ValueError("Registration data not found. Please register again.")

            role = EmergencyTeamRole(reg_data["role"])
            department = Department(reg_data["department"])

            team_member = await self.team_repo.create(
                phone_number=reg_data["phone_number"],
                password_hash=reg_data["password_hash"],
                full_name=reg_data["full_name"],
                email=reg_data["email"],
                role=role,
                department=department,
                employee_id=reg_data.get("employee_id"),
                status=UserStatus.ACTIVE
            )
            logger.info(f"✅ Emergency team member created: {team_member.id}")

            await delete_registration_data(phone_number)
            await self.session.flush()
            await self.session.refresh(team_member)

            access_token = create_access_token(
                user_id=team_member.id,
                user_type="emergency_team"
            )
            refresh_token = create_refresh_token(
                user_id=team_member.id,
                user_type="emergency_team"
            )

            logger.info(f"✅ Registration completed for {phone_number}")
            return {
                "team_member": {
                    "id": team_member.id,
                    "phone_number": team_member.phone_number,
                    "full_name": team_member.full_name,
                    "email": team_member.email,
                    "role": team_member.role.value,
                    "department": team_member.department.value,
                    "employee_id": team_member.employee_id,
                    "status": team_member.status.value,
                    "created_at": team_member.created_at.isoformat()
                },
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "token_type": "bearer",
                    "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
                }
            }

        except ValueError:
            raise
        except Exception:
            logger.exception("❌ Emergency team registration verification failed")
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # Login — 2-step MFA (email+password → OTP)
    # ─────────────────────────────────────────────────────────────────────────

    async def login_team_member(
        self,
        email: str,
        password: str
    ) -> Dict[str, str]:
        """
        Login step 1 — verify email + password, then send OTP.

        Steps:
        1. Look up active team member by email
        2. Verify password (Argon2)
        3. Store a short-lived login-pending flag in Redis keyed by phone number
        4. Send OTP to the member's registered phone number
        5. Return {message, phone_number} — client uses phone_number for step 2

        Args:
            email:    Registered email address
            password: Plain-text password

        Returns:
            {"message": "...", "phone_number": "+353..."}

        Raises:
            ValueError: bad credentials / inactive account
        """
        logger.info(f"🔑 Login step 1 for {email}")

        try:
            team_member = await self.team_repo.get_active_team_member_by_email(email)
            if not team_member:
                logger.warning(f"❌ Team member not found or inactive: {email}")
                raise ValueError("Invalid credentials or account is not active")

            if not verify_password(password, team_member.password_hash):
                logger.warning("❌ Invalid password")
                raise ValueError("Invalid credentials")

            logger.debug(f"✅ Password verified for {email}")

            # Mark this phone number as "password already verified".
            # TTL = OTP expiry + a small buffer so both expire together.
            pending_ttl = settings.OTP_EXPIRY_SECONDS + 60
            await set_with_expiry(
                _login_pending_key(team_member.phone_number),
                team_member.id,   # store the member id as the value
                pending_ttl
            )

            # Send OTP to the registered phone
            otp = await send_otp_code(team_member.phone_number)
            if not otp:
                await delete_key(_login_pending_key(team_member.phone_number))
                raise Exception("OTP service failed — please try again")

            logger.info(f"✅ Login OTP sent to {team_member.phone_number}")
            return {
                "message": (
                    f"Password verified. An OTP has been sent to your registered "
                    f"phone number ending in {team_member.phone_number[-4:]}. "
                    "Please verify to complete login."
                ),
                "phone_number": team_member.phone_number
            }

        except ValueError:
            raise
        except Exception:
            logger.exception("❌ Login step 1 failed")
            raise

    async def verify_login_otp(
        self,
        phone_number: str,
        otp: str
    ) -> Dict[str, Any]:
        """
        Login step 2 — verify OTP and issue JWT tokens.

        Steps:
        1. Check that a login-pending flag exists for this phone number
           (ensures step 1 was completed first)
        2. Verify the OTP
        3. Clean up the login-pending flag
        4. Look up the team member
        5. Generate and return JWT tokens + team member data

        Args:
            phone_number: Registered phone number (returned by step 1)
            otp:          6-digit OTP received via SMS

        Returns:
            {"team_member": {...}, "tokens": {...}}

        Raises:
            ValueError: OTP invalid / step 1 not completed / account not found
        """
        logger.info(f"🔐 Login step 2 (OTP verify) for {phone_number}")

        try:
            # 1. Confirm step 1 was completed for this phone number
            pending_member_id = await get_value(_login_pending_key(phone_number))
            if not pending_member_id:
                logger.warning(f"❌ No pending login session for {phone_number}")
                raise ValueError(
                    "No active login session found. Please start login again."
                )

            # 2. Verify OTP (one-time use — deleted inside verify_otp on success)
            is_valid = await verify_otp(phone_number, otp)
            if not is_valid:
                logger.warning(f"❌ Invalid or expired OTP for {phone_number}")
                raise ValueError("Invalid or expired OTP")

            # 3. Clean up the login-pending flag
            await delete_key(_login_pending_key(phone_number))

            # 4. Fetch the team member (use the id stored in the pending flag)
            team_member = await self.team_repo.get_active_team_member_by_phone(
                phone_number
            )
            if not team_member:
                logger.error(f"❌ Team member not found after OTP verify: {phone_number}")
                raise ValueError("Account not found or is no longer active")

            # 5. Generate JWT tokens
            access_token = create_access_token(
                user_id=team_member.id,
                user_type="emergency_team"
            )
            refresh_token = create_refresh_token(
                user_id=team_member.id,
                user_type="emergency_team"
            )

            logger.info(f"✅ Login complete for {phone_number} ({team_member.email})")
            return {
                "team_member": {
                    "id": team_member.id,
                    "phone_number": team_member.phone_number,
                    "full_name": team_member.full_name,
                    "email": team_member.email,
                    "role": team_member.role.value,
                    "department": team_member.department.value,
                    "employee_id": team_member.employee_id,
                    "status": team_member.status.value,
                    "created_at": team_member.created_at.isoformat()
                },
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "token_type": "bearer",
                    "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
                }
            }

        except ValueError:
            raise
        except Exception:
            logger.exception("❌ Login step 2 (OTP verify) failed")
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # Account management  — UNCHANGED
    # ─────────────────────────────────────────────────────────────────────────

    async def get_team_member_by_id(
        self,
        team_member_id: str
    ) -> Optional[EmergencyTeam]:
        """Get team member by ID."""
        return await self.team_repo.get_by_id(team_member_id)

    async def get_team_member_by_email(
        self,
        email: str
    ) -> Optional[EmergencyTeam]:
        """Get team member by email."""
        return await self.team_repo.get_by_email(email)

    async def get_team_members_by_department(
        self,
        department: Department,
        skip: int = 0,
        limit: int = 100
    ) -> list[EmergencyTeam]:
        """Get all team members in a department."""
        return await self.team_repo.get_by_department(department, skip, limit)

    async def change_password(
        self,
        team_member_id: str,
        old_password: str,
        new_password: str
    ) -> Dict[str, str]:
        """
        Change password for an authenticated team member.

        Verifies the current (old) password before applying the new one.
        """
        from sqlalchemy import text
        from datetime import datetime

        logger.info(f"🔐 Changing password for team member {team_member_id}")

        fetch_sql = text("""
            SELECT id, password_hash, status
            FROM emergency_teams
            WHERE id = :id
              AND deleted_at IS NULL
        """)
        result = await self.session.execute(fetch_sql, {"id": team_member_id})
        member = result.mappings().first()

        if not member:
            raise ValueError("Team member not found")

        if str(member["status"]) != "ACTIVE":
            raise ValueError("Account is not active. Please contact an administrator.")

        if not verify_password(old_password, member["password_hash"]):
            raise ValueError("Current password is incorrect")

        new_hash = hash_password(new_password)
        now = datetime.utcnow()

        update_sql = text("""
            UPDATE emergency_teams
            SET password_hash = :password_hash,
                updated_at    = :updated_at
            WHERE id = :id
              AND deleted_at IS NULL
        """)
        await self.session.execute(update_sql, {
            "password_hash": new_hash,
            "updated_at":    now,
            "id":            team_member_id,
        })

        logger.info(f"✅ Password changed for team member {team_member_id}")
        return {"message": "Password changed successfully"}

    async def deactivate_team_member(
        self,
        team_member_id: str,
        requesting_user_id: str
    ) -> Dict[str, str]:
        """Deactivate a team member account."""
        team_member = await self.team_repo.deactivate_team_member(team_member_id)

        if not team_member:
            raise ValueError("Team member not found")

        await self.session.commit()
        return {"message": "Team member deactivated successfully"}