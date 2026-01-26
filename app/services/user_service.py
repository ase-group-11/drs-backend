# File: app/services/user_service.py
"""
User service for user registration and login.

Business logic for:
- User registration with OTP
- OTP verification and user creation
- User login with OTP
- Token generation
"""

from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.user_repository import UserRepository
from app.services.otp_service import send_otp_code, verify_otp
from app.auth.jwt_handler import create_access_token, create_refresh_token
from app.models.user import User
from app.models.enums import UserStatus
from app.core.config import settings


class UserService:
    """
    User service for authentication and user management.
    
    Handles registration, login, and user operations.
    """
    
    def __init__(self, session: AsyncSession):
        """
        Initialize user service.
        
        Args:
            session: Database session
        """
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
        
        Steps:
        1. Check if phone number already exists
        2. Check if email already exists (if provided)
        3. Generate and send OTP
        4. Return success message
        
        Args:
            phone_number: User's phone number (E.164 format)
            full_name: User's full name
            email: Optional email address
            
        Returns:
            Dict with success message
            
        Raises:
            ValueError: If phone or email already exists
            Exception: If OTP sending fails
            
        Example:
            >>> result = await service.register_user("+1234567890", "John Doe")
            >>> print(result)
            {"message": "OTP sent to +1234567890"}
        """
        # Check if phone number already registered
        if await self.user_repo.phone_exists(phone_number):
            raise ValueError(f"Phone number {phone_number} is already registered")
        
        # Check if email already registered (if provided)
        if email and await self.user_repo.email_exists(email):
            raise ValueError(f"Email {email} is already registered")
        
        # Generate and send OTP
        otp = await send_otp_code(phone_number)
        
        if not otp:
            raise Exception("Failed to send OTP. Please try again later.")
        
        return {
            "message": f"OTP sent successfully to {phone_number}",
            "phone_number": phone_number
        }
    
    async def verify_registration(
        self,
        phone_number: str,
        otp: str,
        full_name: str,
        email: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Verify OTP and create user account.
        
        Steps:
        1. Verify OTP from Redis
        2. Create user with ACTIVE status
        3. Generate JWT tokens
        4. Return user data + tokens
        
        Args:
            phone_number: User's phone number
            otp: OTP code to verify
            full_name: User's full name
            email: Optional email
            
        Returns:
            Dict with user data and tokens
            
        Raises:
            ValueError: If OTP is invalid or expired
            
        Example:
            >>> result = await service.verify_registration(
            ...     "+1234567890", "123456", "John Doe"
            ... )
            >>> print(result["user"]["id"])
            "550e8400-e29b-41d4-a716-446655440000"
        """
        # Verify OTP
        is_valid = await verify_otp(phone_number, otp)
        
        if not is_valid:
            raise ValueError("Invalid or expired OTP")
        
        # Create user with ACTIVE status
        user = await self.user_repo.create(
            phone_number=phone_number,
            full_name=full_name,
            email=email,
            status=UserStatus.ACTIVE
        )
        
        # Commit the transaction
        await self.session.commit()
        await self.session.refresh(user)
        
        # Generate JWT tokens
        access_token = create_access_token(
            user_id=user.id,
            user_type="user"
        )
        
        refresh_token = create_refresh_token(user_id=user.id)
        
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
    
    async def login_user(self, phone_number: str) -> Dict[str, str]:
        """
        Initiate user login (send OTP).
        
        Steps:
        1. Check if user exists and is active
        2. Generate and send OTP
        3. Return success message
        
        Args:
            phone_number: User's phone number
            
        Returns:
            Dict with success message
            
        Raises:
            ValueError: If user not found or not active
            Exception: If OTP sending fails
            
        Example:
            >>> result = await service.login_user("+1234567890")
            >>> print(result)
            {"message": "OTP sent to +1234567890"}
        """
        # Check if user exists and is active
        user = await self.user_repo.get_active_user_by_phone(phone_number)
        
        if not user:
            raise ValueError(
                "User not found or account is not active. "
                "Please register first."
            )
        
        # Generate and send OTP
        otp = await send_otp_code(phone_number)
        
        if not otp:
            raise Exception("Failed to send OTP. Please try again later.")
        
        return {
            "message": f"OTP sent successfully to {phone_number}",
            "phone_number": phone_number
        }
    
    async def verify_login(
        self,
        phone_number: str,
        otp: str
    ) -> Dict[str, Any]:
        """
        Verify OTP and complete login.
        
        Steps:
        1. Verify OTP
        2. Get active user
        3. Generate JWT tokens
        4. Return user data + tokens
        
        Args:
            phone_number: User's phone number
            otp: OTP code to verify
            
        Returns:
            Dict with user data and tokens
            
        Raises:
            ValueError: If OTP invalid or user not found
            
        Example:
            >>> result = await service.verify_login("+1234567890", "123456")
            >>> print(result["tokens"]["access_token"])
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
        """
        # Verify OTP
        is_valid = await verify_otp(phone_number, otp)
        
        if not is_valid:
            raise ValueError("Invalid or expired OTP")
        
        # Get active user
        user = await self.user_repo.get_active_user_by_phone(phone_number)
        
        if not user:
            raise ValueError("User not found or account is not active")
        
        # Generate JWT tokens
        access_token = create_access_token(
            user_id=user.id,
            user_type="user"
        )
        
        refresh_token = create_refresh_token(user_id=user.id)
        
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
    
    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        """
        Get user by ID.
        
        Args:
            user_id: User ID (UUID)
            
        Returns:
            Optional[User]: User instance or None
        """
        return await self.user_repo.get_by_id(user_id)
    
    async def get_user_by_phone(self, phone_number: str) -> Optional[User]:
        """
        Get user by phone number.
        
        Args:
            phone_number: Phone number
            
        Returns:
            Optional[User]: User instance or None
        """
        return await self.user_repo.get_by_phone_number(phone_number)