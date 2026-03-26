"""
app/repositories/reroute_repository.py

Database access layer for the Reroute Traffic service.

Provides all persistence operations needed by RerouteService:
  - Road segment CRUD + status updates
  - ReroutePlan save / fetch active plan
  - TrafficOverride apply / list active
  - AuditLog append
  - User lookups by affected region
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.reroute import RoadSegment, ReroutePlan, TrafficOverride, AuditLog
from app.db.models.disaster import Disaster
from app.db.models.user import User
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class RerouteRepository:
    """
    Repository for all reroute-related database operations.

    Injected into RerouteService via constructor.
    Uses an AsyncSession from the FastAPI dependency system.
    """

    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self._segments = BaseRepository(RoadSegment, db_session)
        self._plans = BaseRepository(ReroutePlan, db_session)
        self._overrides = BaseRepository(TrafficOverride, db_session)
        self._logs = BaseRepository(AuditLog, db_session)

    # -------------------------------------------------------------------------
    # Road Segments
    # -------------------------------------------------------------------------

    async def get_blocked_roads(self, disaster_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all road segments currently closed for a given disaster.

        Returns serialised dicts (not ORM objects) so the service layer
        can pass them directly to TomTom avoidance constraints.
        """
        result = await self.db.execute(
            select(RoadSegment).where(
                and_(
                    RoadSegment.disaster_id == disaster_id,
                    RoadSegment.status == "closed",
                )
            )
        )
        segments = result.scalars().all()
        return [self._segment_to_dict(s) for s in segments]

    async def upsert_road_segments(
        self, segments: List[Dict[str, Any]], disaster_id: str
    ) -> List[RoadSegment]:
        """
        Insert or update road segments for a disaster.

        Uniqueness is (segment_id, disaster_id) — the same physical road
        can be blocked by multiple disasters without overwriting history.
        Only updates if the exact (segment_id, disaster_id) pair already exists.
        """
        created = []
        for seg in segments:
            # Look up by BOTH segment_id AND disaster_id to preserve history
            result = await self.db.execute(
                select(RoadSegment).where(
                    RoadSegment.segment_id == seg["segment_id"],
                    RoadSegment.disaster_id == disaster_id,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                for k, v in seg.items():
                    if hasattr(existing, k):
                        setattr(existing, k, v)
                await self.db.flush()
                await self.db.refresh(existing)
                created.append(existing)
            else:
                obj = await self._segments.create(
                    disaster_id=disaster_id,
                    **seg,
                )
                created.append(obj)
        return created

    async def update_road_status(
        self, segments: List[Dict[str, Any]], status: str
    ) -> bool:
        """
        Bulk-update the status of road segments.

        Args:
            segments: List of segment dicts (must include 'segment_id')
            status:   'open' | 'closed' | 'restricted'
        """
        segment_ids = [s["segment_id"] for s in segments]
        await self.db.execute(
            update(RoadSegment)
            .where(RoadSegment.segment_id.in_(segment_ids))
            .values(status=status)
        )
        await self.db.flush()
        logger.info(f"Updated {len(segment_ids)} segments to status='{status}'")
        return True

    # -------------------------------------------------------------------------
    # Reroute Plans
    # -------------------------------------------------------------------------

    async def save_reroute_plan(
        self,
        disaster_id: str,
        blocked_roads: List[Dict[str, Any]],
        chosen_routes: List[Dict[str, Any]],
        route_assignments: Dict[str, str],
        estimated_times: Optional[Dict[str, Any]] = None,
        capacity_usage: Optional[Dict[str, Any]] = None,
        trigger_source: str = "disaster_trigger",
        vehicles_affected: int = 0,
    ) -> Dict[str, Any]:
        """
        Persist a new reroute plan and supersede any previously active plan.

        Returns a dict representation of the saved plan.
        """
        # Supersede any existing active plans for this disaster
        await self.db.execute(
            update(ReroutePlan)
            .where(
                and_(
                    ReroutePlan.disaster_id == disaster_id,
                    ReroutePlan.status == "active",
                )
            )
            .values(status="superseded")
        )
        await self.db.flush()

        plan = await self._plans.create(
            disaster_id=disaster_id,
            blocked_roads=blocked_roads,
            chosen_routes=chosen_routes,
            route_assignments=route_assignments,
            estimated_times=estimated_times or {},
            capacity_usage=capacity_usage or {},
            trigger_source=trigger_source,
            vehicles_affected=vehicles_affected,
            status="active",
        )
        logger.info(
            f"Saved reroute plan {plan.id} for disaster {disaster_id} "
            f"(trigger={trigger_source}, vehicles={vehicles_affected})"
        )
        return {"id": plan.id, "status": plan.status, "disaster_id": disaster_id}

    async def get_active_reroute_plan(
        self, disaster_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch the currently active reroute plan for a disaster."""
        result = await self.db.execute(
            select(ReroutePlan).where(
                and_(
                    ReroutePlan.disaster_id == disaster_id,
                    ReroutePlan.status == "active",
                )
            )
        )
        plan = result.scalar_one_or_none()
        if not plan:
            return None
        return {
            "id": plan.id,
            "disaster_id": plan.disaster_id,
            "status": plan.status,
            "blocked_roads": plan.blocked_roads,
            "route_assignments": plan.route_assignments,
            "estimated_times": plan.estimated_times,
            "capacity_usage": plan.capacity_usage,
            "chosen_routes": plan.chosen_routes,
            "trigger_source": plan.trigger_source,
            "vehicles_affected": plan.vehicles_affected,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
        }

    async def clear_reroute_plans(self, disaster_id: str) -> bool:
        """Mark all plans for a disaster as cleared (road restored)."""
        await self.db.execute(
            update(ReroutePlan)
            .where(ReroutePlan.disaster_id == disaster_id)
            .values(status="cleared")
        )
        await self.db.flush()
        return True

    # -------------------------------------------------------------------------
    # Traffic Overrides
    # -------------------------------------------------------------------------

    async def apply_override(
        self, override: Dict[str, Any], disaster_id: str
    ) -> Dict[str, Any]:
        """
        Persist an operator override.

        Args:
            override:    Dict with keys: type, segment_id?, route_id?,
                         priority?, operator_id, metadata?
            disaster_id: Disaster this override belongs to
        """
        obj = await self._overrides.create(
            disaster_id=disaster_id,
            override_type=override.get("type"),
            segment_id=override.get("segment_id"),
            route_id=override.get("route_id"),
            priority=override.get("priority"),
            operator_id=override["operator_id"],
            override_metadata=override.get("metadata"),
            is_active=True,
        )
        logger.info(
            f"Applied override {obj.id} (type={obj.override_type}) "
            f"by operator {obj.operator_id} for disaster {disaster_id}"
        )
        return {
            "id": obj.id,
            "type": obj.override_type,
            "segment_id": obj.segment_id,
            "operator_id": obj.operator_id,
            "is_active": obj.is_active,
        }

    async def get_active_overrides(
        self, disaster_id: str
    ) -> List[Dict[str, Any]]:
        """Return all currently active overrides for a disaster."""
        result = await self.db.execute(
            select(TrafficOverride).where(
                and_(
                    TrafficOverride.disaster_id == disaster_id,
                    TrafficOverride.is_active == True,  # noqa: E712
                )
            )
        )
        overrides = result.scalars().all()
        return [
            {
                "id": o.id,
                "type": o.override_type,
                "segment_id": o.segment_id,
                "route_id": o.route_id,
                "priority": o.priority,
                "operator_id": o.operator_id,
            }
            for o in overrides
        ]

    # -------------------------------------------------------------------------
    # Audit Log
    # -------------------------------------------------------------------------

    async def log_event(
        self,
        disaster_id: str,
        event_type: str,
        data: Optional[Dict[str, Any]] = None,
        reroute_plan_id: Optional[str] = None,
        triggered_by: str = "system",
    ) -> bool:
        """
        Append an event to the audit log.

        Never updates or deletes — append only.
        """
        await self._logs.create(
            disaster_id=disaster_id,
            event_type=event_type,
            event_data=data or {},
            reroute_plan_id=reroute_plan_id,
            triggered_by=triggered_by,
        )
        logger.info(f"Audit log: disaster={disaster_id} event={event_type}")
        return True

    # -------------------------------------------------------------------------
    # User / vehicle lookups
    # -------------------------------------------------------------------------

    async def get_users_in_affected_area(
        self,
        lat: float,
        lon: float,
        radius_km: float,
    ) -> list[dict]:
        """
        Return active travellers whose current position falls within
        radius_km of (lat, lon).
    
        Dev/test path: if UserSimulator has vehicles loaded (via
        POST /scenarios/seed-vehicles), returns those — keeps scenario_engine
        working for testing without touching the DB.
    
        Production path: queries active_trips using a bounding box derived
        from the disaster coordinates and impact radius.
    
        Bounding box conversion:
            1 degree lat  ≈ 111 km
            1 degree lon  ≈ 73 km at Dublin latitude (53°N)
        """
        # -- Dev/test override ---------------------------------------------------
        from app.services.user_simulator import user_simulator
    
        lat_offset = radius_km / 111.0
        lon_offset = radius_km / 73.0
    
        bounds = {
            "lat_min": lat - lat_offset,
            "lat_max": lat + lat_offset,
            "lng_min": lon - lon_offset,
            "lng_max": lon + lon_offset,
        }
    
        simulated = user_simulator.get_users_in_region(bounds)
        if simulated:
            return simulated
    
        # -- Production path -----------------------------------------------------
        from sqlalchemy import select
        from app.db.models.active_trip import ActiveTrip
        from datetime import datetime, timezone
    
        now = datetime.now(timezone.utc)
    
        result = await self.db.execute(
            select(ActiveTrip).where(
                ActiveTrip.current_lat >= bounds["lat_min"],
                ActiveTrip.current_lat <= bounds["lat_max"],
                ActiveTrip.current_lng >= bounds["lng_min"],
                ActiveTrip.current_lng <= bounds["lng_max"],
                ActiveTrip.expires_at > now,
            )
        )
        trips = result.scalars().all()
    
        return [
            {
                "user_id": trip.user_id,
                "current_location": {"lat": trip.current_lat, "lng": trip.current_lng},
                "destination": {"lat": trip.dest_lat, "lng": trip.dest_lng},
                "type": trip.vehicle_type,
                "phone_number": None,  # extend: join users table for SMS
            }
            for trip in trips
        ]

    async def get_all_active_plans(self) -> list[dict]:
        """
        Return every active reroute plan across all disasters.
        Used by the admin dashboard to show the full operational picture.
        """
        from sqlalchemy import select
        from app.db.models.disaster import Disaster

        result = await self.db.execute(
            select(ReroutePlan, Disaster)
            .join(Disaster, ReroutePlan.disaster_id == Disaster.id)
            .where(ReroutePlan.status == "active")
            .order_by(ReroutePlan.created_at.desc())
        )

        rows = result.all()   # ← not scalars()

        plans = []
        for p, d in rows:   
            meta = (d.disaster_metadata or {}).get("evaluation", {})
            plans.append({
                "id": p.id,
                "disaster_id": p.disaster_id,
                "status": p.status,
                "blocked_roads": p.blocked_roads,
                "vehicles_affected": p.vehicles_affected,
                "routes_count": len(p.chosen_routes) if p.chosen_routes else 0,
                "trigger_source": p.trigger_source,
                "route_assignments": p.route_assignments,
                "chosen_routes": p.chosen_routes,
                "capacity_usage": p.capacity_usage,
                "estimated_times": p.estimated_times,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "impact_radius_km": meta.get("impact_radius_km", 3.0),
                "disaster_lat": d.disaster_metadata.get("lat") if d.disaster_metadata else None,
                "disaster_lng": d.disaster_metadata.get("lon") if d.disaster_metadata else None,
            })

        return plans

    async def resolve_disaster(self, disaster_id: str) -> None:
        """
        Update disaster status to RESOLVED in the disasters table.
        Called when restore_normal_flow completes.
        """
        from sqlalchemy import text
        try:
            await self.db.execute(
                text("""
                    UPDATE disasters
                    SET disaster_status = CAST('RESOLVED' AS disaster_status),
                        resolved_time = now(),
                        updated_at = now()
                    WHERE id = :disaster_id
                """),
                {"disaster_id": disaster_id}
            )
            await self.db.commit()
            logger.info(f"resolve_disaster: disaster={disaster_id} → RESOLVED")
        except Exception as e:
            await self.db.rollback()
            logger.warning(f"resolve_disaster: failed — {e}")

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _segment_to_dict(segment: RoadSegment) -> Dict[str, Any]:
        return {
            "segment_id": segment.segment_id,
            "road_name": segment.road_name,
            "start_lat": segment.start_lat,
            "start_lng": segment.start_lng,
            "end_lat": segment.end_lat,
            "end_lng": segment.end_lng,
            "status": segment.status,
            "reason": segment.reason,
            "capacity": segment.capacity,
            "disaster_id": segment.disaster_id,
            "points":  segment.points,  
            "geojson": segment.geojson,   
        }