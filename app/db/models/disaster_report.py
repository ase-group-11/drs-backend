# # File: app/models/disaster_report.py
# """
# Disaster Report model for emergency incident reporting.

# Users can submit reports about emergencies with details like:
# - Location, disaster type, severity
# - Media attachments (photos/videos)
# - Number of people affected
# - Additional critical details
# """

# from datetime import datetime
# from typing import Optional, List, TYPE_CHECKING
# from sqlalchemy import String, Integer, Float, Boolean, Text, JSON, ForeignKey, Enum as SQLEnum, Index
# from sqlalchemy.orm import Mapped, mapped_column, relationship
# from sqlalchemy.dialects.postgresql import UUID
# from app.db.models.base import Base
# from app.db.models.enums import DisasterType, Department, DisasterReportStatus, DisasterStatus, DisasterSeverity

# if TYPE_CHECKING:
#     from app.db.models.user import User
#     from app.db.models.emergency_team import EmergencyTeam


# class DisasterReport(Base):
#     """
#     Disaster report model for emergency incidents.
    
#     Users submit reports about emergencies/disasters with:
#     - Location and disaster details
#     - Severity assessment
#     - Media evidence (photos/videos)
#     - Impact information (people affected)
#     - Critical situation flags
#     - Status tracking and assignment
    
#     Fields:
#     - user_id: Reporter's user ID
#     - location_address: Human-readable address
#     - location_latitude: GPS latitude
#     - location_longitude: GPS longitude
#     - disaster_type: Type of emergency
#     - severity: Severity level (low, medium, high, critical)
#     - description: Detailed description of the situation
#     - media_urls: JSON array of photo/video URLs
#     - people_affected: Number of people affected
#     - multiple_casualties: Flag for multiple casualties
#     - structural_damage: Flag for building damage
#     - hazmat_involved: Flag for hazardous materials
#     - road_blocked: Flag for blocked road access
#     - status: Report lifecycle status
#     - assigned_to_id: Assigned emergency team member ID
#     - assigned_department: Department handling the report
#     - response_time: When emergency team started responding
#     - resolved_time: When the emergency was resolved
#     - resolution_notes: Notes about how it was resolved
#     """
    
#     __tablename__ = "disaster_reports"
    
#     # Reporter reference
#     user_id: Mapped[str] = mapped_column(
#         UUID(as_uuid=False),
#         ForeignKey("users.id", ondelete="CASCADE"),
#         nullable=False,
#         index=True
#     )

#     severity: Mapped[str] = mapped_column(
#         SQLEnum(DisasterSeverity, name = "disaster_severity"), 
#         nullable=False,

#     )
    
#     # Location information
#     location_address: Mapped[str] = mapped_column(
#         Text,
#         nullable=False
#     )
    
#     location_latitude: Mapped[Optional[float]] = mapped_column(
#         Float,
#         nullable=True
#     )
    
#     location_longitude: Mapped[Optional[float]] = mapped_column(
#         Float,
#         nullable=True
#     )
    
#     # Disaster information
#     disaster_type: Mapped[DisasterType] = mapped_column(
#         SQLEnum(DisasterType, name="disaster_type"),
#         nullable=False,
#         index=True
#     )
    
    
#     description: Mapped[str] = mapped_column(
#         Text,
#         nullable=False
#     )
    
#     # Media attachments (stored as JSON array of URLs)
#     media_urls: Mapped[Optional[List[str]]] = mapped_column(
#         JSON,
#         nullable=True,
#         default=list
#     )
    
#     # Impact assessment
#     people_affected: Mapped[int] = mapped_column(
#         Integer,
#         nullable=False,
#         default=0
#     )
    
#     # Additional critical details (boolean flags)
#     multiple_casualties: Mapped[bool] = mapped_column(
#         Boolean,
#         nullable=False,
#         default=False
#     )
    
#     structural_damage: Mapped[bool] = mapped_column(
#         Boolean,
#         nullable=False,
#         default=False
#     )
    
#     hazmat_involved: Mapped[bool] = mapped_column(
#         Boolean,
#         nullable=False,
#         default=False
#     )
    
#     road_blocked: Mapped[bool] = mapped_column(
#         Boolean,
#         nullable=False,
#         default=False
#     )
    
