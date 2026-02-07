"""

Disaster model for tracking emergency events with user reporting.

Supports:
- User-reported disasters (from residents via mobile app)

"""

from sqlalchemy import String, Text, JSON, Enum as SQLEnum, Index, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from geoalchemy2 import Geometry
from typing import Optional, List

from app.db.models.base import Base
from app.db.models.enums import DisasterType, DisasterSeverity, DisasterStatus, DisasterReportStatus


class Disaster(Base):
    """
    Disaster event model with PostGIS geospatial support and user reporting.
    
    Tracks emergency events including:
    - User-reported incidents with photos
    - Official disaster declarations
    - Location tracking with PostGIS
    - Validation and verification workflow
    
    Fields inherited from Base:
    - id: UUID primary key
    - created_at: Creation timestamp (report time)
    - updated_at: Last update timestamp
    - deleted_at: Soft delete timestamp
    
    Additional fields:
    - type: Type of disaster (flood, fire, earthquake, etc.)
    - severity: Severity level (low, medium, high, critical)
    - status: Disaster status (active, monitoring, resolved, archived)
    - report_status: Report validation status (pending, verified, rejected)
    - location: Geographic point (PostGIS POINT geometry)
    - affected_area: Name of affected area/region
    - description: Detailed description
    - tracking_id: User-facing tracking ID (for users to track their reports)
    - reporter_id: User who reported this disaster (nullable for official reports)
    - verified_by_id: Emergency team member who verified (nullable)
    - is_user_reported: Whether this was reported by a user vs official source
    - metadata: Additional flexible data
    """
    
    __tablename__ = "disasters"
    
    # Disaster classification
    type: Mapped[DisasterType] = mapped_column(
        SQLEnum(DisasterType, name="disaster_type"),
        nullable=False,
        index=True
    )
    
    severity: Mapped[DisasterSeverity] = mapped_column(
        SQLEnum(DisasterSeverity, name="disaster_severity"),
        nullable=False,
        index=True
    )
    
    status: Mapped[DisasterStatus] = mapped_column(
        SQLEnum(DisasterStatus, name="disaster_status"),
        default=DisasterStatus.ACTIVE,
        nullable=False,
        index=True
    )
    
    # NEW: Report validation status
    report_status: Mapped[DisasterReportStatus] = mapped_column(
        SQLEnum(DisasterReportStatus, name="disaster_report_status"),
        default=DisasterReportStatus.PENDING,
        nullable=False,
        index=True
    )
    
    # Geospatial data (PostGIS)
    location: Mapped[Geometry] = mapped_column(
        Geometry(
            geometry_type='POINT',
            srid=4326,  # WGS 84 coordinate system
            spatial_index=True
        ),
        nullable=False,
        comment="Geographic location (point geometry) using WGS 84"
    )
    
    # Textual information
    affected_area: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Name of affected area/region"
    )
    
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Detailed description of the disaster"
    )
    
    # NEW: Tracking and reporting
    tracking_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="User-facing tracking ID (e.g., DIS-2026-001234)"
    )
    
    is_user_reported: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
        comment="True if reported by user, False if from official source"
    )
    
    # NEW: User relationships (Foreign Keys)
    reporter_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User who reported this disaster"
    )
    
    verified_by_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("emergency_teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Emergency team member who verified this report"
    )
    
    # Additional metadata (flexible JSON field)
    metadata: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Additional metadata (casualties, damage estimates, etc.)"
    )
    
    # NEW: Relationships
    reporter = relationship(
        "User",
        foreign_keys=[reporter_id],
        back_populates="reported_disasters"
    )
    
    verified_by = relationship(
        "EmergencyTeam",
        foreign_keys=[verified_by_id],
        back_populates="verified_disasters"
    )
    
    photos = relationship(
        "DisasterPhoto",
        back_populates="disaster",
        cascade="all, delete-orphan"
    )
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_disasters_type_severity', 'type', 'severity'),
        Index('idx_disasters_status_created', 'status', 'created_at'),
        Index('idx_disasters_location', 'location', postgresql_using='gist'),
        Index('idx_disasters_active', 'status', postgresql_where=(status == DisasterStatus.ACTIVE)),
        Index('idx_disasters_tracking_id', 'tracking_id'),
        Index('idx_disasters_reporter', 'reporter_id'),
        Index('idx_disasters_report_status', 'report_status'),
        Index('idx_disasters_user_reported', 'is_user_reported', 'report_status'),
    )
    
    def __repr__(self) -> str:
        return (
            f"<Disaster(id={self.id}, tracking_id={self.tracking_id}, "
            f"type={self.type}, severity={self.severity}, status={self.status})>"
        )
    
    # Status transitions
    def resolve(self) -> None:
        """Mark disaster as resolved."""
        self.status = DisasterStatus.RESOLVED
    
    def archive(self) -> None:
        """Archive the disaster record."""
        self.status = DisasterStatus.ARCHIVED
    
    def start_monitoring(self) -> None:
        """Change status to monitoring."""
        self.status = DisasterStatus.MONITORING
    
    # Report validation
    def verify(self, verified_by_team_member_id: str) -> None:
        """Verify a user-reported disaster."""
        self.report_status = DisasterReportStatus.VERIFIED
        self.verified_by_id = verified_by_team_member_id
    
    def reject(self, verified_by_team_member_id: str) -> None:
        """Reject a user-reported disaster (false report)."""
        self.report_status = DisasterReportStatus.REJECTED
        self.verified_by_id = verified_by_team_member_id
    
    def mark_duplicate(self) -> None:
        """Mark as duplicate report."""
        self.report_status = DisasterReportStatus.DUPLICATE
    
    # Properties
    @property
    def is_active(self) -> bool:
        """Check if disaster is currently active."""
        return self.status == DisasterStatus.ACTIVE
    
    @property
    def is_critical(self) -> bool:
        """Check if disaster has critical severity."""
        return self.severity == DisasterSeverity.CRITICAL
    
    @property
    def is_verified(self) -> bool:
        """Check if report has been verified."""
        return self.report_status == DisasterReportStatus.VERIFIED
    
    @property
    def needs_verification(self) -> bool:
        """Check if report is pending verification."""
        return self.report_status == DisasterReportStatus.PENDING


class DisasterPhoto(Base):
    """
    Photos/images attached to disaster reports.
    
    Stores cloud URLs for photos uploaded by users when reporting disasters.
    Multiple photos can be attached to a single disaster report.
    """
    
    __tablename__ = "disaster_photos"
    
    # Foreign key to disaster
    disaster_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("disasters.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Cloud storage URL
    image_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Cloud storage URL for the photo"
    )
    
    # Optional caption/description
    caption: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Optional caption for the photo"
    )
    
    # Image metadata
    file_size: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment="File size in bytes"
    )
    
    mime_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="MIME type (e.g., image/jpeg, image/png)"
    )
    
    # Relationship
    disaster = relationship("Disaster", back_populates="photos")
    
    # Indexes
    __table_args__ = (
        Index('idx_disaster_photos_disaster_id', 'disaster_id'),
    )
    
    def __repr__(self) -> str:
        return f"<DisasterPhoto(id={self.id}, disaster_id={self.disaster_id})>"