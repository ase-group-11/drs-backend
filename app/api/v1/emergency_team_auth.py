# # # File: app/api/v1/emergency_team_auth.py
# # """
# # Emergency Team Authentication API endpoints.

# # Provides:
# # - Emergency team registration (password-based)
# # - Emergency team login (password-based)
# # - Password change
# # """

# # from fastapi import APIRouter, Depends, HTTPException, status
# # from sqlalchemy.ext.asyncio import AsyncSession

# # from app.db.session import get_db
# # from app.services.emergency_team_service import EmergencyTeamService
# # from app.schemas.emergency_team import (
# #     EmergencyTeamRegisterRequest,
# #     EmergencyTeamLoginRequest,
# #     EmergencyTeamAuthResponse,
# #     ChangePasswordRequest,
# # )
# # # from app.schemas.common import ResponseBase, MessageResponse
# # from app.schemas.common import ResponseBase
# # from app.models.enums import EmergencyTeamRole, Department

# # router = APIRouter(prefix="/emergency-team", tags=["Emergency Team Auth"])


# # @router.post(
# #     "/register",
# #     response_model=dict,
# #     status_code=status.HTTP_201_CREATED,
# #     summary="Register emergency team member",
# #     description="Register new emergency team member with password"
# # )
# # async def register_team_member(
# #     request: EmergencyTeamRegisterRequest,
# #     db: AsyncSession = Depends(get_db)
# # ):
# #     """
# #     Register a new emergency team member.
    
# #     Steps:
# #     1. Validate phone number, email, employee_id uniqueness
# #     2. Validate password strength
# #     3. Hash password with Argon2
# #     4. Create team member account (status: ACTIVE)
# #     5. Return team member data
    
# #     **Password Requirements:**
# #     - At least 8 characters
# #     - Contains uppercase letter
# #     - Contains lowercase letter
# #     - Contains digit
    
# #     **Request Body:**
# #     - phone_number: E.164 format (e.g., +1234567890)
# #     - password: Strong password
# #     - full_name: Team member's full name
# #     - email: Email address
# #     - role: admin | manager | staff
# #     - department: medical | police | fire | it
# #     - employee_id: Optional employee ID
    
# #     **Response:**
# #     - message: Success message
# #     - team_member: Team member profile data
    
# #     **Errors:**
# #     - 400: Phone/email/employee_id already exists
# #     - 422: Validation error (weak password, invalid format)
# #     """
# #     service = EmergencyTeamService(db)
    
# #     try:
# #         # Convert string enums to actual enum types
# #         role = EmergencyTeamRole(request.role)
# #         department = Department(request.department)
        
# #         result = await service.register_team_member(
# #             phone_number=request.phone_number,
# #             password=request.password,
# #             full_name=request.full_name,
# #             email=request.email,
# #             role=role,
# #             department=department,
# #             employee_id=request.employee_id
# #         )
        
# #         return result
        
# #     except ValueError as e:
# #         # Duplicate phone/email/employee_id or enum conversion error
# #         raise HTTPException(
# #             status_code=status.HTTP_400_BAD_REQUEST,
# #             detail=str(e)
# #         )
# #     except Exception as e:
# #         raise HTTPException(
# #             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
# #             detail="Registration failed. Please try again."
# #         )


# # @router.post(
# #     "/login",
# #     response_model=EmergencyTeamAuthResponse,
# #     status_code=status.HTTP_200_OK,
# #     summary="Emergency team login",
# #     description="Login with email/phone + password"
# # )
# # async def login_team_member(
# #     request: EmergencyTeamLoginRequest,
# #     db: AsyncSession = Depends(get_db)
# # ):
# #     """
# #     Login emergency team member with password.
    
# #     Steps:
# #     1. Find team member by email or phone number
# #     2. Verify password with Argon2
# #     3. Check account is active
# #     4. Generate JWT access token (30 min expiry)
# #     5. Generate JWT refresh token (7 days expiry)
# #     6. Return team member data + tokens
    
# #     **Request Body:**
# #     - email: Email address (use email OR phone_number)
# #     - phone_number: Phone number (use email OR phone_number)
# #     - password: Password
    
