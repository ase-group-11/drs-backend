# File: app/services/user_service.py
"""
User service for user registration and login.

ENHANCED VERSION with detailed logging for debugging.
"""

from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.repositories.user_repository import UserRepository
from app.services.otp_service import send_otp_code, verify_otp
from app.services.registration_cache import (
    store_registration_data,
    get_registration_data,
    delete_registration_data
)
from app.auth.jwt_handler import create_access_token, create_refresh_token
from app.db.models.user import User
from app.db.models.enums import UserStatus
from app.core.config import settings

# Setup logging
logger = logging.getLogger(__name__)


class UserService:
    """User service for authentication and user management."""
    
    def __init__(self, session: AsyncSession):
        """Initialize user service."""
        self.session = session
        self.user_repo = UserRepository(session)
    
    async def register_user(
        self,
        phone_number: str,
        full_name: str,
        email: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Register a new user (send OTP).
        
        Enhanced with detailed logging for debugging.
        """
        logger.info(f"📝 Starting registration for phone: {phone_number}")
        
        try:
            # Step 1: Check phone number
            logger.debug(f"🔍 Checking if phone {phone_number} exists...")
            if await self.user_repo.phone_exists(phone_number):
                logger.warning(f"❌ Phone {phone_number} already registered")
                raise ValueError(f"Phone number {phone_number} is already registered")
            logger.debug("✅ Phone number available")
            
            # Step 2: Check email
            if email:
                logger.debug(f"🔍 Checking if email {email} exists...")
                if await self.user_repo.email_exists(email):
                    logger.warning(f"❌ Email {email} already registered")
                    raise ValueError(f"Email {email} is already registered")
                logger.debug("✅ Email available")
            
            # Step 3: Store registration data in cache
            logger.info(f"💾 Storing registration data in Redis cache...")
            try:
                await store_registration_data(
                    phone_number=phone_number,
                    full_name=full_name,
                    email=email
                )
                # await store_registration_data(
                #     phone_number=phone_number,
                #     data={
                #         "full_name": full_name,
                #         "email": email,
                #         "user_type": "user"
                #     }
                # )
                logger.info("✅ Registration data cached successfully")
            except Exception as cache_error:
                logger.error(f"❌ Failed to cache registration data: {cache_error}")
                raise Exception(f"Cache error: {str(cache_error)}")
            
            # Step 4: Generate and send OTP
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
            
            logger.info(f"✅ Registration successful for {phone_number}")
            return {
                "message": f"OTP sent successfully to {phone_number}",
                "phone_number": phone_number
            }
            
        except ValueError as e:
            # Re-raise validation errors as-is
            raise
        except Exception as e:
            logger.exception(f"❌ Registration failed with unexpected error")
            raise
    
    async def verify_registration(
        self,
        phone_number: str,
        otp: str
    ) -> Dict[str, Any]:
        """
        Verify OTP and create user account.
        
        Enhanced with detailed logging.
        """
        logger.info(f"🔐 Starting registration verification for {phone_number}")
        
        try:
            # Step 1: Verify OTP
            logger.debug(f"🔍 Verifying OTP...")
            is_valid = await verify_otp(phone_number, otp)
            
            if not is_valid:
                logger.warning(f"❌ Invalid or expired OTP for {phone_number}")
                raise ValueError("Invalid or expired OTP")
            logger.debug("✅ OTP verified")
            
            # Step 2: Retrieve registration data from cache
            logger.debug(f"💾 Retrieving registration data from cache...")
            reg_data = await get_registration_data(phone_number)
            
            if not reg_data:
                logger.error(f"❌ No registration data found for {phone_number}")
                raise ValueError(
                    "Registration data not found. Please register again."
                )
            
            logger.debug(f"✅ Retrieved data: {reg_data.get('full_name')}")

            print(reg_data)
            
            # Step 3: Create user
            logger.info(f"👤 Creating user account...")
            user = await self.user_repo.create(
                phone_number=reg_data["phone_number"],
                full_name=reg_data["full_name"],
                email=reg_data.get("email"),
                status=UserStatus.ACTIVE
            )
            logger.info(f"✅ User created: {user.id}")
            
            # Step 4: Delete registration cache
            logger.debug("🗑️  Cleaning up registration cache...")
            await delete_registration_data(phone_number)
            
            # Step 5: Commit transaction
            await self.session.commit()
            await self.session.refresh(user)
            logger.debug("✅ Transaction committed")
            
            # Step 6: Generate JWT tokens
            logger.debug("🔑 Generating JWT tokens...")
            access_token = create_access_token(
                user_id=user.id,
                user_type="user"
            )
            refresh_token = create_refresh_token(user_id=user.id)
            logger.debug("✅ Tokens generated")
            
            logger.info(f"✅ Registration completed for {phone_number}")
            return {
                "user": {
                    "id": user.id,
                    "phone_number": user.phone_number,
                    "full_name": user.full_name,
                    "email": user.email,
                    "status": user.status.value,
                    "created_at": user.created_at.isoformat()
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
            logger.exception(f"❌ Verification failed")
            raise
    
    async def login_user(self, phone_number: str) -> Dict[str, str]:
        """
        Initiate user login (send OTP).
        
        Enhanced with detailed logging.
        """
        logger.info(f"🔑 Starting login for {phone_number}")
        
        try:
            # Check if user exists and is active
            logger.debug(f"🔍 Checking if user exists and is active...")
            user = await self.user_repo.get_active_user_by_phone(phone_number)
            
            if not user:
                logger.warning(f"❌ User not found or not active: {phone_number}")
                raise ValueError(
                    "User not found or account is not active. "
                    "Please register first."
                )
            logger.debug(f"✅ User found: {user.full_name}")
            
            # Generate and send OTP
            logger.info(f"📱 Generating and sending OTP...")
            otp = await send_otp_code(phone_number)
            
            if not otp:
                logger.error("❌ send_otp_code returned None/False")
                raise Exception("Failed to send OTP. Please try again later.")
            
            logger.info(f"✅ OTP sent: {otp[:2]}****")
            return {
                "message": f"OTP sent successfully to {phone_number}",
                "phone_number": phone_number
            }
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.exception(f"❌ Login failed")
            raise
    
    async def verify_login(
        self,
        phone_number: str,
        otp: str
    ) -> Dict[str, Any]:
        """
        Verify OTP and complete login.
        
        Enhanced with detailed logging.
        """
        logger.info(f"🔐 Starting login verification for {phone_number}")
        
        try:
            # Verify OTP
            logger.debug("🔍 Verifying OTP...")
            is_valid = await verify_otp(phone_number, otp)
            
            if not is_valid:
                logger.warning(f"❌ Invalid or expired OTP")
                raise ValueError("Invalid or expired OTP")
            logger.debug("✅ OTP verified")
            
            # Get active user
            logger.debug("🔍 Getting user...")
            user = await self.user_repo.get_active_user_by_phone(phone_number)
            
            if not user:
                logger.error(f"❌ User not found: {phone_number}")
                raise ValueError("User not found or account is not active")
            logger.debug(f"✅ User found: {user.full_name}")
            
            # Generate JWT tokens
            logger.debug("🔑 Generating tokens...")
            access_token = create_access_token(
                user_id=user.id,
                user_type="user"
            )
            refresh_token = create_refresh_token(user_id=user.id)
            logger.debug("✅ Tokens generated")
            
            logger.info(f"✅ Login completed for {phone_number}")
            return {
                "user": {
                    "id": user.id,
                    "phone_number": user.phone_number,
                    "full_name": user.full_name,
                    "email": user.email,
                    "status": user.status.value,
                    "created_at": user.created_at.isoformat()
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
            logger.exception(f"❌ Login verification failed")
            raise
    
    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        return await self.user_repo.get_by_id(user_id)
    
    async def get_user_by_phone(self, phone_number: str) -> Optional[User]:
        """Get user by phone number."""
        return await self.user_repo.get_by_phone_number(phone_number)