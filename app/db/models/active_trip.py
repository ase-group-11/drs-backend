"""
DROP IN: app/db/models/active_trip.py

Then add to app/db/models/__init__.py:
    from app.db.models.active_trip import ActiveTrip
    and "ActiveTrip" to __all__

One row per user who has set a destination in the mobile app.
get_users_in_affected_area() queries using a lat/lon bounding box
computed from disaster coordinates + impact_radius_km.
No region_id — lat/lng are sufficient.
"""

from datetime import datetime
from sqlalchemy import String, Float, DateTime, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.models.base import Base


class ActiveTrip(Base):

    __tablename__ = "active_trips"

    # One active trip per user — upsert conflict target
    user_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    # Current GPS position
    current_lat: Mapped[float] = mapped_column(Float, nullable=False)
    current_lng: Mapped[float] = mapped_column(Float, nullable=False)

    # Destination
    dest_lat: Mapped[float] = mapped_column(Float, nullable=False)
    dest_lng: Mapped[float] = mapped_column(Float, nullable=False)

    # "emergency" > "public_transport" > "general"  (PRIORITY_ORDER in traffic_distribution.py)
    vehicle_type: Mapped[str] = mapped_column(String, nullable=False, default="general")

    # Auto-expire after 4 hours. text() is required — func.now() + interval
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now() + interval '4 hours'"),
    )

    __table_args__ = (
        Index("ix_active_trips_lat_lng", "current_lat", "current_lng"),
        Index("ix_active_trips_expires", "expires_at"),
    )