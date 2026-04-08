# File: app/repositories/evacuation_repository.py
"""
Evacuation Repository — UC8: Plan Evacuation

Database access layer for EvacuationService.
Mirrors the pattern of RerouteRepository: takes AsyncSession in __init__,
provides async methods, returns plain dicts (not ORM objects).

Methods match exactly what EvacuationService needs so the service
stays free of raw SQL and is fully testable by mocking this class.

v2 changes:
  - get_disaster() now returns disaster_metadata (evaluation enrichment)
  - get_available_transport_units() queries emergency_units table
  - get_users_in_impact_area() replaces get_users_in_zones()
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class EvacuationRepository:
    """
    All persistence operations for the evacuation pipeline.

    Injected into EvacuationService via constructor — swap for AsyncMock in tests.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # -------------------------------------------------------------------------
    # Disaster lookup
    # -------------------------------------------------------------------------

    async def get_disaster(self, disaster_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch disaster with PostGIS lat/lon extraction AND disaster_metadata.

        disaster_metadata contains the evaluation enrichment written by UC5:
          - impact_radius_km, estimated_population
          - affected_roads, affected_facilities
        These drive the impact-area model (no more hardcoded zones).
        """
        result = await self.db.execute(
            text("""
                SELECT id, tracking_id, type, severity, disaster_status,
                       ST_Y(location::geometry) AS lat,
                       ST_X(location::geometry) AS lon,
                       location_address, people_affected, road_blocked,
                       disaster_metadata
                FROM disasters
                WHERE id = :did AND deleted_at IS NULL
            """),
            {"did": disaster_id},
        )
        row = result.mappings().first()
        if not row:
            return None
        d = dict(row)
        # disaster_metadata is JSONB — may already be a dict or may be a string
        if isinstance(d.get("disaster_metadata"), str):
            try:
                d["disaster_metadata"] = json.loads(d["disaster_metadata"])
            except (json.JSONDecodeError, TypeError):
                d["disaster_metadata"] = {}
        return d

    # -------------------------------------------------------------------------
    # Emergency units — real DB data for transport allocation
    # -------------------------------------------------------------------------

    async def get_available_transport_units(self) -> List[Dict[str, Any]]:
        """
        Return available emergency units grouped by unit_type.

        Used by compute_transport_needs() to allocate real units instead
        of fictional BUS_CAPACITY / AMBULANCE_CAPACITY constants.
        """
        try:
            result = await self.db.execute(
                text("""
                    SELECT unit_type, COUNT(*) AS available_count, capacity
                    FROM emergency_units
                    WHERE unit_status = 'AVAILABLE'
                      AND deleted_at IS NULL
                    GROUP BY unit_type, capacity
                    ORDER BY unit_type
                """)
            )
            rows = result.mappings().all()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning(f"[UC8] get_available_transport_units failed: {exc}")
            return []

    # -------------------------------------------------------------------------
    # Blocked roads — reads UC7's road_segments table
    # -------------------------------------------------------------------------

    async def get_blocked_roads(self, disaster_id: str) -> List[Dict[str, Any]]:
        """
        Return road segments currently closed for this disaster.
        Same table UC7's RerouteRepository writes to.
        Returns dicts with start_lat/lng, end_lat/lng — TomTom-ready.
        """
        try:
            result = await self.db.execute(
                text("""
                    SELECT segment_id, road_name,
                           start_lat, start_lng, end_lat, end_lng,
                           status, reason
                    FROM road_segments
                    WHERE disaster_id = :did AND status = 'closed'
                """),
                {"did": disaster_id},
            )
            rows = result.mappings().all()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning(f"[UC8] get_blocked_roads failed (road_segments missing?): {exc}")
            return []

    # -------------------------------------------------------------------------
    # Users
    # -------------------------------------------------------------------------

    async def get_users_in_impact_area(self, impact_area: Dict) -> List[Dict[str, Any]]:
        """
        Return registered users to notify about evacuation.

        Note: users table has no lat/lon so we broadcast to all active users
        as safe-side approach. The impact_area metadata is attached to each
        user record so the notification layer can include it.
        """
        try:
            result = await self.db.execute(
                text("SELECT id, full_name, phone_number FROM users "
                     "WHERE deleted_at IS NULL LIMIT 500")
            )
            rows = result.mappings().all()
            return [
                {**dict(u),
                 "impact_area_id": impact_area.get("disaster_id", ""),
                 "area_name":      impact_area.get("area_name", "affected area")}
                for u in rows
            ]
        except Exception as exc:
            logger.warning(f"[UC8] get_users_in_impact_area: {exc}")
            return []

    # -------------------------------------------------------------------------
    # Evacuation plan CRUD
    # -------------------------------------------------------------------------

    async def generate_plan_ref(self) -> str:
        result = await self.db.execute(
            text("SELECT COUNT(*) FROM evacuation_plans WHERE deleted_at IS NULL")
        )
        count = result.scalar() or 0
        return f"EVA-{(count + 1):04d}"

    async def save_plan(
        self,
        disaster_id: str,
        plan_ref: str,
        impact_zones: List[Dict],
        population_stats: Dict,
        blocked_roads: List[Dict],
        traffic_snapshot: Dict,
        shelters_with_capacity: List[Dict],
        best_routes_per_zone: Dict,
        transport_plan: Dict,
        allocations: Dict,
        auto_approved: bool,
    ) -> str:
        plan_id = str(uuid.uuid4())

        # Drop created_at / updated_at from INSERT — server_default handles them.
        # Passing Python datetimes here causes naive/aware conflicts with asyncpg.
        road_names = [s.get("road_name", "") for s in blocked_roads]

        await self.db.execute(
            text("""
                INSERT INTO evacuation_plans (
                    id, plan_ref, disaster_id, plan_status,
                    impact_zones, population_stats, blocked_roads, traffic_snapshot,
                    shelters_with_capacity, best_routes_per_zone,
                    transport_plan, allocations, completion_metrics,
                    auto_approved
                ) VALUES (
                    :id, :ref, :did,
                    CASE WHEN :auto_status THEN 'APPROVED' ELSE 'PENDING' END,
                    :zones, :pop, :roads, :traffic,
                    :shelters, :routes, :transport, :alloc, :metrics,
                    :auto_flag
                )
            """),
            {
                "id":          plan_id,
                "ref":         plan_ref,
                "did":         disaster_id,
                "auto_status": auto_approved,
                "auto_flag":   auto_approved,
                "zones":       json.dumps(impact_zones),
                "pop":         json.dumps(population_stats),
                "roads":       json.dumps(road_names),
                "traffic":     json.dumps(traffic_snapshot),
                "shelters":    json.dumps(shelters_with_capacity),
                "routes":      json.dumps(best_routes_per_zone),
                "transport":   json.dumps(transport_plan),
                "alloc":       json.dumps(allocations),
                "metrics":     json.dumps({}),
            },
        )
        await self.db.flush()
        return plan_id

    async def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        result = await self.db.execute(
            text("""
                SELECT id, plan_ref, disaster_id, plan_status,
                       impact_zones, population_stats, blocked_roads, traffic_snapshot,
                       shelters_with_capacity, best_routes_per_zone, transport_plan,
                       allocations, completion_metrics, auto_approved,
                       approved_by, approved_at, activated_at, completed_at, notes,
                       created_at, updated_at
                FROM evacuation_plans
                WHERE id = :pid AND deleted_at IS NULL
            """),
            {"pid": plan_id},
        )
        row = result.mappings().first()
        if not row:
            return None
        return self._deserialise(dict(row))

    async def update_plan(self, plan_id: str, **fields) -> bool:
        """
        Generic column updater.

        Callers pass keyword args matching column names.
        JSON-serialises dict/list values automatically.

        All timestamp columns in evacuation_plans are TIMESTAMP WITHOUT TIME ZONE.
        Always use datetime.utcnow() (naive) — never datetime.now(tz=timezone.utc).
        """
        if not fields:
            return True

        set_clauses = []
        params: Dict[str, Any] = {
            "pid":        plan_id,
            "updated_at": datetime.utcnow(),  # naive — matches TIMESTAMP WITHOUT TIME ZONE
        }

        for col, val in fields.items():
            set_clauses.append(f"{col} = :{col}")
            params[col] = json.dumps(val) if isinstance(val, (dict, list)) else val

        set_clauses.append("updated_at = :updated_at")
        sql = f"UPDATE evacuation_plans SET {', '.join(set_clauses)} WHERE id = :pid"

        await self.db.execute(text(sql), params)
        await self.db.flush()
        return True

    async def list_plans(self, disaster_id: Optional[str] = None) -> List[Dict[str, Any]]:
        where  = "WHERE deleted_at IS NULL"
        params: Dict[str, Any] = {}
        if disaster_id:
            where += " AND disaster_id = :did"
            params["did"] = disaster_id

        result = await self.db.execute(
            text(
                f"SELECT id, plan_ref, disaster_id, plan_status, auto_approved, "
                f"approved_by, approved_at, activated_at, created_at, updated_at "
                f"FROM evacuation_plans {where} ORDER BY created_at DESC LIMIT 50"
            ),
            params,
        )
        return [
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(r).items()}
            for r in result.mappings().all()
        ]

    async def get_disaster_by_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        result = await self.db.execute(
            text("""
                SELECT d.id,
                       ST_Y(d.location::geometry) AS lat,
                       ST_X(d.location::geometry) AS lon,
                       d.severity
                FROM disasters d
                JOIN evacuation_plans ep ON ep.disaster_id = d.id
                WHERE ep.id = :pid
            """),
            {"pid": plan_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None
    
    async def get_on_scene_transport_units(self, disaster_id: str) -> List[Dict[str, Any]]:
        """
        Return ambulances and rescue units already ON_SCENE for this disaster.
        These are physically present — prioritised over the AVAILABLE pool.
        """
        try:
            result = await self.db.execute(
                text("""
                    SELECT
                        eu.id,
                        eu.unit_code,
                        LOWER(eu.unit_type::text)  AS unit_type,
                        eu.capacity,
                        dep.id                     AS deployment_id,
                        dep.deployment_status
                    FROM deployments dep
                    JOIN emergency_units eu ON dep.unit_id = eu.id
                    WHERE dep.disaster_id       = :did
                    AND dep.deployment_status IN ('ON_SCENE', 'IN_PROGRESS')
                    AND eu.unit_type IN (
                            CAST('AMBULANCE' AS unit_type),
                            CAST('RESCUE'    AS unit_type)
                        )
                    AND dep.deleted_at IS NULL
                    AND eu.deleted_at  IS NULL
                """),
                {"did": disaster_id},
            )
            rows = result.mappings().all()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning(f"[UC8] get_on_scene_transport_units failed: {exc}")
            return []

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _deserialise(row: Dict) -> Dict:
        """Parse JSONB string columns back into Python objects."""
        json_cols = {
            "impact_zones", "population_stats", "blocked_roads",
            "traffic_snapshot", "shelters_with_capacity",
            "best_routes_per_zone", "transport_plan", "allocations",
            "completion_metrics",
        }
        out = {}
        for k, v in row.items():
            if k in json_cols and isinstance(v, str):
                try:
                    out[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    out[k] = v
            elif hasattr(v, "isoformat"):
                out[k] = v.isoformat()
            else:
                out[k] = v
        return out