# File: app/models/base.py
"""
Base SQLAlchemy model with common fields.

All models inherit from Base and include:
- id (UUID primary key)
- created_at (timestamp)
- updated_at (timestamp)
- deleted_at (soft delete timestamp)
"""

from datetime import datetime
from typing import Any, Optional
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
import uuid


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy models.
    
    Provides common fields that all models should have:
    - id: UUID primary key
    - created_at: Creation timestamp
    - updated_at: Last update timestamp
    - deleted_at: Soft delete timestamp (NULL if not deleted)
    """
    
    # Common fields for all models
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        unique=True,
        nullable=False,
        index=True
    )
    
    # created_at: Mapped[datetime] = mapped_column(
    #     DateTime(timezone=True),
    #     server_default=func.now(),
    #     nullable=False
    # )
    
    # updated_at: Mapped[datetime] = mapped_column(
    #     DateTime(timezone=True),
    #     server_default=func.now(),
    #     onupdate=func.now(),
    #     nullable=False
    # )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),          # Python-side fallback
        server_default=func.now(),   # DB-side default
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),          # Python-side fallback
        server_default=func.now(),   # DB-side default
        onupdate=func.now(),
        nullable=False
    )
    
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None
    )
    
    def soft_delete(self) -> None:
        """
        Soft delete the record by setting deleted_at timestamp.
        
        Soft deletes allow records to be recovered if needed.
        Use this instead of actual DELETE for audit trail.
        """
        self.deleted_at = datetime.utcnow()
    
    def restore(self) -> None:
        """
        Restore a soft-deleted record.
        
        Sets deleted_at back to NULL.
        """
        self.deleted_at = None
    
    @property
    def is_deleted(self) -> bool:
        """
        Check if the record is soft-deleted.
        
        Returns:
            bool: True if deleted_at is set, False otherwise
        """
        return self.deleted_at is not None
    
    def __repr__(self) -> str:
        """String representation of the model."""
        return f"<{self.__class__.__name__}(id={self.id})>"