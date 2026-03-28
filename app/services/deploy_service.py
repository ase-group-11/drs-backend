"""
app/services/deploy_service.py

UC6 Deploy Services — New business logic.

Complements the existing deployment_service.py WITHOUT modifying it.
Handles the four missing pieces:
  1. get_suggested_units    — recommend unit types based on disaster type + severity
  2. update_gps_location    — responder phone pings GPS position
  3. get_unit_positions     — all unit positions for the admin map
  4. recall_unit            — admin recalls a deployed unit

Patterns followed (same as deployment_service.py):
  - Raw SQL via text()
  - Enums always UPPERCASE with CAST
  - datetime.now(tz=timezone.utc) for timestamps
  - _pending_event dict returned to API layer for post-commit RabbitMQ publish
  - flush() inside service, commit owned by get_db() dependency
"""

import math
import logging
import json as _json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Service requirement map
# (disaster_type, severity) → [(unit_type, required_count, reason)]
# ─────────────────────────────────────────────────────────────────────────────

_SERVICE_MAP: Dict[tuple, List[tuple]] = {
    # ── FIRE ─────────────────────────────────────────────────────────────────
    ("FIRE", "CRITICAL"): [
        ("FIRE_ENGINE", 3, "Primary fire suppression"),
        ("AMBULANCE",   2, "Casualty response"),
        ("PATROL_CAR",  1, "Crowd and traffic control"),
        ("RESCUE",      1, "Structural rescue"),
    ],
    ("FIRE", "HIGH"): [
        ("FIRE_ENGINE", 2, "Fire suppression"),
        ("AMBULANCE",   1, "Medical standby"),
        ("PATROL_CAR",  1, "Traffic control"),
    ],
    ("FIRE", "MEDIUM"): [
        ("FIRE_ENGINE", 1, "Fire suppression"),
        ("AMBULANCE",   1, "Medical standby"),
    ],
    ("FIRE", "LOW"): [
        ("FIRE_ENGINE", 1, "Fire suppression"),
    ],

    # ── FLOOD ────────────────────────────────────────────────────────────────
    ("FLOOD", "CRITICAL"): [
        ("RESCUE",     3, "Water rescue operations"),
        ("AMBULANCE",  2, "Casualty response"),
        ("PATROL_CAR", 2, "Area evacuation and control"),
    ],
    ("FLOOD", "HIGH"): [
        ("RESCUE",     2, "Water rescue"),
        ("AMBULANCE",  1, "Medical response"),
        ("PATROL_CAR", 1, "Area control"),
    ],
    ("FLOOD", "MEDIUM"): [
        ("RESCUE",     1, "Water rescue"),
        ("PATROL_CAR", 1, "Area control"),
    ],
    ("FLOOD", "LOW"): [
        ("RESCUE", 1, "Water rescue"),
    ],

    # ── EARTHQUAKE ───────────────────────────────────────────────────────────
    ("EARTHQUAKE", "CRITICAL"): [
        ("RESCUE",      4, "Structural rescue"),
        ("AMBULANCE",   3, "Mass casualty response"),
        ("FIRE_ENGINE", 2, "Fire prevention and rescue"),
        ("PATROL_CAR",  2, "Crowd control"),
        ("HAZMAT",      1, "Gas leak response"),
    ],
    ("EARTHQUAKE", "HIGH"): [
        ("RESCUE",      2, "Structural rescue"),
        ("AMBULANCE",   2, "Casualty response"),
        ("FIRE_ENGINE", 1, "Fire prevention"),
        ("PATROL_CAR",  1, "Crowd control"),
    ],
    ("EARTHQUAKE", "MEDIUM"): [
        ("RESCUE",     1, "Structural rescue"),
        ("AMBULANCE",  1, "Medical response"),
        ("PATROL_CAR", 1, "Crowd control"),
    ],
    ("EARTHQUAKE", "LOW"): [
        ("RESCUE", 1, "Structural rescue"),
    ],

    # ── ACCIDENT ─────────────────────────────────────────────────────────────
    ("ACCIDENT", "CRITICAL"): [
        ("AMBULANCE",   3, "Mass casualty response"),
        ("PATROL_CAR",  2, "Traffic control and scene management"),
        ("RESCUE",      1, "Vehicle entrapment rescue"),
        ("FIRE_ENGINE", 1, "Fire prevention"),
    ],
    ("ACCIDENT", "HIGH"): [
        ("AMBULANCE",  2, "Casualty response"),
        ("PATROL_CAR", 1, "Traffic control"),
        ("RESCUE",     1, "Rescue"),
    ],
    ("ACCIDENT", "MEDIUM"): [
        ("AMBULANCE",  1, "Casualty response"),
        ("PATROL_CAR", 1, "Traffic control"),
    ],
    ("ACCIDENT", "LOW"): [
        ("AMBULANCE", 1, "Casualty response"),
    ],

    # ── HAZMAT ───────────────────────────────────────────────────────────────
    ("HAZMAT", "CRITICAL"): [
        ("HAZMAT",      2, "Hazardous materials response"),
        ("AMBULANCE",   2, "Decontamination and medical support"),
        ("PATROL_CAR",  2, "Exclusion zone enforcement"),
        ("FIRE_ENGINE", 1, "Fire prevention"),
    ],
    ("HAZMAT", "HIGH"): [
        ("HAZMAT",     1, "Hazmat response"),
        ("AMBULANCE",  1, "Medical standby"),
        ("PATROL_CAR", 1, "Exclusion zone"),
    ],
    ("HAZMAT", "MEDIUM"): [
        ("HAZMAT",     1, "Hazmat response"),
        ("PATROL_CAR", 1, "Exclusion zone"),
    ],
    ("HAZMAT", "LOW"): [
        ("HAZMAT", 1, "Hazmat response"),
    ],

    # ── MEDICAL EMERGENCY ────────────────────────────────────────────────────
    ("MEDICAL_EMERGENCY", "CRITICAL"): [
        ("AMBULANCE",  3, "Mass casualty response"),
        ("PATROL_CAR", 1, "Traffic clearance"),
    ],
    ("MEDICAL_EMERGENCY", "HIGH"): [
        ("AMBULANCE", 2, "Casualty response"),
    ],
    ("MEDICAL_EMERGENCY", "MEDIUM"): [
        ("AMBULANCE", 1, "Casualty response"),
    ],
    ("MEDICAL_EMERGENCY", "LOW"): [
        ("AMBULANCE", 1, "Casualty response"),
    ],

    # ── GAS LEAK ─────────────────────────────────────────────────────────────
    ("GAS_LEAK", "CRITICAL"): [
        ("HAZMAT",      2, "Gas leak response"),
        ("FIRE_ENGINE", 2, "Fire prevention"),
        ("PATROL_CAR",  2, "Evacuation and exclusion zone"),
        ("AMBULANCE",   1, "Medical standby"),
    ],
    ("GAS_LEAK", "HIGH"): [
        ("HAZMAT",      1, "Gas leak response"),
        ("FIRE_ENGINE", 1, "Fire prevention"),
        ("PATROL_CAR",  1, "Evacuation support"),
    ],
    ("GAS_LEAK", "MEDIUM"): [
        ("HAZMAT",      1, "Gas leak response"),
        ("FIRE_ENGINE", 1, "Fire prevention"),
    ],
    ("GAS_LEAK", "LOW"): [
        ("HAZMAT", 1, "Gas leak response"),
    ],

    # ── BUILDING COLLAPSE ────────────────────────────────────────────────────
    ("BUILDING_COLLAPSE", "CRITICAL"): [
        ("RESCUE",      4, "Structural rescue"),
        ("AMBULANCE",   3, "Mass casualty response"),
        ("FIRE_ENGINE", 2, "Fire prevention"),
        ("PATROL_CAR",  2, "Perimeter control"),
        ("HAZMAT",      1, "Gas/chemical risk management"),
    ],
    ("BUILDING_COLLAPSE", "HIGH"): [
        ("RESCUE",      2, "Structural rescue"),
        ("AMBULANCE",   2, "Casualty response"),
        ("FIRE_ENGINE", 1, "Fire prevention"),
        ("PATROL_CAR",  1, "Perimeter control"),
    ],
    ("BUILDING_COLLAPSE", "MEDIUM"): [
        ("RESCUE",    1, "Structural rescue"),
        ("AMBULANCE", 1, "Medical response"),
    ],
    ("BUILDING_COLLAPSE", "LOW"): [
        ("RESCUE", 1, "Structural rescue"),
    ],

    # ── EXPLOSION ────────────────────────────────────────────────────────────
    ("EXPLOSION", "CRITICAL"): [
        ("FIRE_ENGINE", 3, "Fire suppression"),
        ("RESCUE",      3, "Structural rescue"),
        ("AMBULANCE",   3, "Mass casualty response"),
        ("PATROL_CAR",  2, "Perimeter and crowd control"),
        ("HAZMAT",      1, "Chemical/gas risk"),
    ],
    ("EXPLOSION", "HIGH"): [
        ("FIRE_ENGINE", 2, "Fire suppression"),
        ("RESCUE",      2, "Structural rescue"),
        ("AMBULANCE",   2, "Casualty response"),
        ("PATROL_CAR",  1, "Perimeter control"),
    ],
    ("EXPLOSION", "MEDIUM"): [
        ("FIRE_ENGINE", 1, "Fire suppression"),
        ("RESCUE",      1, "Structural rescue"),
        ("AMBULANCE",   1, "Medical response"),
    ],
    ("EXPLOSION", "LOW"): [
        ("FIRE_ENGINE", 1, "Fire suppression"),
        ("AMBULANCE",   1, "Medical standby"),
    ],

    # ── STORM ────────────────────────────────────────────────────────────────
    ("STORM", "CRITICAL"): [
        ("RESCUE",     3, "Storm rescue operations"),
        ("AMBULANCE",  2, "Casualty response"),
        ("PATROL_CAR", 2, "Road safety and evacuation"),
    ],
    ("STORM", "HIGH"): [
        ("RESCUE",     2, "Storm rescue"),
        ("PATROL_CAR", 1, "Road safety"),
        ("AMBULANCE",  1, "Medical standby"),
    ],
    ("STORM", "MEDIUM"): [
        ("RESCUE",     1, "Storm rescue"),
        ("PATROL_CAR", 1, "Road safety"),
    ],
    ("STORM", "LOW"): [
        ("PATROL_CAR", 1, "Road safety"),
    ],
}