# #     **Response:**
# #     - team_member: Team member profile data
# #     - tokens: Access and refresh tokens
    
# #     **Errors:**
# #     - 401: Invalid credentials
# #     - 403: Account not active
# #     """
# #     service = EmergencyTeamService(db)
    
# #     try:
# #         # Login with email or phone number
# #         if request.email:
# #             result = await service.login_team_member(
# #                 email=request.email,
# #                 password=request.password
# #             )
# #         else:
# #             result = await service.login_team_member_by_phone(
# #                 phone_number=request.phone_number,
# #                 password=request.password
# #             )
        
# #         return EmergencyTeamAuthResponse(**result)
        
# #     except ValueError as e:
# #         # Invalid credentials or account not active
# #         raise HTTPException(
# #             status_code=status.HTTP_401_UNAUTHORIZED,
# #             detail=str(e)
# #         )


# # @router.post(
# #     "/change-password",
# #     response_model=ResponseBase,
# #     status_code=status.HTTP_200_OK,
# #     summary="Change password",
# #     description="Change team member password"
# # )
# # async def change_password(
# #     request: ChangePasswordRequest,
# #     team_member_id: str,  # In production, get from JWT token
# #     db: AsyncSession = Depends(get_db)
# # ):
# #     """
# #     Change team member password.
    
# #     Steps:
# #     1. Verify old password
# #     2. Validate new password strength
# #     3. Hash new password with Argon2
# #     4. Update password in database
    
# #     **Request Body:**
# #     - old_password: Current password
# #     - new_password: New password (must meet strength requirements)
    
# #     **Response:**
# #     - message: Success message
    
# #     **Errors:**
# #     - 400: Old password incorrect
# #     - 404: Team member not found
# #     - 422: New password too weak
    
# #     Note: In production, team_member_id should be extracted from JWT token,
# #     not passed as parameter.
# #     """
# #     service = EmergencyTeamService(db)
    
# #     try:
# #         result = await service.change_password(
# #             team_member_id=team_member_id,
# #             old_password=request.old_password,
# #             new_password=request.new_password
# #         )
        
# #         return ResponseBase(**result)
        
# #     except ValueError as e:
# #         if "not found" in str(e).lower():
# #             raise HTTPException(
# #                 status_code=status.HTTP_404_NOT_FOUND,
# #                 detail=str(e)
# #             )
# #         raise HTTPException(
# #             status_code=status.HTTP_400_BAD_REQUEST,
# #             detail=str(e)
# #         )


# # @router.get(
# #     "/health",
# #     response_model=ResponseBase,
# #     summary="Health check",
# #     description="Check if emergency team service is healthy"
# # )
# # async def health_check():
# #     """
# #     Health check endpoint for emergency team service.
# #     """
# #     return ResponseBase(message="Emergency team service is healthy")


# # # Additional endpoints for production

# # @router.get(
# #     "/me",
# #     response_model=dict,
# #     summary="Get current team member profile",
# #     description="Get authenticated team member's profile"
# # )
# # async def get_current_team_member(
# #     # In production: current_user: EmergencyTeam = Depends(get_current_team_member)
# #     db: AsyncSession = Depends(get_db)
# # ):
# #     """
# #     Get current authenticated team member profile.
    
# #     Note: Requires authentication middleware in production.
# #     Extract team_member_id from JWT token.
# #     """
# #     return {
# #         "message": "This endpoint requires authentication middleware",
# #         "implementation": "Extract user_id from JWT, query database, return profile"
# #     }


# # @router.post(
# #     "/deactivate/{team_member_id}",
# #     response_model=ResponseBase,
# #     summary="Deactivate team member",
# #     description="Deactivate a team member account (Admin only)"
# # )
# # async def deactivate_team_member(
# #     team_member_id: str,
# #     db: AsyncSession = Depends(get_db)
# # ):
# #     """
# #     Deactivate team member account.
    
# #     Note: Should be protected with admin-only authorization in production.
# #     """
# #     service = EmergencyTeamService(db)
    
