from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, Text, ARRAY, Enum as SQLEnum, Float
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from geoalchemy2 import Geography
import enum

from app.core.database import Base

class DisasterType(str, enum.Enum):
    FLOOD = "flood"
    FIRE = "fire"
    ACCIDENT = "accident"
    MEDICAL = "medical"
    OTHER = "other"

class DisasterSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class DisasterStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"

class Disaster(Base):
    __tablename__ = "disasters"

    disaster_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Geographic location using PostGIS
    location: Mapped[str] = mapped_column(
        Geography(geometry_type='POINT', srid=4326),
        nullable=False
    )

    # Denormalized lat/lng for easier access
    location_lat: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    location_lng: Mapped[float] = mapped_column(Float, nullable=False, index=True)

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

    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Reporter information
    reporter_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False
    )

    # Status tracking
    status: Mapped[str] = mapped_column(
        SQLEnum(DisasterStatus),
        default=DisasterStatus.PENDING.value,
        nullable=False,
        index=True
    )

    # Image storage (Azure Blob URLs)
    image_urls: Mapped[list] = mapped_column(
        ARRAY(String),
        nullable=True,
        default=[]
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True
    )

    # Relationship to User
    reporter: Mapped["User"] = relationship("User", back_populates="reported_disasters")
