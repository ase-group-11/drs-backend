# File: app/models/emergency_team.py
"""
Emergency Team model for emergency responders.

Emergency team members authenticate using phone + password.
They have roles (Admin, Manager, Staff) and belong to departments.
"""

from sqlalchemy import String, Enum as SQLEnum, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List, TYPE_CHECKING
from app.db.models.base import Base
from app.db.models.enums import UserStatus, EmergencyTeamRole, Department

if TYPE_CHECKING:
    from app.db.models.disaster_report import DisasterReport
    from app.db.models.disaster import Disaster


class EmergencyTeam(Base):
    """
    Emergency team member model (password-based authentication).
    
    Emergency team members have:
    - Password authentication (hashed with Argon2)
    - Role-based access control (Admin, Manager, Staff)
    - Department assignment (Medical, Police, Fire, IT)
    
    Fields:
    - phone_number: Unique phone number (E.164 format)
    - password_hash: Argon2 hashed password
    - full_name: Team member's full name
    - email: Email address
    - role: Access level (admin, manager, staff)
    - department: Service department
    - employee_id: Optional employee ID
    - status: Account status
    """
    
    __tablename__ = "emergency_teams"
    
    # Authentication
    phone_number: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
        index=True
    )
    
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )
    
    # Personal details
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )
    
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )
    
    employee_id: Mapped[Optional[str]] = mapped_column(
        String(50),
        unique=True,
        nullable=True,
        index=True
    )
    
    # Role and department
    role: Mapped[EmergencyTeamRole] = mapped_column(
        SQLEnum(EmergencyTeamRole, name="emergency_team_role"),
        default=EmergencyTeamRole.STAFF,
        nullable=False,
        index=True
    )
    
    department: Mapped[Department] = mapped_column(
        SQLEnum(Department, name="department"),
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

    verified_disasters: Mapped[List["Disaster"]] = relationship(
        "Disaster",
        back_populates="verified_by",
        foreign_keys="Disaster.verified_by_team_id"
    )
    
    
    # Relationships
    assigned_reports: Mapped[List["DisasterReport"]] = relationship(
        "DisasterReport",
        back_populates="assigned_to",
        foreign_keys="[DisasterReport.assigned_to_id]",
        lazy="select"
    )
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_team_phone_status', 'phone_number', 'status'),
        Index('idx_team_role_dept', 'role', 'department'),
        Index('idx_team_dept_status', 'department', 'status'),
    )
    
    def __repr__(self) -> str:
        return (
            f"<EmergencyTeam(id={self.id}, phone={self.phone_number}, "
            f"role={self.role}, dept={self.department})>"
        )
    
    def activate(self) -> None:
        """Activate team member account after onboarding."""
        self.status = UserStatus.ACTIVE
    
    def deactivate(self) -> None:
        """Deactivate team member account."""
        self.status = UserStatus.INACTIVE
    
    def suspend(self) -> None:
        """Suspend team member account."""
        self.status = UserStatus.SUSPENDED
    
    @property
    def is_active(self) -> bool:
        """Check if team member account is active."""
        return self.status == UserStatus.ACTIVE
    
    @property
    def is_admin(self) -> bool:
        """Check if team member has admin role."""
        return self.role == EmergencyTeamRole.ADMIN
    
    @property
    def is_manager(self) -> bool:
        """Check if team member has manager role."""
        return self.role == EmergencyTeamRole.MANAGER
    
    @property
    def can_manage_team(self) -> bool:
        """Check if team member can manage other team members."""
        return self.role in (EmergencyTeamRole.ADMIN, EmergencyTeamRole.MANAGER)
    
    @property
    def active_assignments_count(self) -> int:
        """Get count of currently assigned active reports."""
        from app.db.models.enums import ReportStatus
        return sum(1 for r in self.assigned_reports if r.status in [
            ReportStatus.ASSIGNED,
            ReportStatus.IN_PROGRESS
        ])
    
    @property
    def total_resolved_count(self) -> int:
        """Get count of total reports resolved by this team member."""
        from app.db.models.enums import ReportStatus
        return sum(1 for r in self.assigned_reports if r.status == ReportStatus.RESOLVED)# File: app/models/emergency_team.py