# #     try:
# #         result = await service.deactivate_team_member(team_member_id)
# #         return ResponseBase(**result)
        
# #     except ValueError as e:
# #         raise HTTPException(
# #             status_code=status.HTTP_404_NOT_FOUND,
# #             detail=str(e)
# #         )




# # File: app/services/emergency_team_service.py
# """
# Emergency team service for emergency responders.

# UPDATED: 
# - Registration uses OTP (like users)
# - Login uses password (password-based authentication)
# """

# from typing import Optional, Dict, Any
# from sqlalchemy.ext.asyncio import AsyncSession
# import logging

# from app.repositories.emergency_team_repository import EmergencyTeamRepository
# from app.services.otp_service import send_otp_code, verify_otp
# from app.services.registration_cache import (
#     store_registration_data,
#     get_registration_data,
#     delete_registration_data
# )
# from app.auth.password_handler import hash_password, verify_password
# from app.auth.jwt_handler import create_access_token, create_refresh_token
# from app.models.emergency_team import EmergencyTeam
# from app.models.enums import UserStatus, EmergencyTeamRole, Department
# from app.core.config import settings

# # Setup logging
# logger = logging.getLogger(__name__)


# class EmergencyTeamService:
#     """Emergency team service for authentication and team management."""
    
#     def __init__(self, session: AsyncSession):
#         """Initialize emergency team service."""
#         self.session = session
#         self.team_repo = EmergencyTeamRepository(session)
    
#     async def register_team_member(
#         self,
#         phone_number: str,
#         password: str,
#         full_name: str,
#         email: str,
#         role: EmergencyTeamRole,
#         department: Department,
#         employee_id: Optional[str] = None
#     ) -> Dict[str, str]:
#         """
#         Register a new emergency team member (send OTP).
        
#         UPDATED: Now sends OTP instead of creating account immediately.
        
#         Steps:
#         1. Validate uniqueness (phone, email, employee_id)
#         2. Hash password
#         3. Store registration data in Redis cache
#         4. Generate and send OTP
#         5. Return success message
        
#         Args:
#             phone_number: Team member's phone number
#             password: Plain text password (will be hashed and cached)
#             full_name: Team member's full name
#             email: Email address
#             role: Team role (admin, manager, staff)
#             department: Department (medical, police, fire, it)
#             employee_id: Optional employee ID
            
#         Returns:
#             Dict with success message and phone number
            
#         Raises:
#             ValueError: If phone, email, or employee_id already exists
            
#         Example:
#             >>> result = await service.register_team_member(
#             ...     phone_number="+1234567890",
#             ...     password="SecurePass123!",
#             ...     full_name="John Doe",
#             ...     email="john.doe@emergency.ie",
#             ...     role=EmergencyTeamRole.STAFF,
#             ...     department=Department.MEDICAL
#             ... )
#             >>> # Returns: {"message": "OTP sent to +1234567890"}
#         """
#         logger.info(f"📝 Starting emergency team registration for {phone_number}")
        
#         try:
#             # Step 1: Check if phone number already exists
#             logger.debug(f"🔍 Checking if phone {phone_number} exists...")
#             if await self.team_repo.phone_exists(phone_number):
#                 logger.warning(f"❌ Phone {phone_number} already registered")
#                 raise ValueError(f"Phone number {phone_number} is already registered")
#             logger.debug("✅ Phone number available")
            
#             # Step 2: Check if email already exists
#             logger.debug(f"🔍 Checking if email {email} exists...")
#             if await self.team_repo.email_exists(email):
#                 logger.warning(f"❌ Email {email} already registered")
#                 raise ValueError(f"Email {email} is already registered")
#             logger.debug("✅ Email available")
            
#             # Step 3: Check if employee ID already exists (if provided)
#             if employee_id:
#                 logger.debug(f"🔍 Checking if employee_id {employee_id} exists...")
#                 if await self.team_repo.employee_id_exists(employee_id):
#                     logger.warning(f"❌ Employee ID {employee_id} already registered")
#                     raise ValueError(f"Employee ID {employee_id} is already registered")
#                 logger.debug("✅ Employee ID available")
            
