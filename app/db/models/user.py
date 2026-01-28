# File: app/models/user.py
"""
User model for regular users.

Regular users authenticate using OTP (SMS-based).
They can submit emergency requests and track their status.
"""

from sqlalchemy import String, Enum as SQLEnum, Index
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional
from app.db.models.base import Base
from app.db.models.enums import UserStatus, UserRole


class User(Base):
    """
    Regular user model (OTP-based authentication).
    
    Users authenticate via OTP sent to their phone number.
    No password is stored for regular users.
    
    Fields:
    - phone_number: Unique phone number (E.164 format)
    - full_name: User's full name
    - email: Optional email address
    - status: Account status (pending, active, inactive, etc.)
    """
    
    __tablename__ = "users"
    
    # Phone number (unique, E.164 format: +1234567890)
    phone_number: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
        index=True
    )
    
    # User details
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )
    
    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True
    )

    role: Mapped[UserRole] = mapped_column(
        SQLEnum(UserRole, name = "user_role"),
        default=UserRole.RESIDENT,
        nullable=False,
        index=True
    )
    
    # Account status
    status: Mapped[UserStatus] = mapped_column(
        SQLEnum(UserStatus, name="user_status"),
        default=UserStatus.PENDING,
        nullable=False,
        index=True
    )
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_user_phone_status', 'phone_number', 'status'),
        Index('idx_user_status_created', 'status', 'created_at'),
    )
    
    def __repr__(self) -> str:
        return f"<User(id={self.id}, phone={self.phone_number}, status={self.status})>"
    
    def activate(self) -> None:
        """
        Activate user account after OTP verification.
        
        Changes status from PENDING to ACTIVE.
        """
        self.status = UserStatus.ACTIVE
    
    def deactivate(self) -> None:
        """
        Deactivate user account.
        
        Changes status to INACTIVE. Account can be reactivated.
        """
        self.status = UserStatus.INACTIVE
    
    def suspend(self) -> None:
        """
        Suspend user account (security/policy violation).
        
        Changes status to SUSPENDED.
        """
        self.status = UserStatus.SUSPENDED
    
    @property
    def is_active(self) -> bool:
        """Check if user account is active."""
        return self.status == UserStatus.ACTIVE
    
    @property
    def is_pending(self) -> bool:
        """Check if user account is pending verification."""
        return self.status == UserStatus.PENDING