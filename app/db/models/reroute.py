"""
app/db/models/reroute.py

Database models for the Reroute Traffic service.

Tables:
    road_segments       — Road segments that can be blocked/opened by disaster events
    reroute_plans       — Computed reroute plans produced for a disaster
    traffic_overrides   — Operator manual overrides (open/close lane, pin detour, corridor priority)
    audit_logs          — Append-only event log for the full reroute lifecycle
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Text, JSON, Enum as SQLEnum,
    Index, ForeignKey, Boolean, Integer, Float
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.db.models.base import Base
from app.db.models.enums import DisasterStatus


# ---------------------------------------------------------------------------
# RoadSegment
# ---------------------------------------------------------------------------

class RoadSegmentStatus(str):
    OPEN = "open"
    CLOSED = "closed"
    RESTRICTED = "restricted"


class RoadSegment(Base):
    """
    A road segment that can be affected by a disaster.

    Segments are identified by their TomTom segment ID and store
    enough geographic data to build avoidance constraints for the
    TomTom Routing API.

    Linked (optionally) to the Disaster that caused the closure.
    """

    __tablename__ = "road_segments"

    # --- Segment identity ---
    segment_id: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
        index=True,
        comment="TomTom / internal road segment identifier"
    )

    road_name: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Human-readable road name"
    )

    # --- Geometry (start/end points — lightweight, no PostGIS dependency) ---
    start_lat: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Segment start latitude (WGS 84)"
    )

    start_lng: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Segment start longitude (WGS 84)"
    )

    end_lat: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Segment end latitude (WGS 84)"
    )

    end_lng: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Segment end longitude (WGS 84)"
    )

    # --- Status ---
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="open",
        index=True,
        comment="open | closed | restricted"
    )

    reason: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Reason for closure (flood, fire, operator_override, etc.)"
    )

    # --- Link to disaster that caused the closure ---
    disaster_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("disasters.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Disaster that caused this segment to be blocked"
    )

    # --- Capacity metadata ---
    capacity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=300,
        comment="Max vehicles per hour this segment can handle"
    )

    points: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        comment="Road-following polyline [[lat,lng],...] fetched from TomTom at trigger time"
    )

    geojson: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="GeoJSON LineString Feature for frontend map rendering"
    )

    __table_args__ = (
        Index("idx_road_segments_status", "status"),
        Index("idx_road_segments_disaster", "disaster_id"),
    )

    def __repr__(self) -> str:
        return f"<RoadSegment(segment_id={self.segment_id}, status={self.status})>"


# ---------------------------------------------------------------------------
# ReroutePlan
# ---------------------------------------------------------------------------

class ReroutePlanStatus(str):
    ACTIVE = "active"
    SUPERSEDED = "superseded"   # replaced by a newer plan for the same disaster
    CLEARED = "cleared"         # disaster resolved, routes restored


class ReroutePlan(Base):
    """
    A computed reroute plan for a specific disaster event.

    Stores the full distribution plan produced by Innovation 1
    (capacity-aware greedy distribution) so it can be retrieved,
    audited, and compared across recalculation cycles.

    One disaster can have multiple plans (superseded by recalculations).
    The latest ACTIVE plan is the one currently in effect.
    """

    __tablename__ = "reroute_plans"

    # --- Disaster reference ---
    disaster_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("disasters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Disaster this plan was computed for"
    )

    # --- Plan status ---
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        index=True,
        comment="active | superseded | cleared"
    )

    # --- Blocked roads at the time this plan was computed (snapshot) ---
    blocked_roads: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        comment="Snapshot of blocked road segments when plan was computed"
    )

    # --- Route assignments: {user_id: route_id} ---
    route_assignments: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="Vehicle-to-route mapping produced by capacity-aware distribution"
    )

    # --- Per-route estimated travel times ---
    estimated_times: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Estimated travel times per route_id"
    )

    # --- Capacity usage per segment ---
    capacity_usage: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Vehicles assigned and remaining capacity per segment"
    )



    # --- Full alternative routes as returned by TomTom (GeoJSON-ready) ---
    chosen_routes: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Full route objects selected for this plan"
    )

    # --- Trigger source ---
    trigger_source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="disaster_trigger",
        comment="What triggered this plan: disaster_trigger | congestion_reactive | congestion_predictive | operator_override | multi_incident"
    )

    # --- Number of vehicles covered ---
    vehicles_affected: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Total vehicles included in this reroute plan"
    )

    __table_args__ = (
        Index("idx_reroute_plans_disaster_status", "disaster_id", "status"),
        Index("idx_reroute_plans_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<ReroutePlan(id={self.id}, disaster_id={self.disaster_id}, "
            f"status={self.status}, vehicles={self.vehicles_affected})>"
        )


# ---------------------------------------------------------------------------
# TrafficOverride
# ---------------------------------------------------------------------------

class OverrideType(str):
    CLOSE_LANE = "close_lane"
    OPEN_LANE = "open_lane"
    PIN_DETOUR = "pin_detour"
    CORRIDOR_PRIORITY = "corridor_priority"


class TrafficOverride(Base):
    """
    Operator manual override applied to the active reroute plan.

    Overrides are recorded here so that:
    - The ReRoute Service can recompute routes with override constraints
    - The audit trail captures every operator action
    - Overrides can be reversed or listed per disaster

    Override types (Section 6, Alt 13c):
    - close_lane: Force a lane/segment closed
    - open_lane: Force a previously closed segment open
    - pin_detour: Lock a specific detour route as the preferred option
    - corridor_priority: Mark a corridor for exclusive emergency vehicle use
    """

    __tablename__ = "traffic_overrides"

    # --- Disaster context ---
    disaster_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("disasters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Disaster this override applies to"
    )

    # --- Override type ---
    override_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="close_lane | open_lane | pin_detour | corridor_priority"
    )

    # --- Target segment or route ---
    segment_id: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Segment affected (for close_lane / open_lane)"
    )

    route_id: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Route pinned (for pin_detour)"
    )

    # --- Priority level (for corridor_priority) ---
    priority: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="emergency | public_transport | general"
    )

    # --- Operator who issued the override ---
    operator_id: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
        comment="ID of the operator who issued this override"
    )

    # --- Active flag (operator can revoke) ---
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
        comment="Whether this override is currently active"
    )

    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        comment="When the override was revoked (NULL if still active)"
    )

    # --- Additional override parameters ---
    override_metadata: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Additional parameters for the override"
    )

    __table_args__ = (
        Index("idx_traffic_overrides_disaster_active", "disaster_id", "is_active"),
        Index("idx_traffic_overrides_operator", "operator_id"),
    )

    def revoke(self) -> None:
        """Revoke this override."""
        self.is_active = False
        self.revoked_at = datetime.utcnow()

    def __repr__(self) -> str:
        return (
            f"<TrafficOverride(id={self.id}, type={self.override_type}, "
            f"disaster_id={self.disaster_id}, active={self.is_active})>"
        )


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------

class AuditLog(Base):
    """
    Append-only event log for the full reroute lifecycle.

    Every significant event in the reroute pipeline is logged here:
    - disaster triggered
    - reroute plan computed
    - congestion detected (reactive / predictive)
    - operator override applied
    - roads cleared / traffic restored

    This is the authoritative audit trail for a disaster's traffic response.
    Never update or delete rows — append only.
    """

    __tablename__ = "audit_logs"

    # --- Disaster context ---
    disaster_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("disasters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Disaster this event belongs to"
    )

    # --- Event classification ---
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment=(
            "traffic_rerouted | congestion_detected | congestion_predicted | "
            "operator_override | multi_incident | priority_recomputed | restored"
        )
    )

    # --- Optional link to the reroute plan that was active during this event ---
    reroute_plan_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("reroute_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="ReroutePlan active at the time of this event"
    )

    # --- Event payload ---
    event_data: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Event-specific data (routes, segments, override details, etc.)"
    )

    # --- Trigger source ---
    triggered_by: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="system",
        comment="system | tomtom_reactive | predictive_model | operator | disaster_evaluation_service"
    )

    __table_args__ = (
        Index("idx_audit_logs_disaster_event", "disaster_id", "event_type"),
        Index("idx_audit_logs_event_type", "event_type"),
        Index("idx_audit_logs_created", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog(id={self.id}, disaster_id={self.disaster_id}, "
            f"event_type={self.event_type})>"
        )