#             # Step 4: Hash password
#             logger.debug("🔐 Hashing password...")
#             password_hash = hash_password(password)
#             logger.debug("✅ Password hashed")
            
#             # Step 5: Store registration data in cache
#             logger.info(f"💾 Storing emergency team registration data in Redis cache...")
#             try:
#                 # Store all registration data including password hash
#                 await store_registration_data(
#                     phone_number=phone_number,
#                     full_name=full_name,
#                     email=email,
#                     # Additional emergency team specific data
#                     password_hash=password_hash,
#                     role=role.value,
#                     department=department.value,
#                     employee_id=employee_id
#                 )
#                 logger.info("✅ Registration data cached successfully")
#             except Exception as cache_error:
#                 logger.error(f"❌ Failed to cache registration data: {cache_error}")
#                 raise Exception(f"Cache error: {str(cache_error)}")
            
#             # Step 6: Generate and send OTP
#             logger.info(f"📱 Generating and sending OTP to {phone_number}...")
#             try:
#                 otp = await send_otp_code(phone_number)
                
#                 if not otp:
#                     logger.error("❌ send_otp_code returned None/False")
#                     # Clean up cache if OTP sending fails
#                     await delete_registration_data(phone_number)
#                     raise Exception("OTP service returned None - check Twilio credentials or set ENVIRONMENT=testing")
                
#                 logger.info(f"✅ OTP generated and sent: {otp[:2]}****")
                
#             except Exception as otp_error:
#                 logger.error(f"❌ OTP sending failed: {type(otp_error).__name__}: {otp_error}")
#                 # Clean up cache
#                 await delete_registration_data(phone_number)
#                 raise Exception(f"OTP sending failed: {str(otp_error)}")
            
#             logger.info(f"✅ Emergency team registration OTP sent to {phone_number}")
#             return {
#                 "message": f"OTP sent successfully to {phone_number}. Please verify to complete registration.",
#                 "phone_number": phone_number
#             }
            
#         except ValueError as e:
#             # Re-raise validation errors as-is
#             raise
#         except Exception as e:
#             logger.exception(f"❌ Emergency team registration failed")
#             raise
    
#     async def verify_team_member_registration(
#         self,
#         phone_number: str,
#         otp: str
#     ) -> Dict[str, Any]:
#         """
#         Verify OTP and create emergency team member account.
        
#         Steps:
#         1. Verify OTP from Redis
#         2. Retrieve registration data from Redis cache
#         3. Create team member account (status: ACTIVE)
#         4. Delete registration cache
#         5. Generate JWT tokens
#         6. Return team member data + tokens
        
#         Args:
#             phone_number: Team member's phone number
#             otp: OTP code to verify
            
#         Returns:
#             Dict with team member data and tokens
            
#         Raises:
#             ValueError: If OTP invalid or registration data not found
#         """
#         logger.info(f"🔐 Starting emergency team registration verification for {phone_number}")
        
#         try:
#             # Step 1: Verify OTP
#             logger.debug(f"🔍 Verifying OTP...")
#             is_valid = await verify_otp(phone_number, otp)
            
#             if not is_valid:
#                 logger.warning(f"❌ Invalid or expired OTP for {phone_number}")
#                 raise ValueError("Invalid or expired OTP")
#             logger.debug("✅ OTP verified")
            
#             # Step 2: Retrieve registration data from cache
#             logger.debug(f"💾 Retrieving emergency team registration data from cache...")
#             reg_data = await get_registration_data(phone_number)
            
#             if not reg_data:
#                 logger.error(f"❌ No registration data found for {phone_number}")
#                 raise ValueError(
#                     "Registration data not found. Please register again."
#                 )
#             logger.debug(f"✅ Retrieved data: {reg_data.get('full_name')}")
            
#             # Step 3: Create emergency team member
#             logger.info(f"👤 Creating emergency team member account...")
            
#             # Convert string back to enums
#             role = EmergencyTeamRole(reg_data["role"])
#             department = Department(reg_data["department"])
            