#     # Status and assignment
#     status: Mapped[DisasterReportStatus] = mapped_column(
#         SQLEnum(DisasterReportStatus, name="report_status"),
#         default=DisasterReportStatus.PENDING,
#         nullable=False,
#         index=True
#     )
    
#     assigned_to_id: Mapped[Optional[str]] = mapped_column(
#         UUID(as_uuid=False),
#         ForeignKey("emergency_teams.id", ondelete="SET NULL"),
#         nullable=True,
#         index=True
#     )
    
#     assigned_department: Mapped[Optional[Department]] = mapped_column(
#         SQLEnum(Department, name="department"),
#         nullable=True,
#         index=True
#     )
    
#     # Timeline tracking
#     response_time: Mapped[Optional[datetime]] = mapped_column(
#         nullable=True
#     )
    
#     resolved_time: Mapped[Optional[datetime]] = mapped_column(
#         nullable=True
#     )
    
#     # Resolution details
#     resolution_notes: Mapped[Optional[str]] = mapped_column(
#         Text,
#         nullable=True
#     )
    
#     # Relationships
#     user: Mapped["User"] = relationship(
#         "User",
#         back_populates="disaster_reports",
#         lazy="select"
#     )
    
#     assigned_to: Mapped[Optional["EmergencyTeam"]] = relationship(
#         "EmergencyTeam",
#         back_populates="assigned_reports",
#         foreign_keys=[assigned_to_id],
#         lazy="select"
#     )
    
#     # Composite indexes for common queries
#     __table_args__ = (
#         # Index('idx_report_status_severity', 'status', 'severity'),
#         Index('idx_report_disaster_status', 'disaster_type', 'status'),
#         Index('idx_report_user_status', 'user_id', 'status'),
#         Index('idx_report_assigned', 'assigned_to_id', 'status'),
#         Index('idx_report_dept_status', 'assigned_department', 'status'),
#         Index('idx_report_created', 'created_at', 'status'),
#         Index('idx_report_location', 'location_latitude', 'location_longitude'),
#     )
    
#     def __repr__(self) -> str:
#         return (
#             f"<DisasterReport(id={self.id}, type={self.disaster_type} "
#             # f"severity={self.severity}, status={self.status})>"
#         )
    
#     # Status transition methods
#     def mark_under_review(self) -> None:
#         """Mark report as under review by emergency team."""
#         self.status = DisasterReportStatus.PENDING
    
#     def assign_to(
#         self, 
#         team_member_id: str, 
#         department: Department
#     ) -> None:
#         """
#         Assign report to an emergency team member.
        
#         Args:
#             team_member_id: ID of the emergency team member
#             department: Department handling the report
#         """
#         self.assigned_to_id = team_member_id
#         self.assigned_department = department
#         self.status = DisasterReportStatus.VERIFIED
    
#     def start_response(self) -> None:
#         """Mark when emergency team starts responding."""
#         self.status = DisasterReportStatus.PENDING
#         if not self.response_time:
#             self.response_time = datetime.utcnow()
    
#     def resolve(self, resolution_notes: Optional[str] = None) -> None:
#         """
#         Mark report as resolved.
        
#         Args:
#             resolution_notes: Optional notes about the resolution
#         """
#         self.status = DisasterStatus.RESOLVED
#         self.resolved_time = datetime.utcnow()
#         if resolution_notes:
#             self.resolution_notes = resolution_notes
    
#     def cancel(self, reason: Optional[str] = None) -> None:
#         """
#         Cancel the report.
        
#         Args:
#             reason: Optional reason for cancellation
#         """
#         self.status = DisasterReportStatus.REJECTED
#         if reason:
#             self.resolution_notes = f"Cancelled: {reason}"
    
#     def reject(self, reason: Optional[str] = None) -> None:
#         """
#         Reject the report (false alarm, duplicate, etc.).
        
#         Args:
#             reason: Optional reason for rejection
#         """
#         self.status = DisasterReportStatus.REJECTED
#         if reason:
#             self.resolution_notes = f"Rejected: {reason}"
    
#     # Property helpers
#     # @property
#     # def is_critical(self) -> bool:
#     #     """Check if report is critical severity."""
#     #     return self.severity == D.CRITICAL
    
#     @property
#     def is_high_priority(self) -> bool:
#         """
#         Check if report is high priority.
        
