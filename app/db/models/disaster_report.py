# File: app/models/disaster_report.py
"""
Disaster Report model for emergency incident reporting.

Users can submit reports about emergencies with details like:
- Location, disaster type, severity
- Media attachments (photos/videos)
- Number of people affected
- Additional critical details
"""

from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, Integer, Float, Boolean, Text, JSON, ForeignKey, Enum as SQLEnum, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db.models.base import Base
from app.db.models.enums import DisasterType, Department, DisasterReportStatus, DisasterStatus, DisasterSeverity

if TYPE_CHECKING:
    from app.db.models.user import User
    from app.db.models.emergency_team import EmergencyTeam


class DisasterReport(Base):
    """
    Disaster report model for emergency incidents.
    
    Users submit reports about emergencies/disasters with:
    - Location and disaster details
    - Severity assessment
    - Media evidence (photos/videos)
    - Impact information (people affected)
    - Critical situation flags
    - Status tracking and assignment
    
    Fields:
    - user_id: Reporter's user ID
    - location_address: Human-readable address
    - location_latitude: GPS latitude
    - location_longitude: GPS longitude
    - disaster_type: Type of emergency
    - severity: Severity level (low, medium, high, critical)
    - description: Detailed description of the situation
    - media_urls: JSON array of photo/video URLs
    - people_affected: Number of people affected
    - multiple_casualties: Flag for multiple casualties
    - structural_damage: Flag for building damage
    - hazmat_involved: Flag for hazardous materials
    - road_blocked: Flag for blocked road access
    - status: Report lifecycle status
    - assigned_to_id: Assigned emergency team member ID
    - assigned_department: Department handling the report
    - response_time: When emergency team started responding
    - resolved_time: When the emergency was resolved
    - resolution_notes: Notes about how it was resolved
    """
    
    __tablename__ = "disaster_reports"
    
    # Reporter reference
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    severity: Mapped[str] = mapped_column(
        SQLEnum(DisasterSeverity, name = "disaster_severity"), 
        nullable=False,

    )
    
    # Location information
    location_address: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )
    
    location_latitude: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True
    )
    
    location_longitude: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True
    )
    
    # Disaster information
    disaster_type: Mapped[DisasterType] = mapped_column(
        SQLEnum(DisasterType, name="disaster_type"),
        nullable=False,
        index=True
    )
    
    
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )
    
    # Media attachments (stored as JSON array of URLs)
    media_urls: Mapped[Optional[List[str]]] = mapped_column(
        JSON,
        nullable=True,
        default=list
    )
    
    # Impact assessment
    people_affected: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0
    )
    
    # Additional critical details (boolean flags)
    multiple_casualties: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )
    
    structural_damage: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )
    
    hazmat_involved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )
    
    road_blocked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )
    
    # Status and assignment
    status: Mapped[DisasterReportStatus] = mapped_column(
        SQLEnum(DisasterReportStatus, name="report_status"),
        default=DisasterReportStatus.PENDING,
        nullable=False,
        index=True
    )
    
    assigned_to_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("emergency_teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    assigned_department: Mapped[Optional[Department]] = mapped_column(
        SQLEnum(Department, name="department"),
        nullable=True,
        index=True
    )
    
    # Timeline tracking
    response_time: Mapped[Optional[datetime]] = mapped_column(
        nullable=True
    )
    
    resolved_time: Mapped[Optional[datetime]] = mapped_column(
        nullable=True
    )
    
    # Resolution details
    resolution_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )
    
    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="disaster_reports",
        lazy="select"
    )
    
    assigned_to: Mapped[Optional["EmergencyTeam"]] = relationship(
        "EmergencyTeam",
        back_populates="assigned_reports",
        foreign_keys=[assigned_to_id],
        lazy="select"
    )
    
    # Composite indexes for common queries
    __table_args__ = (
        # Index('idx_report_status_severity', 'status', 'severity'),
        Index('idx_report_disaster_status', 'disaster_type', 'status'),
        Index('idx_report_user_status', 'user_id', 'status'),
        Index('idx_report_assigned', 'assigned_to_id', 'status'),
        Index('idx_report_dept_status', 'assigned_department', 'status'),
        Index('idx_report_created', 'created_at', 'status'),
        Index('idx_report_location', 'location_latitude', 'location_longitude'),
    )
    
    def __repr__(self) -> str:
        return (
            f"<DisasterReport(id={self.id}, type={self.disaster_type} "
            # f"severity={self.severity}, status={self.status})>"
        )
    
    # Status transition methods
    def mark_under_review(self) -> None:
        """Mark report as under review by emergency team."""
        self.status = DisasterReportStatus.PENDING
    
    def assign_to(
        self, 
        team_member_id: str, 
        department: Department
    ) -> None:
        """
        Assign report to an emergency team member.
        
        Args:
            team_member_id: ID of the emergency team member
            department: Department handling the report
        """
        self.assigned_to_id = team_member_id
        self.assigned_department = department
        self.status = DisasterReportStatus.VERIFIED
    
    def start_response(self) -> None:
        """Mark when emergency team starts responding."""
        self.status = DisasterReportStatus.PENDING
        if not self.response_time:
            self.response_time = datetime.utcnow()
    
    def resolve(self, resolution_notes: Optional[str] = None) -> None:
        """
        Mark report as resolved.
        
        Args:
            resolution_notes: Optional notes about the resolution
        """
        self.status = DisasterStatus.RESOLVED
        self.resolved_time = datetime.utcnow()
        if resolution_notes:
            self.resolution_notes = resolution_notes
    
    def cancel(self, reason: Optional[str] = None) -> None:
        """
        Cancel the report.
        
        Args:
            reason: Optional reason for cancellation
        """
        self.status = DisasterReportStatus.REJECTED
        if reason:
            self.resolution_notes = f"Cancelled: {reason}"
    
    def reject(self, reason: Optional[str] = None) -> None:
        """
        Reject the report (false alarm, duplicate, etc.).
        
        Args:
            reason: Optional reason for rejection
        """
        self.status = DisasterReportStatus.REJECTED
        if reason:
            self.resolution_notes = f"Rejected: {reason}"
    
    # Property helpers
    # @property
    # def is_critical(self) -> bool:
    #     """Check if report is critical severity."""
    #     return self.severity == D.CRITICAL
    
    @property
    def is_high_priority(self) -> bool:
        """
        Check if report is high priority.
        
        High priority = Critical severity OR (High severity + critical flags)
        """
        if self.severity == DisasterSeverity.CRITICAL:
            return True
        
        if self.severity == DisasterSeverity.HIGH:
            critical_flags = (
                self.multiple_casualties or
                self.structural_damage or
                self.hazmat_involved
            )
            return critical_flags
        
        return False
    
    @property
    def is_active(self) -> bool:
        """Check if report is still active (not resolved/cancelled/rejected)."""
        return self.status in (
            DisasterStatus.ACTIVE,
            DisasterStatus.MONITORING,
        )
    
    @property
    def is_assigned(self) -> bool:
        """Check if report is assigned to a team member."""
        return self.assigned_to_id is not None
    
    @property
    def response_time_minutes(self) -> Optional[float]:
        """
        Calculate response time in minutes.
        
        Returns:
            Time between report creation and response start, or None if not responded
        """
        if not self.response_time:
            return None
        
        delta = self.response_time - self.created_at
        return delta.total_seconds() / 60
    
    @property
    def resolution_time_minutes(self) -> Optional[float]:
        """
        Calculate total resolution time in minutes.
        
        Returns:
            Time between report creation and resolution, or None if not resolved
        """
        if not self.resolved_time:
            return None
        
        delta = self.resolved_time - self.created_at
        return delta.total_seconds() / 60
    
    @property
    def has_media(self) -> bool:
        """Check if report has media attachments."""
        return bool(self.media_urls and len(self.media_urls) > 0)
    
    @property
    def critical_flags_count(self) -> int:
        """Count how many critical flags are set."""
        return sum([
            self.multiple_casualties,
            self.structural_damage,
            self.hazmat_involved,
            self.road_blocked
        ])
    
    def to_summary_dict(self) -> dict:
        """
        Create a summary dictionary for API responses.
        
        Returns:
            Dictionary with key report information
        """
        return {
            "id": self.id,
            "disaster_type": self.disaster_type.value,
            "severity": self.severity.value,
            "status": self.status.value,
            "location": self.location_address,
            "people_affected": self.people_affected,
            # "is_critical": self.is_critical,
            "is_high_priority": self.is_high_priority,
            "has_media": self.has_media,
            "critical_flags_count": self.critical_flags_count,
            "created_at": self.created_at.isoformat(),
            "response_time_minutes": self.response_time_minutes,
            "resolution_time_minutes": self.resolution_time_minutes
        }