#             team_member = await self.team_repo.create(
#                 phone_number=reg_data["phone_number"],
#                 password_hash=reg_data["password_hash"],
#                 full_name=reg_data["full_name"],
#                 email=reg_data["email"],
#                 role=role,
#                 department=department,
#                 employee_id=reg_data.get("employee_id"),
#                 status=UserStatus.ACTIVE
#             )
#             logger.info(f"✅ Emergency team member created: {team_member.id}")
            
#             # Step 4: Delete registration cache
#             logger.debug("🗑️  Cleaning up registration cache...")
#             await delete_registration_data(phone_number)
            
#             # Step 5: Commit transaction
#             await self.session.commit()
#             await self.session.refresh(team_member)
#             logger.debug("✅ Transaction committed")
            
#             # Step 6: Generate JWT tokens
#             logger.debug("🔑 Generating JWT tokens...")
#             access_token = create_access_token(
#                 user_id=team_member.id,
#                 user_type="emergency_team"
#             )
#             refresh_token = create_refresh_token(user_id=team_member.id)
#             logger.debug("✅ Tokens generated")
            
#             logger.info(f"✅ Emergency team registration completed for {phone_number}")
#             return {
#                 "team_member": {
#                     "id": team_member.id,
#                     "phone_number": team_member.phone_number,
#                     "full_name": team_member.full_name,
#                     "email": team_member.email,
#                     "role": team_member.role.value,
#                     "department": team_member.department.value,
#                     "employee_id": team_member.employee_id,
#                     "status": team_member.status.value,
#                     "created_at": team_member.created_at.isoformat()
#                 },
#                 "tokens": {
#                     "access_token": access_token,
#                     "refresh_token": refresh_token,
#                     "token_type": "bearer",
#                     "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
#                 }
#             }
            
#         except ValueError as e:
#             # Re-raise validation errors
#             raise
#         except Exception as e:
#             logger.exception(f"❌ Emergency team verification failed")
#             raise
    
#     async def login_team_member(
#         self,
#         email: str,
#         password: str
#     ) -> Dict[str, Any]:
#         """
#         Login emergency team member with email and password.
        
#         UNCHANGED: Still uses password-based authentication for login.
        
#         Steps:
#         1. Find active team member by email
#         2. Verify password with Argon2
#         3. Generate JWT tokens
#         4. Return team member data + tokens
        
#         Args:
#             email: Team member's email
#             password: Plain text password
            
#         Returns:
#             Dict with team member data and tokens
            
#         Raises:
#             ValueError: If credentials invalid or account not active
#         """
#         logger.info(f"🔑 Starting emergency team login for {email}")
        
#         try:
#             # Get active team member by email
#             logger.debug("🔍 Getting team member by email...")
#             team_member = await self.team_repo.get_active_team_member_by_email(email)
            
#             if not team_member:
#                 logger.warning(f"❌ Team member not found or not active: {email}")
#                 raise ValueError("Invalid credentials or account is not active")
#             logger.debug(f"✅ Team member found: {team_member.full_name}")
            
#             # Verify password
#             logger.debug("🔐 Verifying password...")
#             is_valid = verify_password(password, team_member.password_hash)
            
#             if not is_valid:
#                 logger.warning("❌ Invalid password")
#                 raise ValueError("Invalid credentials")
#             logger.debug("✅ Password verified")
            
#             # Generate JWT tokens
#             logger.debug("🔑 Generating tokens...")
#             access_token = create_access_token(
#                 user_id=team_member.id,
#                 user_type="emergency_team"
#             )
#             refresh_token = create_refresh_token(user_id=team_member.id)
#             logger.debug("✅ Tokens generated")
            
#             logger.info(f"✅ Emergency team login successful for {email}")
#             return {
#                 "team_member": {
#                     "id": team_member.id,
#                     "phone_number": team_member.phone_number,
#                     "full_name": team_member.full_name,
#                     "email": team_member.email,
#                     "role": team_member.role.value,
#                     "department": team_member.department.value,
#                     "employee_id": team_member.employee_id,
#                     "status": team_member.status.value,
#                     "created_at": team_member.created_at.isoformat()
#                 },
#                 "tokens": {
#                     "access_token": access_token,
#                     "refresh_token": refresh_token,
#                     "token_type": "bearer",
#                     "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
#                 }
#             }
            
