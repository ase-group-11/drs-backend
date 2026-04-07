# File: app/repositories/evacuation_repository.py
"""
Evacuation Repository — UC8: Plan Evacuation

Database access layer for EvacuationService.
Mirrors the pattern of RerouteRepository: takes AsyncSession in __init__,
provides async methods, returns plain dicts (not ORM objects).

Methods match exactly what EvacuationService needs so the service
stays free of raw SQL and is fully testable by mocking this class.
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
        """Fetch disaster with PostGIS lat/lon extraction."""
        result = await self.db.execute(
            text("""
                SELECT id, tracking_id, type, severity, disaster_status,
                       ST_Y(location::geometry) AS lat,
                       ST_X(location::geometry) AS lon,
                       location_address, people_affected, road_blocked
                FROM disasters
                WHERE id = :did AND deleted_at IS NULL
            """),
            {"did": disaster_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

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

    async def get_users_in_zones(self, zones: List[Dict]) -> List[Dict[str, Any]]:
        """
        Return registered users. Note: users table has no lat/lon so we
        broadcast to all active users as safe-side approach.
        """
        try:
            result = await self.db.execute(
                text("SELECT id, full_name, phone_number FROM users "
                     "WHERE deleted_at IS NULL LIMIT 500")
            )
            rows  = result.mappings().all()
            first = zones[0] if zones else {}
            return [
                {**dict(u),
                 "zone_id":   first.get("zone_id", ""),
                 "zone_name": first.get("name", "affected area")}
                for u in rows
            ]
        except Exception as exc:
            logger.warning(f"[UC8] get_users_in_zones: {exc}")
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

        # created_at / updated_at are TIMESTAMP WITH TIME ZONE (base model columns)
        now_aware = datetime.now()

        # Store road names (strings) not full segment dicts
        road_names = [s.get("road_name", "") for s in blocked_roads]

        await self.db.execute(
            text("""
                INSERT INTO evacuation_plans (
                    id, plan_ref, disaster_id, plan_status,
                    impact_zones, population_stats, blocked_roads, traffic_snapshot,
                    shelters_with_capacity, best_routes_per_zone,
                    transport_plan, allocations, completion_metrics,
                    auto_approved, created_at, updated_at
                ) VALUES (
                    :id, :ref, :did,
                    CASE WHEN :auto_status THEN 'APPROVED' ELSE 'PENDING' END,
                    :zones, :pop, :roads, :traffic,
                    :shelters, :routes, :transport, :alloc, :metrics,
                    :auto_flag, :created_at, :updated_at
                )
            """),
            {
                "id":          plan_id,
                "ref":         plan_ref,
                "did":         disaster_id,
                "auto_status": auto_approved,   # used in CASE WHEN
                "auto_flag":   auto_approved,   # stored in auto_approved column
                "zones":       json.dumps(impact_zones),
                "pop":         json.dumps(population_stats),
                "roads":       json.dumps(road_names),
                "traffic":     json.dumps(traffic_snapshot),
                "shelters":    json.dumps(shelters_with_capacity),
                "routes":      json.dumps(best_routes_per_zone),
                "transport":   json.dumps(transport_plan),
                "alloc":       json.dumps(allocations),
                "metrics":     json.dumps({}),
                "created_at":  now_aware,
                "updated_at":  now_aware,
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
        Generic column updater. Callers pass keyword args matching column names.
        JSON-serialises dict/list values automatically.

        updated_at is TIMESTAMP WITH TIME ZONE — use aware datetime.
        All other timestamp fields (approved_at, activated_at, completed_at)
        are TIMESTAMP WITHOUT TIME ZONE — callers pass naive datetimes for those.
        """
        if not fields:
            return True

        set_clauses = []
        # updated_at is WITH TIME ZONE on the base model
        params: Dict[str, Any] = {
            "pid":        plan_id,
            "updated_at": datetime.now(),
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