#         High priority = Critical severity OR (High severity + critical flags)
#         """
#         if self.severity == DisasterSeverity.CRITICAL:
#             return True
        
#         if self.severity == DisasterSeverity.HIGH:
#             critical_flags = (
#                 self.multiple_casualties or
#                 self.structural_damage or
#                 self.hazmat_involved
#             )
#             return critical_flags
        
#         return False
    
#     @property
#     def is_active(self) -> bool:
#         """Check if report is still active (not resolved/cancelled/rejected)."""
#         return self.status in (
#             DisasterStatus.ACTIVE,
#             DisasterStatus.MONITORING,
#         )
    
#     @property
#     def is_assigned(self) -> bool:
#         """Check if report is assigned to a team member."""
#         return self.assigned_to_id is not None
    
#     @property
#     def response_time_minutes(self) -> Optional[float]:
#         """
#         Calculate response time in minutes.
        
#         Returns:
#             Time between report creation and response start, or None if not responded
#         """
#         if not self.response_time:
#             return None
        
#         delta = self.response_time - self.created_at
#         return delta.total_seconds() / 60
    
#     @property
#     def resolution_time_minutes(self) -> Optional[float]:
#         """
#         Calculate total resolution time in minutes.
        
#         Returns:
#             Time between report creation and resolution, or None if not resolved
#         """
#         if not self.resolved_time:
#             return None
        
#         delta = self.resolved_time - self.created_at
#         return delta.total_seconds() / 60
    
#     @property
#     def has_media(self) -> bool:
#         """Check if report has media attachments."""
#         return bool(self.media_urls and len(self.media_urls) > 0)
    
#     @property
#     def critical_flags_count(self) -> int:
#         """Count how many critical flags are set."""
#         return sum([
#             self.multiple_casualties,
#             self.structural_damage,
#             self.hazmat_involved,
#             self.road_blocked
#         ])
    
#     def to_summary_dict(self) -> dict:
#         """
#         Create a summary dictionary for API responses.
        
#         Returns:
#             Dictionary with key report information
#         """
#         return {
#             "id": self.id,
#             "disaster_type": self.disaster_type.value,
#             "severity": self.severity.value,
#             "status": self.status.value,
#             "location": self.location_address,
#             "people_affected": self.people_affected,
#             # "is_critical": self.is_critical,
#             "is_high_priority": self.is_high_priority,
#             "has_media": self.has_media,
#             "critical_flags_count": self.critical_flags_count,
#             "created_at": self.created_at.isoformat(),
#             "response_time_minutes": self.response_time_minutes,
#             "resolution_time_minutes": self.resolution_time_minutes
#         }



"""
Disaster Report model - User submissions (immutable)
"""

from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, Integer, Boolean, Text, ForeignKey, Enum as SQLEnum, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geography
from app.db.models.base import Base
from app.db.models.enums import DisasterType, DisasterSeverity, DisasterReportStatus

if TYPE_CHECKING:
    from app.db.models.user import User
    from app.db.models.emergency_team import EmergencyTeam
    from app.db.models.disaster import Disaster, DisasterPhoto


