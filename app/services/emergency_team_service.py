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
# from app.db.models.emergency_team import EmergencyTeam
# from app.db.models.enums import UserStatus, EmergencyTeamRole, Department
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

#                 # await store_registration_data(
#                 #     phone_number=phone_number,
#                 #     data={
#                 #         "full_name": full_name,
#                 #         "email": email,
#                 #         "password_hash": password_hash,
#                 #         "role": role.value,
#                 #         "department": department.value,
#                 #         "employee_id": employee_id,
#                 #         "user_type": "emergency_team"
#                 #     }
#                 # )
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



# File: app/services/emergency_team_service.py
"""
Emergency team service for emergency responders.

UPDATED: 
- Registration uses OTP (like users)
- Login uses password (password-based authentication)
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

# Setup logging
logger = logging.getLogger(__name__)


class EmergencyTeamService:
    """Emergency team service for authentication and team management."""
    
    def __init__(self, session: AsyncSession):
        """Initialize emergency team service."""
        self.session = session
        self.team_repo = EmergencyTeamRepository(session)
    
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
        Register a new emergency team member (send OTP).
        
        UPDATED: Now sends OTP instead of creating account immediately.
        
        Steps:
        1. Validate uniqueness (phone, email, employee_id)
        2. Hash password
        3. Store registration data in Redis cache
        4. Generate and send OTP
        5. Return success message
        
        Args:
            phone_number: Team member's phone number
            password: Plain text password (will be hashed and cached)
            full_name: Team member's full name
            email: Email address
            role: Team role (admin, manager, staff)
            department: Department (medical, police, fire, it)
            employee_id: Optional employee ID
            
        Returns:
            Dict with success message and phone number
            
        Raises:
            ValueError: If phone, email, or employee_id already exists
            
        Example:
            >>> result = await service.register_team_member(
            ...     phone_number="+1234567890",
            ...     password="SecurePass123!",
            ...     full_name="John Doe",
            ...     email="john.doe@emergency.ie",
            ...     role=EmergencyTeamRole.STAFF,
            ...     department=Department.MEDICAL
            ... )
            >>> # Returns: {"message": "OTP sent to +1234567890"}
        """
        logger.info(f"📝 Starting emergency team registration for {phone_number}")
        
        try:
            # Step 1: Check if phone number already exists
            logger.debug(f"🔍 Checking if phone {phone_number} exists...")
            if await self.team_repo.phone_exists(phone_number):
                logger.warning(f"❌ Phone {phone_number} already registered")
                raise ValueError(f"Phone number {phone_number} is already registered")
            logger.debug("✅ Phone number available")
            
            # Step 2: Check if email already exists
            logger.debug(f"🔍 Checking if email {email} exists...")
            if await self.team_repo.email_exists(email):
                logger.warning(f"❌ Email {email} already registered")
                raise ValueError(f"Email {email} is already registered")
            logger.debug("✅ Email available")
            
            # Step 3: Check if employee ID already exists (if provided)
            if employee_id:
                logger.debug(f"🔍 Checking if employee_id {employee_id} exists...")
                if await self.team_repo.employee_id_exists(employee_id):
                    logger.warning(f"❌ Employee ID {employee_id} already registered")
                    raise ValueError(f"Employee ID {employee_id} is already registered")
                logger.debug("✅ Employee ID available")
            
            # Step 4: Hash password
            logger.debug("🔐 Hashing password...")
            password_hash = hash_password(password)
            logger.debug("✅ Password hashed")
            
            # Step 5: Store registration data in cache
            logger.info(f"💾 Storing emergency team registration data in Redis cache...")
            try:
                # Store all registration data including password hash
                await store_registration_data(
                    phone_number=phone_number,
                    full_name=full_name,
                    email=email,
                    # Additional emergency team specific data
                    password_hash=password_hash,
                    role=role.value,
                    department=department.value,
                    employee_id=employee_id
                )

                # await store_registration_data(
                #     phone_number=phone_number,
                #     data={
                #         "full_name": full_name,
                #         "email": email,
                #         "password_hash": password_hash,
                #         "role": role.value,
                #         "department": department.value,
                #         "employee_id": employee_id,
                #         "user_type": "emergency_team"
                #     }
                # )
                logger.info("✅ Registration data cached successfully")
            except Exception as cache_error:
                logger.error(f"❌ Failed to cache registration data: {cache_error}")
                raise Exception(f"Cache error: {str(cache_error)}")
            
            # Step 6: Generate and send OTP
            logger.info(f"📱 Generating and sending OTP to {phone_number}...")
            try:
                otp = await send_otp_code(phone_number)
                
                if not otp:
                    logger.error("❌ send_otp_code returned None/False")
                    # Clean up cache if OTP sending fails
                    await delete_registration_data(phone_number)
                    raise Exception("OTP service returned None - check Twilio credentials or set ENVIRONMENT=testing")
                
                logger.info(f"✅ OTP generated and sent: {otp[:2]}****")
                
            except Exception as otp_error:
                logger.error(f"❌ OTP sending failed: {type(otp_error).__name__}: {otp_error}")
                # Clean up cache
                await delete_registration_data(phone_number)
                raise Exception(f"OTP sending failed: {str(otp_error)}")
            
            logger.info(f"✅ Emergency team registration OTP sent to {phone_number}")
            return {
                "message": f"OTP sent successfully to {phone_number}. Please verify to complete registration.",
                "phone_number": phone_number
            }
            
        except ValueError as e:
            # Re-raise validation errors as-is
            raise
        except Exception as e:
            logger.exception(f"❌ Emergency team registration failed")
            raise
    
    async def verify_team_member_registration(
        self,
        phone_number: str,
        otp: str
    ) -> Dict[str, Any]:
        """
        Verify OTP and create emergency team member account.
        
        Steps:
        1. Verify OTP from Redis
        2. Retrieve registration data from Redis cache
        3. Create team member account (status: ACTIVE)
        4. Delete registration cache
        5. Generate JWT tokens
        6. Return team member data + tokens
        
        Args:
            phone_number: Team member's phone number
            otp: OTP code to verify
            
        Returns:
            Dict with team member data and tokens
            
        Raises:
            ValueError: If OTP invalid or registration data not found
        """
        logger.info(f"🔐 Starting emergency team registration verification for {phone_number}")
        
        try:
            # Step 1: Verify OTP
            logger.debug(f"🔍 Verifying OTP...")
            is_valid = await verify_otp(phone_number, otp)
            
            if not is_valid:
                logger.warning(f"❌ Invalid or expired OTP for {phone_number}")
                raise ValueError("Invalid or expired OTP")
            logger.debug("✅ OTP verified")
            
            # Step 2: Retrieve registration data from cache
            logger.debug(f"💾 Retrieving emergency team registration data from cache...")
            reg_data = await get_registration_data(phone_number)
            
            if not reg_data:
                logger.error(f"❌ No registration data found for {phone_number}")
                raise ValueError(
                    "Registration data not found. Please register again."
                )
            logger.debug(f"✅ Retrieved data: {reg_data.get('full_name')}")
            
            # Step 3: Create emergency team member
            logger.info(f"👤 Creating emergency team member account...")
            
            # Convert string back to enums
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
            
            # Step 4: Delete registration cache
            logger.debug("🗑️  Cleaning up registration cache...")
            await delete_registration_data(phone_number)

            # Step 5: Flush so the team member row is visible within this session
            # before we refresh — but do NOT commit here. get_db() owns the commit.
            # (FIX #9: removed explicit session.commit() to eliminate double-commit)
            await self.session.flush()
            await self.session.refresh(team_member)
            logger.debug("✅ Changes flushed")
            
            # Step 6: Generate JWT tokens
            logger.debug("🔑 Generating JWT tokens...")
            access_token = create_access_token(
                user_id=team_member.id,
                user_type="emergency_team"
            )
            # refresh_token = create_refresh_token(user_id=team_member.id)
            refresh_token = create_refresh_token(user_id=team_member.id, user_type="emergency_team")
            logger.debug("✅ Tokens generated")
            
            logger.info(f"✅ Emergency team registration completed for {phone_number}")
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
            
        except ValueError as e:
            # Re-raise validation errors
            raise
        except Exception as e:
            logger.exception(f"❌ Emergency team verification failed")
            raise
    
    async def login_team_member(
        self,
        email: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Login emergency team member with email and password.
        
        UNCHANGED: Still uses password-based authentication for login.
        
        Steps:
        1. Find active team member by email
        2. Verify password with Argon2
        3. Generate JWT tokens
        4. Return team member data + tokens
        
        Args:
            email: Team member's email
            password: Plain text password
            
        Returns:
            Dict with team member data and tokens
            
        Raises:
            ValueError: If credentials invalid or account not active
        """
        logger.info(f"🔑 Starting emergency team login for {email}")
        
        try:
            # Get active team member by email
            logger.debug("🔍 Getting team member by email...")
            team_member = await self.team_repo.get_active_team_member_by_email(email)
            
            if not team_member:
                logger.warning(f"❌ Team member not found or not active: {email}")
                raise ValueError("Invalid credentials or account is not active")
            logger.debug(f"✅ Team member found: {team_member.full_name}")
            
            # Verify password
            logger.debug("🔐 Verifying password...")
            is_valid = verify_password(password, team_member.password_hash)
            
            if not is_valid:
                logger.warning("❌ Invalid password")
                raise ValueError("Invalid credentials")
            logger.debug("✅ Password verified")
            
            # Generate JWT tokens
            logger.debug("🔑 Generating tokens...")
            access_token = create_access_token(
                user_id=team_member.id,
                user_type="emergency_team"
            )
            # refresh_token = create_refresh_token(user_id=team_member.id)
            refresh_token = create_refresh_token(user_id=team_member.id, user_type="emergency_team")
            logger.debug("✅ Tokens generated")
            
            logger.info(f"✅ Emergency team login successful for {email}")
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
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.exception(f"❌ Emergency team login failed")
            raise
    
    async def login_team_member_by_phone(
        self,
        phone_number: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Login emergency team member with phone number and password.
        
        Alternative login method using phone number instead of email.
        """
        logger.info(f"🔑 Starting emergency team login by phone for {phone_number}")
        
        try:
            # Get active team member by phone
            team_member = await self.team_repo.get_active_team_member_by_phone(
                phone_number
            )
            
            if not team_member:
                logger.warning(f"❌ Team member not found: {phone_number}")
                raise ValueError("Invalid credentials or account is not active")
            
            # Verify password
            is_valid = verify_password(password, team_member.password_hash)
            
            if not is_valid:
                logger.warning("❌ Invalid password")
                raise ValueError("Invalid credentials")
            
            # Generate JWT tokens
            access_token = create_access_token(
                user_id=team_member.id,
                user_type="emergency_team"
            )
            
            # refresh_token = create_refresh_token(user_id=team_member.id)
            refresh_token = create_refresh_token(user_id=team_member.id, user_type="emergency_team")
            
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
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.exception(f"❌ Emergency team login by phone failed")
            raise
    
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
        Uses raw SQL for the lookup and update to stay consistent with
        the project's no-ORM rule.

        Args:
            team_member_id: UUID from the JWT token (get_current_team_member)
            old_password:   The member's current plaintext password
            new_password:   The desired new plaintext password (already validated
                            by the Pydantic schema before this is called)

        Raises:
            ValueError: Team member not found / old password wrong / account inactive
        """
        from sqlalchemy import text
        from datetime import datetime

        logger.info(f"🔐 Changing password for team member {team_member_id}")

        # ── 1. Fetch current password hash + status ──────────────────────────
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

        # ── 2. Verify old password ────────────────────────────────────────────
        if not verify_password(old_password, member["password_hash"]):
            raise ValueError("Current password is incorrect")

        # ── 3. Hash + persist new password ───────────────────────────────────
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
        team_member_id: str
    ) -> Dict[str, str]:
        """Deactivate a team member account."""
        team_member = await self.team_repo.deactivate_team_member(team_member_id)

        if not team_member:
            raise ValueError("Team member not found")

        await self.session.commit()

        return {"message": "Team member deactivated successfully"}