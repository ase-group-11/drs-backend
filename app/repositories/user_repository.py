# File: app/repositories/user_repository.py
"""
User repository for user-specific database operations.

Extends BaseRepository with user-specific queries.
"""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.db.models.enums import UserStatus
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """
    User repository with user-specific queries.
    
    Provides CRUD operations plus user-specific methods.
    """
    
    def __init__(self, session: AsyncSession):
        """Initialize user repository."""
        super().__init__(User, session)
    
    async def get_by_phone_number(self, phone_number: str) -> Optional[User]:
        """
        Get user by phone number.
        
        Args:
            phone_number: User's phone number
            
        Returns:
            Optional[User]: User instance or None
        """
        return await self.get_by_field("phone_number", phone_number)
    
    async def get_by_email(self, email: str) -> Optional[User]:
        """
        Get user by email.
        
        Args:
            email: User's email address
            
        Returns:
            Optional[User]: User instance or None
        """
        return await self.get_by_field("email", email)
    
    async def phone_exists(self, phone_number: str) -> bool:
        """
        Check if phone number is already registered.
        
        Args:
            phone_number: Phone number to check
            
        Returns:
            bool: True if exists, False otherwise
        """
        return await self.exists("phone_number", phone_number)
    
    async def email_exists(self, email: str) -> bool:
        """
        Check if email is already registered.
        
        Args:
            email: Email to check
            
        Returns:
            bool: True if exists, False otherwise
        """
        if not email:
            return False
        return await self.exists("email", email)
    
    async def get_active_user_by_phone(self, phone_number: str) -> Optional[User]:
        """
        Get active user by phone number.
        
        Only returns user if status is ACTIVE.
        
        Args:
            phone_number: User's phone number
            
        Returns:
            Optional[User]: Active user or None
        """
        result = await self.session.execute(
            select(User).where(
                User.phone_number == phone_number,
                User.status == UserStatus.ACTIVE,
                User.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()
    
    async def activate_user(self, user_id: str) -> Optional[User]:
        """
        Activate a pending user.
        
        Args:
            user_id: User ID
            
        Returns:
            Optional[User]: Activated user or None
        """
        user = await self.get_by_id(user_id)
        if not user:
            return None
        
        user.activate()
        await self.session.flush()
        await self.session.refresh(user)
        return user
    
    async def deactivate_user(self, user_id: str) -> Optional[User]:
        """
        Deactivate a user.
        
        Args:
            user_id: User ID
            
        Returns:
            Optional[User]: Deactivated user or None
        """
        user = await self.get_by_id(user_id)
        if not user:
            return None
        
        user.deactivate()
        await self.session.flush()
        await self.session.refresh(user)
        return user
    
    async def get_pending_users(self, skip: int = 0, limit: int = 100):
        """
        Get all pending users (awaiting verification).
        
        Args:
            skip: Number of records to skip
            limit: Maximum records to return
            
        Returns:
            List[User]: List of pending users
        """
        return await self.get_all(
            skip=skip,
            limit=limit,
            filters={"status": UserStatus.PENDING}
        )