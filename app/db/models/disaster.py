# File: app/db/models/disaster.py
"""
Disaster model for emergency disaster reporting.

Represents disaster reports submitted by users including:
- Geographic location (PostGIS geography)
- Disaster type and severity
- Images and description
- Assignment and resolution tracking
"""

from typing import Optional, TYPE_CHECKING
from sqlalchemy import String, ForeignKey, Text, ARRAY, Enum as SQLEnum, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from geoalchemy2 import Geography

from app.db.models.base import Base
from app.db.models.enums import DisasterType, DisasterSeverity, DisasterStatus

if TYPE_CHECKING:
    from app.db.models.user import User


class Disaster(Base):
    """
    Disaster report model.

    Inherits from Base:
    - id (UUID primary key)
    - created_at, updated_at, deleted_at
    - soft_delete(), restore(), is_deleted

    Additional fields:
    - Geographic location (PostGIS point)
    - Disaster classification
    - Reporter information
    - Status tracking
    - Optional images
    """

    __tablename__ = "disasters"

    # Geographic location using PostGIS
    location: Mapped[str] = mapped_column(
        Geography(geometry_type='POINT', srid=4326),
        nullable=False
    )

    # Denormalized lat/lng for easier access and indexing
    location_lat: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    location_lng: Mapped[float] = mapped_column(Float, nullable=False, index=True)

    # Disaster classification
    disaster_type: Mapped[str] = mapped_column(
        SQLEnum(DisasterType),
        nullable=False,
        index=True
    )

    severity: Mapped[str] = mapped_column(
        SQLEnum(DisasterSeverity),
        nullable=False,
        index=True
    )

    # Disaster details
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Reporter information (foreign key to users table)
    reporter_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )

    # Status tracking
    status: Mapped[str] = mapped_column(
        SQLEnum(DisasterStatus),
        default=DisasterStatus.PENDING.value,
        nullable=False,
        index=True
    )

    # Image storage (Azure Blob URLs) - Fixed mutable default
    image_urls: Mapped[Optional[list]] = mapped_column(
        ARRAY(String),
        nullable=True,
        default=None
    )

    # Relationships
    reporter: Mapped["User"] = relationship("User", back_populates="reported_disasters")

    def __repr__(self) -> str:
        """String representation of the disaster."""
        return f"<Disaster(id={self.id}, type={self.disaster_type}, severity={self.severity}, status={self.status})>"