#         except ValueError as e:
#             raise
#         except Exception as e:
#             logger.exception(f"❌ Emergency team login failed")
#             raise
    
#     async def login_team_member_by_phone(
#         self,
#         phone_number: str,
#         password: str
#     ) -> Dict[str, Any]:
#         """
#         Login emergency team member with phone number and password.
        
#         Alternative login method using phone number instead of email.
#         """
#         logger.info(f"🔑 Starting emergency team login by phone for {phone_number}")
        
#         try:
#             # Get active team member by phone
#             team_member = await self.team_repo.get_active_team_member_by_phone(
#                 phone_number
#             )
            
#             if not team_member:
#                 logger.warning(f"❌ Team member not found: {phone_number}")
#                 raise ValueError("Invalid credentials or account is not active")
            
#             # Verify password
#             is_valid = verify_password(password, team_member.password_hash)
            
#             if not is_valid:
#                 logger.warning("❌ Invalid password")
#                 raise ValueError("Invalid credentials")
            
#             # Generate JWT tokens
#             access_token = create_access_token(
#                 user_id=team_member.id,
#                 user_type="emergency_team"
#             )
            
#             refresh_token = create_refresh_token(user_id=team_member.id)
            
#             return {
#                 "team_member": {
#                     "id": team_member.id,
#                     "phone_number": team_member.phone_number,
#                     "full_name": team_member.full_name,
#                     "email": team_member.email,
#                     "role": team_member.role.value,
#                     "department": team_member.department.value,
#                     "employee_id": team_member.employee_id,
#                     "status": team_member.status.value,
#                     "created_at": team_member.created_at.isoformat()
#                 },
#                 "tokens": {
#                     "access_token": access_token,
#                     "refresh_token": refresh_token,
#                     "token_type": "bearer",
#                     "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
#                 }
#             }
            
#         except ValueError as e:
#             raise
#         except Exception as e:
#             logger.exception(f"❌ Emergency team login by phone failed")
#             raise
    
#     async def get_team_member_by_id(
#         self, 
#         team_member_id: str
#     ) -> Optional[EmergencyTeam]:
#         """Get team member by ID."""
#         return await self.team_repo.get_by_id(team_member_id)
    
#     async def get_team_member_by_email(
#         self, 
#         email: str
#     ) -> Optional[EmergencyTeam]:
#         """Get team member by email."""
#         return await self.team_repo.get_by_email(email)
    
#     async def get_team_members_by_department(
#         self,
#         department: Department,
#         skip: int = 0,
#         limit: int = 100
#     ) -> list[EmergencyTeam]:
#         """Get all team members in a department."""
#         return await self.team_repo.get_by_department(department, skip, limit)
    
#     async def change_password(
#         self,
#         team_member_id: str,
#         old_password: str,
#         new_password: str
#     ) -> Dict[str, str]:
#         """Change team member password."""
#         logger.info(f"🔐 Changing password for team member {team_member_id}")
        
#         team_member = await self.team_repo.get_by_id(team_member_id)
        
#         if not team_member:
#             raise ValueError("Team member not found")
        
#         # Verify old password
#         is_valid = verify_password(old_password, team_member.password_hash)
        
#         if not is_valid:
#             raise ValueError("Current password is incorrect")
        
#         # Hash new password
#         new_password_hash = hash_password(new_password)
        
#         # Update password
#         await self.team_repo.update(
#             team_member_id,
#             password_hash=new_password_hash
#         )
        
#         await self.session.commit()
        
#         return {"message": "Password changed successfully"}
    
#     async def deactivate_team_member(
#         self, 
#         team_member_id: str
#     ) -> Dict[str, str]:
#         """Deactivate a team member account."""
#         team_member = await self.team_repo.deactivate_team_member(team_member_id)
        
#         if not team_member:
#             raise ValueError("Team member not found")
        
#         await self.session.commit()
        
#         return {"message": "Team member deactivated successfully"}




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
from app.models.enums import EmergencyTeamRole, Department

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