# Fallback for any unrecognised disaster type
_DEFAULT_SUGGESTIONS: Dict[str, List[tuple]] = {
    "CRITICAL": [("PATROL_CAR", 2, "General emergency response"), ("AMBULANCE", 1, "Medical standby")],
    "HIGH":     [("PATROL_CAR", 1, "General emergency response"), ("AMBULANCE", 1, "Medical standby")],
    "MEDIUM":   [("PATROL_CAR", 1, "General emergency response")],
    "LOW":      [("PATROL_CAR", 1, "General emergency response")],
}


# ─────────────────────────────────────────────────────────────────────────────
# Haversine helper (shared with route_service)
# ─────────────────────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line great-circle distance in km between two WGS-84 points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ─────────────────────────────────────────────────────────────────────────────
# DeployService
# ─────────────────────────────────────────────────────────────────────────────

class DeployService:
    """
    Business logic for the four missing UC6 functions.
    Takes AsyncSession directly — same pattern as DeploymentService.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ─────────────────────────────────────────────────────────
    # 1. Suggested Units
    # ─────────────────────────────────────────────────────────

    async def get_suggested_units(self, disaster_id: str) -> Dict[str, Any]:
        """
        Determine required service types for a disaster and cross-reference
        with actual AVAILABLE unit counts.  Returns a suggestion list with
        shortage flags so the admin knows whether to request mutual aid.

        Sequence diagram steps:
          determineRequiredServices(disasterType, severity)
          queryAvailableUnits(serviceType, location)   [loop per type]
          markShortage(serviceType)                    [alt: insufficient]
        """
        # ── Fetch disaster ──────────────────────────────────────────────────
        dis_result = await self.db.execute(text("""
            SELECT id, tracking_id, type, severity, disaster_status,
                   multiple_casualties, road_blocked,
                   location_address,
                   ST_Y(location::geometry) AS lat,
                   ST_X(location::geometry) AS lon
            FROM disasters
            WHERE id = :did AND deleted_at IS NULL
        """), {"did": disaster_id})
        disaster = dis_result.mappings().first()

        if not disaster:
            raise HTTPException(status_code=404, detail="Disaster not found.")

        dtype    = str(disaster["type"]).upper()
        severity = str(disaster["severity"]).upper()

        # ── Look up base suggestions ────────────────────────────────────────
        base: List[tuple] = _SERVICE_MAP.get(
            (dtype, severity),
            _DEFAULT_SUGGESTIONS.get(severity, [("PATROL_CAR", 1, "General response")]),
        )

        # ── Adjust for disaster flags ───────────────────────────────────────
        extra: List[tuple] = []
        current_types = {s[0] for s in base}

        if disaster["multiple_casualties"] and "AMBULANCE" not in current_types:
            extra.append(("AMBULANCE", 1, "Added: multiple casualties reported"))

        if disaster["road_blocked"] and "PATROL_CAR" not in current_types:
            extra.append(("PATROL_CAR", 1, "Added: road blocked at scene"))

        all_suggestions = list(base) + extra

        # ── Query available counts by unit type ─────────────────────────────
        avail_result = await self.db.execute(text("""
            SELECT unit_type, COUNT(*) AS available_count
            FROM emergency_units
            WHERE unit_status = CAST('AVAILABLE' AS unit_status)
              AND deleted_at IS NULL
            GROUP BY unit_type
        """))
        avail_map: Dict[str, int] = {
            str(row["unit_type"]).upper(): int(row["available_count"])
            for row in avail_result.mappings().all()
        }

        # ── Build suggestions with shortage info ────────────────────────────
        suggestions = []
        has_shortage = False

        for unit_type, required_count, reason in all_suggestions:
            available = avail_map.get(unit_type, 0)
            shortage  = max(0, required_count - available)
            if shortage > 0:
                has_shortage = True

            suggestions.append({
                "unit_type":       unit_type,
                "required_count":  required_count,
                "available_count": available,
                "shortage":        shortage,
                "has_shortage":    shortage > 0,
                "reason":          reason,
            })

        return {
            "disaster_id":      disaster_id,
            "disaster_type":    dtype,
            "severity":         severity,
            "location_address": disaster["location_address"],
            "has_shortage":     has_shortage,
            "suggestions":      suggestions,
            "message": (
                "⚠️ Some unit types have shortages — consider requesting mutual aid."
                if has_shortage else
                "Sufficient units available for all recommended types."
            ),
        }

    # ─────────────────────────────────────────────────────────
    # 2. Update GPS Location
    # ─────────────────────────────────────────────────────────

    async def update_gps_location(
        self,
        deployment_id: str,
        latitude: float,
        longitude: float,
        heading: Optional[float] = None,
        speed_kmh: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Store the responder's current GPS position for a deployment.
        Called by the responder's mobile app every ~10 seconds.

        Sequence diagram: updateUnitLocation(unitId, location)
        """
        # ── Verify deployment exists and is active ──────────────────────────
        dep_result = await self.db.execute(text("""
            SELECT id, deployment_status, disaster_id
            FROM deployments
            WHERE id = :did AND deleted_at IS NULL
        """), {"did": deployment_id})
        dep = dep_result.mappings().first()

        if not dep:
            raise HTTPException(status_code=404, detail="Deployment not found.")

        current_status = str(dep["deployment_status"])
        if current_status in ("COMPLETED", "CANCELLED"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot update GPS for a {current_status} deployment.",
            )

        # Replace with:
        now_naive = datetime.utcnow()                    # for location_updated_at (no tz)
        now_aware = datetime.now(tz=timezone.utc)        # for updated_at (with tz)

        await self.db.execute(text("""
            UPDATE deployments
            SET current_latitude    = :lat,
                current_longitude   = :lon,
                heading             = :heading,
                speed_kmh           = :speed,
                location_updated_at = :now_loc,
                updated_at          = :now_upd
            WHERE id = :did
        """), {
            "lat":     latitude,
            "lon":     longitude,
            "heading": heading,
            "speed":   speed_kmh,
            "now_loc": now_naive,
            "now_upd": now_aware,
            "did":     deployment_id,
        })

        await self.db.flush()

        return {
            "deployment_id": deployment_id,
            "latitude":      latitude,
            "longitude":     longitude,
            "heading":       heading,
            "speed_kmh":     speed_kmh,
            "updated_at":    now_aware.isoformat(),
            "message":       "GPS position updated.",
        }

    # ─────────────────────────────────────────────────────────
    # 3. Unit Positions for Admin Map
    # ─────────────────────────────────────────────────────────

    async def get_unit_positions(self, disaster_id: str) -> Dict[str, Any]:
        """
        Return current positions of all non-completed units for a disaster.
        Falls back to station location when GPS hasn't been received yet.
        Estimates ETA for units still en-route.

        Sequence diagram: trackDeployedUnits + updateUnitPosition
        Polled every ~10 seconds by the admin's map.
        """
        # ── Verify disaster ─────────────────────────────────────────────────
        dis_result = await self.db.execute(text("""
            SELECT id,
                   ST_Y(location::geometry) AS lat,
                   ST_X(location::geometry) AS lon
            FROM disasters
            WHERE id = :did AND deleted_at IS NULL
        """), {"did": disaster_id})
        disaster = dis_result.mappings().first()

        if not disaster:
            raise HTTPException(status_code=404, detail="Disaster not found.")

        dis_lat = float(disaster["lat"])
        dis_lon = float(disaster["lon"])

        # ── Fetch all active deployments with unit details ──────────────────
        rows_result = await self.db.execute(text("""
            SELECT
                dep.id                  AS deployment_id,
                dep.deployment_status,
                dep.current_latitude,
                dep.current_longitude,
                dep.heading,
                dep.speed_kmh,
                dep.location_updated_at,
                eu.id                   AS unit_id,
                eu.unit_code,
                eu.unit_name,
                eu.unit_type,
                eu.department,
                ST_Y(eu.station_location::geometry) AS station_lat,
                ST_X(eu.station_location::geometry) AS station_lon
            FROM deployments dep
            JOIN emergency_units eu ON dep.unit_id = eu.id
            WHERE dep.disaster_id = :did
              AND dep.deployment_status NOT IN ('COMPLETED', 'CANCELLED')
              AND dep.deleted_at IS NULL
            ORDER BY dep.dispatched_at ASC
        """), {"did": disaster_id})
        rows = rows_result.mappings().all()

        # ── Build unit list ─────────────────────────────────────────────────
        units = []
        for row in rows:
            has_gps = row["current_latitude"] is not None

            if has_gps:
                cur_lat = float(row["current_latitude"])
                cur_lon = float(row["current_longitude"])
            elif row["station_lat"] is not None:
                cur_lat = float(row["station_lat"])
                cur_lon = float(row["station_lon"])
            else:
                cur_lat = None
                cur_lon = None

            # ETA for en-route units that have a known position
            eta_minutes = None
            dep_status  = str(row["deployment_status"])
            if cur_lat is not None and dep_status in ("DISPATCHED", "EN_ROUTE"):
                dist_km = _haversine_km(cur_lat, cur_lon, dis_lat, dis_lon)
                # Use reported speed if it's meaningful, else assume 40 km/h
                speed = (
                    float(row["speed_kmh"])
                    if row["speed_kmh"] and float(row["speed_kmh"]) > 5
                    else 40.0
                )
                eta_minutes = round(dist_km / speed * 60)

            units.append({
                "deployment_id":    str(row["deployment_id"]),
                "unit_id":          str(row["unit_id"]),
                "unit_code":        str(row["unit_code"]),
                "unit_name":        str(row["unit_name"]),
                "unit_type":        str(row["unit_type"]),
                "department":       str(row["department"]),
                "deployment_status": dep_status,
                "position": {
                    "latitude":     cur_lat,
                    "longitude":    cur_lon,
                    "is_gps":       has_gps,
                    "heading":      float(row["heading"])   if row["heading"]   else None,
                    "speed_kmh":    float(row["speed_kmh"]) if row["speed_kmh"] else None,
                    "last_updated": (
                        row["location_updated_at"].isoformat()
                        if row["location_updated_at"] else None
                    ),
                },
                "eta_minutes": eta_minutes,
            })

        return {
            "disaster_id":       disaster_id,
            "disaster_location": {"lat": dis_lat, "lon": dis_lon},
            "unit_count":        len(units),
            "units":             units,
        }

    # ─────────────────────────────────────────────────────────
    # 4. Recall Unit
    # ─────────────────────────────────────────────────────────

    async def recall_unit(self, deployment_id: str, reason: str) -> Dict[str, Any]:
        """
        Recall a deployed unit back to base.
          - Deployment status → CANCELLED
          - Unit status       → AVAILABLE
          - Audit log entry written
          - Returns _pending_event for RabbitMQ (published by API after commit)

        Sequence diagram: recallActiveUnits + updateDeploymentRecord + logEvent
        """
        # ── Fetch deployment ────────────────────────────────────────────────
        dep_result = await self.db.execute(text("""
            SELECT dep.id, dep.disaster_id, dep.unit_id, dep.deployment_status,
                   dis.tracking_id,
                   eu.unit_code, eu.unit_name
            FROM deployments dep
            JOIN disasters     dis ON dep.disaster_id = dis.id
            JOIN emergency_units eu ON dep.unit_id    = eu.id
            WHERE dep.id = :did AND dep.deleted_at IS NULL
        """), {"did": deployment_id})
        dep = dep_result.mappings().first()

        if not dep:
            raise HTTPException(status_code=404, detail="Deployment not found.")

        current_status = str(dep["deployment_status"])
        if current_status in ("COMPLETED", "CANCELLED"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot recall a {current_status} deployment.",
            )

        now_naive = datetime.utcnow()              # for completed_at (naive column)
        now_aware = datetime.now(tz=timezone.utc)  # for updated_at (aware column)

        # ── updateDeploymentRecord ──────────────────────────────────────────
        await self.db.execute(text("""
            UPDATE deployments
            SET deployment_status = 'CANCELLED',
                completed_at      = :completed_at,
                updated_at        = :updated_at
            WHERE id = :did
        """), {"completed_at": now_naive, "updated_at": now_aware, "did": deployment_id})

        # ── Free the unit ───────────────────────────────────────────────────
        await self.db.execute(text("""
            UPDATE emergency_units
            SET unit_status = CAST('AVAILABLE' AS unit_status),
                updated_at  = :updated_at
            WHERE id = :uid
        """), {"updated_at": now_aware, "uid": str(dep["unit_id"])})

        # ── logEvent (audit_logs) ───────────────────────────────────────────
        try:
            await self.db.execute(text("""
                INSERT INTO audit_logs
                    (id, created_at, updated_at, disaster_id, event_type, event_data, triggered_by)
                VALUES
                    (gen_random_uuid(), :log_created, :log_updated, :disaster_id,
                     'unit_recalled', :event_data::jsonb, 'operator')
            """), {
                "log_created": now_naive,
                "log_updated": now_aware,
                "disaster_id": str(dep["disaster_id"]),
                "event_data":  _json.dumps({
                    "deployment_id": deployment_id,
                    "unit_code":     str(dep["unit_code"]),
                    "reason":        reason,
                }),
            })
        except Exception as log_err:
            logger.warning(f"recall_unit: audit log write failed (non-fatal): {log_err}")

        await self.db.flush()

        # ── Build pending RabbitMQ event ────────────────────────────────────
        pending_event = {
            "topic": "disaster.unit_recalled",
            "payload": {
                "disaster_id":   str(dep["disaster_id"]),
                "tracking_id":   str(dep["tracking_id"]),
                "deployment_id": deployment_id,
                "unit_id":       str(dep["unit_id"]),
                "unit_code":     str(dep["unit_code"]),
                "reason":        reason,
            },
        }

        return {
            "deployment_id":   deployment_id,
            "unit_id":         str(dep["unit_id"]),
            "unit_code":       str(dep["unit_code"]),
            "unit_name":       str(dep["unit_name"]),
            "previous_status": current_status,
            "new_status":      "CANCELLED",
            "recalled_at":     now_aware.isoformat(),
            "reason":          reason,
            "message":         f"{dep['unit_code']} has been recalled to base.",
            "_pending_event":  pending_event,
        }