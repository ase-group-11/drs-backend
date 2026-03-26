# File: app/db/models/emergency_unit.py
"""
Emergency Unit model - Operational field units.

An Emergency Unit is a deployable field team with:
- A vehicle, crew, and equipment
- A home station
- Operational status tracking
- Performance statistics

Emergency Units are distinct from EmergencyTeam members:
  - EmergencyTeam  = individual personnel (people who log in)
  - EmergencyUnit  = deployable unit (vehicle + crew assigned to incidents)

Relationships:
  - crew_members  → EmergencyTeam (M2M via unit_crew join table)
  - commander     → EmergencyTeam (unit leader, FK)
  - disasters     → Disaster (disasters this unit has been assigned to)
"""

from datetime import datetime
from sqlalchemy import (
    String, Text, JSON, Enum as SQLEnum,
    Index, ForeignKey, Boolean, Integer,
    Float, Table, Column, DateTime
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geography
from typing import Optional, List, TYPE_CHECKING

from app.db.models.base import Base
from app.db.models.enums import Department, UnitType, UnitStatus

if TYPE_CHECKING:
    from app.db.models.emergency_team import EmergencyTeam
    from app.db.models.disaster import Disaster


# ─────────────────────────────────────────────────────────────
# M2M Join Table: unit_crew
# Links EmergencyUnit ↔ EmergencyTeam (crew members)
# ─────────────────────────────────────────────────────────────
unit_crew = Table(
    "unit_crew",
    Base.metadata,
    Column(
        "unit_id",
        UUID(as_uuid=False),
        ForeignKey("emergency_units.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Emergency unit"
    ),
    Column(
        "team_member_id",
        UUID(as_uuid=False),
        ForeignKey("emergency_teams.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Emergency team member assigned to this unit"
    ),
)


# ─────────────────────────────────────────────────────────────
# Emergency Unit Model
# ─────────────────────────────────────────────────────────────
class EmergencyUnit(Base):
    """
    Deployable emergency unit — vehicle + crew + equipment.

    Each unit belongs to a department and has:
    - Identity: unit_code, unit_name, description
    - Classification: unit_type, department, unit_status
    - Station: station_name, station_address, station_location (PostGIS)
    - Vehicle: vehicle_model, vehicle_license_plate, vehicle_year, equipment_checklist
    - Crew: commander_id (FK), crew_members (M2M), capacity
    - Performance: total_deployments, avg_response_time_seconds,
                   success_rate, last_deployed_at
    """

    __tablename__ = "emergency_units"

    # ── IDENTITY ─────────────────────────────────────────────

    unit_code: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
        index=True,
        comment="Human-facing unit code (e.g., UNIT-MED-001)"
    )

    unit_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Display name of the unit (e.g., Alpha Rescue Team)"
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Optional description or notes about this unit"
    )

    # ── CLASSIFICATION ────────────────────────────────────────

    unit_type: Mapped[UnitType] = mapped_column(
        SQLEnum(UnitType, name="unit_type"),
        nullable=False,
        index=True,
        comment="Type of unit: AMBULANCE, FIRE_ENGINE, PATROL_CAR, etc."
    )

    department: Mapped[Department] = mapped_column(
        SQLEnum(Department, name="department"),
        nullable=False,
        index=True,
        comment="Owning department (reuses existing Department enum)"
    )

    unit_status: Mapped[UnitStatus] = mapped_column(
        SQLEnum(UnitStatus, name="unit_status"),
        default=UnitStatus.AVAILABLE,
        nullable=False,
        index=True,
        comment="Operational status: AVAILABLE, DEPLOYED, ON_SCENE, etc."
    )

    # ── STATION ASSIGNMENT ────────────────────────────────────

    station_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Name of the home station (e.g., Dublin Fire Station 3)"
    )

    station_address: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Physical address of the home station"
    )

    station_location: Mapped[Optional[Geography]] = mapped_column(
        Geography(
            geometry_type="POINT",
            srid=4326,
            spatial_index=True
        ),
        nullable=True,
        comment="PostGIS point for the station geographic location"
    )

    # ── VEHICLE DETAILS ───────────────────────────────────────

    vehicle_model: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Vehicle model (e.g., Mercedes Sprinter 519 CDI)"
    )

    vehicle_license_plate: Mapped[Optional[str]] = mapped_column(
        String(20),
        unique=True,
        nullable=True,
        index=True,
        comment="Vehicle license plate number (must be unique)"
    )

    vehicle_year: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Manufacturing year of the vehicle"
    )

    equipment_checklist: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        comment=(
            "Equipment checklist as JSON array. "
            'Example: [{"item": "Defibrillator", "present": true}, ...]'
        )
    )

    # ── CREW ──────────────────────────────────────────────────

    capacity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=4,
        comment="Maximum crew capacity for this unit"
    )

    commander_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("emergency_teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Unit commander / team leader (FK to emergency_teams)"
    )

    # ── PERFORMANCE STATS ─────────────────────────────────────

    total_deployments: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Total number of times this unit has been deployed"
    )

    avg_response_time_seconds: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Rolling average response time in seconds"
    )

    success_rate: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="Ratio of resolved deployments to total (0.0 to 1.0)"
    )

    last_deployed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the most recent deployment"
    )

    # ── RELATIONSHIPS ─────────────────────────────────────────

    commander: Mapped[Optional["EmergencyTeam"]] = relationship(
        "EmergencyTeam",
        foreign_keys=[commander_id],
        lazy="select",
        back_populates="commanded_units"
    )

    crew_members: Mapped[List["EmergencyTeam"]] = relationship(
        "EmergencyTeam",
        secondary=unit_crew,
        lazy="select",
        back_populates="unit_assignments"
    )

    assigned_disasters: Mapped[List["Disaster"]] = relationship(
        "Disaster",
        foreign_keys="Disaster.assigned_unit_id",
        back_populates="assigned_unit",
        lazy="select"
    )

    # ── INDEXES ───────────────────────────────────────────────

    __table_args__ = (
        Index("idx_units_type_status", "unit_type", "unit_status"),
        Index("idx_units_dept_status", "department", "unit_status"),
        Index("idx_units_commander", "commander_id"),
        Index("idx_units_available", "unit_status",
              postgresql_where=(unit_status == UnitStatus.AVAILABLE)),
        Index("idx_units_station_location", "station_location",
              postgresql_using="gist"),
    )

    # ── REPR ──────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<EmergencyUnit(id={self.id}, code={self.unit_code}, "
            f"type={self.unit_type}, status={self.unit_status})>"
        )

    # ── HELPER METHODS ────────────────────────────────────────

    def deploy(self) -> None:
        """Mark unit as deployed and record the timestamp."""
        self.unit_status = UnitStatus.DEPLOYED
        self.last_deployed_at = datetime.utcnow()
        self.total_deployments += 1

    def mark_on_scene(self) -> None:
        """Mark unit as on-scene at the incident."""
        self.unit_status = UnitStatus.ON_SCENE

    def return_to_base(self) -> None:
        """Mark unit as returning to station."""
        self.unit_status = UnitStatus.RETURNING

    def set_available(self) -> None:
        """Mark unit as available for deployment."""
        self.unit_status = UnitStatus.AVAILABLE

    def set_maintenance(self) -> None:
        """Mark unit as under maintenance (not available)."""
        self.unit_status = UnitStatus.MAINTENANCE

    def set_offline(self) -> None:
        """Mark unit as offline."""
        self.unit_status = UnitStatus.OFFLINE

    def update_performance(
        self,
        response_time_seconds: int,
        was_successful: bool
    ) -> None:
        """
        Update rolling performance stats after a deployment resolves.

        Recalculates:
        - avg_response_time_seconds  (rolling average)
        - success_rate               (resolved / total)

        Args:
            response_time_seconds: Time from dispatch to on-scene (seconds).
            was_successful: True if the disaster was resolved by this unit.
        """
        n = self.total_deployments  # already incremented by deploy()

        # Rolling average for response time
        if self.avg_response_time_seconds is None:
            self.avg_response_time_seconds = response_time_seconds
        else:
            self.avg_response_time_seconds = (
                (self.avg_response_time_seconds * (n - 1) + response_time_seconds) // n
            )

        # Recalculate success rate
        if self.success_rate is None:
            self.success_rate = 1.0 if was_successful else 0.0
        else:
            current_successes = round(self.success_rate * (n - 1))
            new_successes = current_successes + (1 if was_successful else 0)
            self.success_rate = round(new_successes / n, 4)

    # ── PROPERTIES ────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """True if the unit can be dispatched right now."""
        return self.unit_status == UnitStatus.AVAILABLE

    @property
    def is_deployed(self) -> bool:
        """True if the unit is currently active on an incident."""
        return self.unit_status in (UnitStatus.DEPLOYED, UnitStatus.ON_SCENE)

    @property
    def crew_count(self) -> int:
        """Current number of crew members assigned."""
        return len(self.crew_members) if self.crew_members else 0

    @property
    def is_at_capacity(self) -> bool:
        """True if the crew is at full capacity."""
        return self.crew_count >= self.capacity

    @property
    def success_rate_percent(self) -> Optional[float]:
        """Success rate as a percentage (0.0 – 100.0)."""
        if self.success_rate is None:
            return None
        return round(self.success_rate * 100, 2)