class DisasterReport(Base):
    """
    Immutable record of user-submitted disaster report.
    
    Never modified after creation - preserves exactly what user reported.
    Links to verified Disaster if approved.
    """
    
    __tablename__ = "disaster_reports"
    
    # === REPORTER ===
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User who submitted this report"
    )
    
    # === WHAT USER REPORTED ===
    disaster_type: Mapped[DisasterType] = mapped_column(
        SQLEnum(DisasterType, name="disaster_type"),
        nullable=False,
        index=True,
        comment="Type of disaster reported by user"
    )
    
    severity: Mapped[DisasterSeverity] = mapped_column(
        SQLEnum(DisasterSeverity, name="disaster_severity"), 
        nullable=False,
        comment="Severity assessment by user"
    )
    
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="User's description of the disaster"
    )
    
    # === LOCATION (PostGIS) ===
    location: Mapped[Geography] = mapped_column(
        Geography(
            geometry_type='POINT',
            srid=4326,
            spatial_index=True
        ),
        nullable=False,
        comment="Geographic location (PostGIS point) from user"
    )
    
    location_address: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Human-readable address from user"
    )
    
    # === IMPACT ASSESSMENT (by user) ===
    people_affected: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="User's estimate of people affected"
    )
    
    # === CRITICAL FLAGS ===
    multiple_casualties: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="User reports multiple casualties"
    )
    
    structural_damage: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="User reports structural damage"
    )
    
    road_blocked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="User reports blocked roads"
    )
    
    # === STATUS ===
    report_status: Mapped[DisasterReportStatus] = mapped_column(
        SQLEnum(DisasterReportStatus, name="disaster_report_status"),
        default=DisasterReportStatus.PENDING,
        nullable=False,
        index=True,
        comment="Review status: PENDING/VERIFIED/REJECTED/DUPLICATE"
    )
    
    # === LINK TO VERIFIED DISASTER ===
    disaster_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("disasters.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Links to verified disaster record (if approved)"
    )
    
    # === REVIEW INFO ===
    reviewed_by_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("emergency_teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Emergency team member who reviewed this"
    )
    
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        comment="When this report was reviewed"
    )
    
    rejection_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Reason for rejection (if applicable)"
    )
    
    # === RELATIONSHIPS ===
    user: Mapped["User"] = relationship(
        "User",
        back_populates="disaster_reports",
        lazy="select"
    )
    
    disaster: Mapped[Optional["Disaster"]] = relationship(
        "Disaster",
        back_populates="reports",
        lazy="select"
    )
    
    reviewed_by: Mapped[Optional["EmergencyTeam"]] = relationship(
        "EmergencyTeam",
        foreign_keys=[reviewed_by_id],
        lazy="select"
    )
    
    # Photos belong to the report
    photos: Mapped[List["DisasterPhoto"]] = relationship(
        "DisasterPhoto",
        back_populates="disaster_report",
        cascade="all, delete-orphan",
        lazy="select"
    )
    
    # === INDEXES ===
    __table_args__ = (
        Index('idx_report_user_status', 'user_id', 'report_status'),
        Index('idx_report_disaster', 'disaster_id'),
        Index('idx_report_status_created', 'report_status', 'created_at'),
        Index('idx_report_location', 'location', postgresql_using='gist'),
        Index('idx_report_type_severity', 'disaster_type', 'severity'),
    )
    
    def __repr__(self) -> str:
        return (
            f"<DisasterReport(id={self.id}, type={self.disaster_type}, "
            f"severity={self.severity}, status={self.report_status})>"
        )
    
    # === HELPER METHODS ===
    
    def verify(self, disaster_id: str, reviewed_by_id: str) -> None:
        """Link report to verified disaster."""
        self.report_status = DisasterReportStatus.VERIFIED
        self.disaster_id = disaster_id
        self.reviewed_by_id = reviewed_by_id
        self.reviewed_at = datetime.utcnow()
    
    def reject(self, reviewed_by_id: str, reason: Optional[str] = None) -> None:
        """Reject report as false/invalid."""
        self.report_status = DisasterReportStatus.REJECTED
        self.reviewed_by_id = reviewed_by_id
        self.reviewed_at = datetime.utcnow()
        if reason:
            self.rejection_reason = reason
    
    def mark_duplicate(self, disaster_id: str, reviewed_by_id: str) -> None:
        """Mark as duplicate of existing disaster."""
        self.report_status = DisasterReportStatus.DUPLICATE
        self.disaster_id = disaster_id
        self.reviewed_by_id = reviewed_by_id
        self.reviewed_at = datetime.utcnow()
    
    # === PROPERTIES ===
    
    @property
    def is_verified(self) -> bool:
        """Check if report was verified."""
        return self.report_status == DisasterReportStatus.VERIFIED
    
    @property
    def is_pending(self) -> bool:
        """Check if report is awaiting review."""
        return self.report_status == DisasterReportStatus.PENDING
    
    @property
    def has_photos(self) -> bool:
        """Check if report has photo attachments."""
        return len(self.photos) > 0
    
    @property
    def photo_count(self) -> int:
        """Number of photos attached."""
        return len(self.photos)
    
    @property
    def is_high_priority(self) -> bool:
        """Check if report indicates high-priority situation."""
        if self.severity == DisasterSeverity.CRITICAL:
            return True
        
        if self.severity == DisasterSeverity.HIGH:
            return (
                self.multiple_casualties or
                self.structural_damage
            )
        
        return False