# File: app/services/evacuation_service.py
"""
Evacuation Service — Use Case 8: Plan Evacuation

Constructor injection pattern — identical to RerouteService:
  self.db       → EvacuationRepository  (all DB access)
  self.external → IntegrationService    (TomTom routing + traffic)
  self.mapping  → MappingService        (Socket.IO map overlay)
  self.publisher→ ReroutePublisher      (RabbitMQ notifications)

Tests pass AsyncMock objects directly into __init__ — no patch() needed.

Zone/shelter data lives at module level (same pattern as _region_id_to_bounds
in integration_service.py) — no separate graph module.
"""

import asyncio
import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from app.providers.integration_service import IntegrationService
from app.repositories.evacuation_repository import EvacuationRepository
from app.services.instant_map_updates import MappingService
from app.workers.reroute_publisher import ReroutePublisher

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
BUS_CAPACITY       = 50
AMBULANCE_CAPACITY = 8   # accessible medical transport (minibus/wheelchair van),
                          # NOT an ICU ambulance. 8 people per vehicle is standard
                          # for evacuation-planning purposes.

CONGESTION_WEIGHTS = {
    "light": 0.5, "moderate": 1.5, "heavy": 2.5, "severe": 4.0, "unknown": 1.0,
}

SEVERITY_RADIUS: Dict[str, float] = {
    "CRITICAL": 5.0, "HIGH": 3.0, "MEDIUM": 2.0, "LOW": 1.5,
}

EVACUATION_REGION = "region-dublin-city"

# ── Dublin evacuation zones ───────────────────────────────────────────────────
DUBLIN_ZONES: List[Dict[str, Any]] = [
    {"zone_id": "zone_city_centre",    "name": "City Centre",             "lat": 53.3498, "lon": -6.2603, "population": 25000, "vulnerable_count": 3000},
    {"zone_id": "zone_northside",      "name": "Northside (Drumcondra)",  "lat": 53.3780, "lon": -6.2500, "population": 40000, "vulnerable_count": 5000},
    {"zone_id": "zone_southside",      "name": "Southside (Terenure)",    "lat": 53.3200, "lon": -6.2700, "population": 35000, "vulnerable_count": 4200},
    {"zone_id": "zone_docklands",      "name": "Docklands",               "lat": 53.3480, "lon": -6.2300, "population": 18000, "vulnerable_count": 1500},
    {"zone_id": "zone_rathmines",      "name": "Rathmines",               "lat": 53.3256, "lon": -6.2653, "population": 22000, "vulnerable_count": 2800},
    {"zone_id": "zone_ballsbridge",    "name": "Ballsbridge",             "lat": 53.3268, "lon": -6.2182, "population": 15000, "vulnerable_count": 1800},
    {"zone_id": "zone_clontarf",       "name": "Clontarf",                "lat": 53.3634, "lon": -6.2100, "population": 20000, "vulnerable_count": 2400},
    {"zone_id": "zone_ringsend",       "name": "Ringsend / Irishtown",    "lat": 53.3400, "lon": -6.2200, "population": 12000, "vulnerable_count": 1200},
]

_ZONE_MAP: Dict[str, Dict] = {z["zone_id"]: z for z in DUBLIN_ZONES}

# ── Dublin evacuation shelters ────────────────────────────────────────────────
DUBLIN_SHELTERS: List[Dict[str, Any]] = [
    {"shelter_id": "shelter_croke_park",   "name": "Croke Park",              "lat": 53.3608, "lon": -6.2510, "capacity": 15000},
    {"shelter_id": "shelter_aviva",        "name": "Aviva Stadium",            "lat": 53.3338, "lon": -6.2286, "capacity": 10000},
    {"shelter_id": "shelter_rds",          "name": "RDS Arena",                "lat": 53.3213, "lon": -6.2265, "capacity": 8000},
    {"shelter_id": "shelter_phoenix_park", "name": "Phoenix Park Visitor Ctr", "lat": 53.3560, "lon": -6.3260, "capacity": 5000},
    {"shelter_id": "shelter_tallaght",     "name": "Tallaght Stadium",         "lat": 53.2876, "lon": -6.3740, "capacity": 7000},
    {"shelter_id": "shelter_malahide",     "name": "Malahide Castle Grounds",  "lat": 53.4508, "lon": -6.1541, "capacity": 4000},
    {"shelter_id": "shelter_blanchardstown","name": "Blanchardstown Centre",   "lat": 53.3900, "lon": -6.3800, "capacity": 6000},
    {"shelter_id": "shelter_leopardstown", "name": "Leopardstown Racecourse",  "lat": 53.2800, "lon": -6.1800, "capacity": 5000},
]


# ── Module-level helpers ──────────────────────────────────────────────────────

def get_zones_near_disaster(lat: float, lon: float, severity: str) -> List[Dict]:
    """Return zones within SEVERITY_RADIUS km of the disaster centre."""
    radius_km = SEVERITY_RADIUS.get(severity.upper(), 3.0)
    result = []
    for z in DUBLIN_ZONES:
        dist = math.sqrt((z["lat"] - lat) ** 2 + (z["lon"] - lon) ** 2) * 111
        if dist <= radius_km:
            result.append(z)
    # Always include at least one zone (nearest) to avoid empty plans
    if not result and DUBLIN_ZONES:
        nearest = min(DUBLIN_ZONES,
                      key=lambda z: (z["lat"] - lat) ** 2 + (z["lon"] - lon) ** 2)
        result = [nearest]
    return result


def get_all_shelters() -> List[Dict]:
    return list(DUBLIN_SHELTERS)


def get_population_profile(zones: List[Dict]) -> Dict[str, Any]:
    total    = sum(z["population"]       for z in zones)
    vuln     = sum(z["vulnerable_count"] for z in zones)
    mobile   = total - vuln
    return {
        "total":          total,
        "vulnerable":     vuln,
        "mobile":         mobile,
        "zones_count":    len(zones),
        "density_factor": round(vuln / total if total else 0.15, 2),
    }


def compute_transport_needs(
    population_stats: Dict,
    impact_zones: List[Dict],
    best_routes_per_zone: Dict,
) -> Dict[str, Any]:
    total    = population_stats["total"]
    vuln     = population_stats["vulnerable"]
    zone_map = {z["zone_id"]: z for z in impact_zones}
    schedules = []
    for zone_id, routes in best_routes_per_zone.items():
        if not routes:
            continue
        zone = zone_map.get(zone_id, {})
        best = routes[0]
        schedules.append({
            "zone_id":            zone_id,
            "zone_name":          zone.get("name", zone_id),
            "shelter_id":         best.get("destination_shelter_id", ""),
            "shelter_name":       best.get("shelter_name", ""),
            "route_id":           best.get("route_id", ""),
            "buses_needed":       max(1, math.ceil(zone.get("population", 0) / BUS_CAPACITY)),
            "ambulances_needed":  max(1, math.ceil(zone.get("vulnerable_count", 0) / AMBULANCE_CAPACITY)),
            "estimated_time_min": best.get("estimated_time_min", 30),
        })
    return {
        "total_buses":      math.ceil(total / BUS_CAPACITY),
        "total_ambulances": math.ceil(vuln / AMBULANCE_CAPACITY),
        "total_people":     total,
        "total_vulnerable": vuln,
        "schedules":        schedules,
    }


def allocate_resources(transport_plan: Dict) -> Dict[str, Any]:
    return {
        "buses_allocated":      transport_plan["total_buses"],
        "ambulances_allocated": transport_plan["total_ambulances"],
        "allocation_confirmed": True,
        "allocated_at":         datetime.utcnow().isoformat(),  # JSON field only, not a DB column
    }


def score_and_select_routes(
    candidates: List[Dict], traffic_snapshot: Dict
) -> List[Dict]:
    """score = distance×0.4 + congestion×0.4 + delay_min×0.2. Top 3 returned."""
    if not candidates:
        return []
    segments = traffic_snapshot.get("segments", []) if isinstance(traffic_snapshot, dict) else []
    avg_cng  = avg_congestion_weight(segments)
    scored   = []
    for i, r in enumerate(candidates):
        delay_min = r.get("traffic_delay_seconds", 0) / 60
        score     = r.get("distance_km", 99.0) * 0.4 + avg_cng * 0.4 + delay_min * 0.2
        scored.append({
            **r,
            "route_id": r.get("route_id") or f"route_{r['origin_zone_id']}_{r['destination_shelter_id']}_{i}",
            "score": round(score, 3),
        })
    scored.sort(key=lambda r: r["score"])
    return scored[:3]


def avg_congestion_weight(segments: List) -> float:
    """Handles UC7 congestion_ratio (float) and flow congestion_level (string)."""
    if not segments:
        return 1.0
    weights = []
    for s in segments:
        if "congestion_ratio" in s:
            r = s["congestion_ratio"]
            weights.append(0.5 if r < 0.2 else (1.5 if r < 0.5 else (2.5 if r < 0.7 else 4.0)))
        else:
            weights.append(CONGESTION_WEIGHTS.get(s.get("congestion_level", "unknown"), 1.0))
    return sum(weights) / len(weights)


def straight_line_fallback(zone: Dict, shelter: Dict) -> Optional[Dict]:
    """Minimal route when TomTom is unavailable."""
    try:
        dist  = math.sqrt((zone["lat"] - shelter["lat"]) ** 2
                          + (zone["lon"] - shelter["lon"]) ** 2) * 111
        t_min = round(dist / 30 * 60, 1)
        return {
            "route_id":               str(uuid.uuid4()),
            "origin_zone_id":         zone["zone_id"],
            "zone_name":              zone["name"],
            "destination_shelter_id": shelter["shelter_id"],
            "shelter_name":           shelter["name"],
            "shelter_capacity":       shelter["capacity"],
            "distance_km":            round(dist, 2),
            "estimated_time_min":     t_min,
            "travel_time_seconds":    int(t_min * 60),
            "length_meters":          int(dist * 1000),
            "traffic_delay_seconds":  0,
            "points":    [[zone["lat"], zone["lon"]], [shelter["lat"], shelter["lon"]]],
            "geojson":   {"type": "Feature",
                          "geometry": {"type": "LineString",
                                       "coordinates": [[zone["lon"], zone["lat"]],
                                                       [shelter["lon"], shelter["lat"]]]},
                          "properties": {"fallback": True}},
            "waypoints": [{"lat": zone["lat"], "lon": zone["lon"]},
                          {"lat": shelter["lat"], "lon": shelter["lon"]}],
            "fallback":  True,
            "score":     0.0,
        }
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════════════
# EvacuationService
# ═════════════════════════════════════════════════════════════════════════════

class EvacuationService:
    """
    Orchestrator for the full evacuation pipeline.

    All dependencies injected via constructor — fully testable with mocks.
    Mirrors RerouteService.__init__ exactly.
    """

    def __init__(
        self,
        db: EvacuationRepository,
        external: IntegrationService,
        mapping: MappingService,
        publisher: ReroutePublisher,
    ):
        self.db        = db
        self.external  = external
        self.mapping   = mapping
        self.publisher = publisher

    # ═════════════════════════════════════════════════════════════════════════
    # PHASE 1 — PLAN
    # ═════════════════════════════════════════════════════════════════════════

    async def plan_evacuation(
        self, disaster_id: str, auto_approve: bool = False
    ) -> Dict[str, Any]:
        """
        Phase 1: gather data, compute routes, save plan.

        Steps (sequence diagram):
          1. getImpactZonesAndPriorities  → zone lookup
          2. getPopulationProfile         → pure calculation
          3. getBlockedRoads              → self.db (UC7's road_segments table)
          4. getTrafficConditions         → self.external (IntegrationService)
          5. getSheltersAndCapacity       → shelter lookup
          6. PAR/LOOP getDirections +
             scoreAndSelectRoutes         → self.external.get_directions() per zone
          7. computeTransportNeeds +
             allocateResources            → pure calculation
          8. saveEvacuationPlan           → self.db
        """
        logger.info(f"[UC8-Phase1] Planning evacuation for disaster {disaster_id}")

        # 1. Zones
        disaster = await self.db.get_disaster(disaster_id)
        if not disaster:
            raise HTTPException(status_code=404, detail="Disaster not found.")

        impact_zones = get_zones_near_disaster(
            disaster["lat"], disaster["lon"], str(disaster.get("severity", "HIGH")))
        if not impact_zones:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No evacuation zones found near this disaster location.",
            )

        # 2. Population
        population_stats = get_population_profile(impact_zones)

        # 3. Blocked roads (UC7's table)
        blocked_roads = await self.db.get_blocked_roads(disaster_id)

        # 4. Traffic (via IntegrationService — circuit breaker + retry included)
        traffic_snapshot = await self.fetch_traffic_data()

        # 5. Shelters
        shelters = get_all_shelters()

        # 6. PAR: concurrent route computation
        best_routes_per_zone = await self._compute_all_zone_routes(
            impact_zones, shelters, blocked_roads, traffic_snapshot)

        # 7. Transport + allocation
        transport_plan = compute_transport_needs(
            population_stats, impact_zones, best_routes_per_zone)
        allocations = allocate_resources(transport_plan)

        # 8. Persist
        plan_ref = await self.db.generate_plan_ref()
        plan_id  = await self.db.save_plan(
            disaster_id=disaster_id,
            plan_ref=plan_ref,
            impact_zones=impact_zones,
            population_stats=population_stats,
            blocked_roads=blocked_roads,
            traffic_snapshot=traffic_snapshot,
            shelters_with_capacity=shelters,
            best_routes_per_zone=best_routes_per_zone,
            transport_plan=transport_plan,
            allocations=allocations,
            auto_approved=auto_approve,
        )

        logger.info(f"[UC8-Phase1] Plan {plan_ref} saved ({len(impact_zones)} zones)")
        return {
            "plan_id": plan_id, "plan_ref": plan_ref, "disaster_id": disaster_id,
            "plan_status": "APPROVED" if auto_approve else "PENDING",
            "zones_count": len(impact_zones), "shelters_count": len(shelters),
            "total_population_affected": population_stats["total"],
            "total_vulnerable": population_stats["vulnerable"],
            "transport_plan_summary": {
                "total_buses":      transport_plan["total_buses"],
                "total_ambulances": transport_plan["total_ambulances"],
            },
            "auto_approved": auto_approve,
            "message": (
                "Plan created and auto-approved. Call /activate to start evacuation."
                if auto_approve else "Plan created. Awaiting approval via /approve."
            ),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # PHASE 2 — APPROVE
    # ═════════════════════════════════════════════════════════════════════════

    async def approve_evacuation(
        self, plan_id: str, approved_by: str, notes: Optional[str] = None
    ) -> Dict[str, Any]:
        plan = await self._get_plan_or_404(plan_id)

        if plan["plan_status"] == "APPROVED":
            raise HTTPException(status_code=400, detail="Plan is already approved.")
        if plan["plan_status"] != "PENDING":
            raise HTTPException(
                status_code=400,
                detail=f"Only PENDING plans can be approved. Current: {plan['plan_status']}",
            )

        # approved_at is TIMESTAMP WITHOUT TIME ZONE in evacuation_plans
        now = datetime.utcnow()
        await self.db.update_plan(
            plan_id,
            plan_status="APPROVED",
            approved_by=approved_by,
            approved_at=now,
            notes=notes,
        )
        logger.info(f"[UC8-Phase2] Plan {plan['plan_ref']} approved by {approved_by}")
        return {
            "plan_id": plan_id, "plan_ref": plan["plan_ref"],
            "plan_status": "APPROVED", "approved_by": approved_by,
            "approved_at": now.isoformat(),
            "message": "Plan approved. Call /activate to start the evacuation.",
        }

    # ═════════════════════════════════════════════════════════════════════════
    # PHASE 3 — ACTIVATE
    # ═════════════════════════════════════════════════════════════════════════

    async def activate_evacuation(self, plan_id: str) -> Dict[str, Any]:
        """
        Phase 3 — Activate an approved plan.

        Steps:
          1. getUsersInZones      → self.db
          2. broadcastAlerts      → self.publisher (RabbitMQ evacuation.triggered)
                                    + Twilio fallback
          3. displayEvacuation    → self.mapping (Socket.IO reroute_alert)
          4. dispatchResources    → allocation count
        """
        plan = await self._get_plan_or_404(plan_id)
        if plan["plan_status"] != "APPROVED":
            raise HTTPException(
                status_code=400,
                detail=f"Only APPROVED plans can be activated. Current: {plan['plan_status']}",
            )

        impact_zones   = plan["impact_zones"]
        users          = await self.db.get_users_in_zones(impact_zones)

        alerts_sent    = await self.broadcast_alerts(
            users, plan["disaster_id"], plan_id,
            plan["best_routes_per_zone"], plan["shelters_with_capacity"],
        )
        map_updated    = await self.display_evacuation_on_map(
            plan_id, impact_zones,
            plan["best_routes_per_zone"], plan["shelters_with_capacity"],
        )
        units_en_route = (plan["allocations"].get("buses_allocated", 0)
                          + plan["allocations"].get("ambulances_allocated", 0))

        initial_metrics = {
            z["zone_id"]: {"percentage": 0, "evacuated": 0,
                           "remaining": z["population"], "status": "in_progress"}
            for z in impact_zones
        }
        # activated_at is TIMESTAMP WITHOUT TIME ZONE
        now = datetime.utcnow()
        await self.db.update_plan(
            plan_id,
            plan_status="ACTIVE",
            activated_at=now,
            completion_metrics=initial_metrics,
        )

        logger.info(f"[UC8-Phase3] Plan {plan['plan_ref']} ACTIVE — alerts={alerts_sent}")
        return {
            "plan_id": plan_id, "plan_ref": plan["plan_ref"],
            "plan_status": "ACTIVE", "activated_at": now.isoformat(),
            "alerts_sent": alerts_sent, "map_updated": map_updated,
            "units_en_route": units_en_route, "zones_active": len(impact_zones),
            "message": f"Evacuation is live. {alerts_sent} residents notified.",
        }

    # ═════════════════════════════════════════════════════════════════════════
    # PHASE 4 — MONITOR
    # ═════════════════════════════════════════════════════════════════════════

    async def get_progress(self, plan_id: str) -> Dict[str, Any]:
        plan = await self._get_plan_or_404(plan_id)
        if plan["plan_status"] not in ("ACTIVE", "MONITORING"):
            raise HTTPException(status_code=400, detail="Plan is not active.")

        traffic_update = await self.fetch_traffic_data()
        metrics        = plan.get("completion_metrics") or {}
        total_pop      = sum(z["population"] for z in plan["impact_zones"])
        total_ev       = sum(m.get("evacuated", 0) for m in metrics.values()
                             if isinstance(m, dict))
        overall        = round(total_ev / total_pop * 100, 1) if total_pop else 0.0

        return {
            "plan_id": plan_id, "plan_ref": plan["plan_ref"],
            "plan_status": plan["plan_status"],
            "completion_metrics": metrics, "overall_completion": overall,
            "traffic_update": traffic_update,
            "last_updated": plan.get("updated_at", datetime.utcnow().isoformat()),
        }

    async def update_progress(
        self, plan_id: str, completion_metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        plan = await self._get_plan_or_404(plan_id)
        if plan["plan_status"] not in ("ACTIVE", "MONITORING"):
            raise HTTPException(status_code=400, detail="Plan is not active.")

        current    = dict(plan.get("completion_metrics") or {})
        current.update(completion_metrics)
        all_done   = all(isinstance(m, dict) and m.get("percentage", 0) >= 100
                         for m in current.values())
        new_status = "COMPLETED" if all_done else plan["plan_status"]

        update_fields: Dict[str, Any] = {
            "completion_metrics": current,
            "plan_status":        new_status,
        }
        # completed_at is TIMESTAMP WITHOUT TIME ZONE
        if all_done:
            update_fields["completed_at"] = datetime.utcnow()

        await self.db.update_plan(plan_id, **update_fields)

        total_pop = sum(z["population"] for z in plan["impact_zones"])
        total_ev  = sum(m.get("evacuated", 0) for m in current.values()
                        if isinstance(m, dict))
        overall   = round(total_ev / total_pop * 100, 1) if total_pop else 0.0

        logger.info(f"[UC8-Phase4] Progress {overall}% — status={new_status}")
        return {
            "plan_id": plan_id, "plan_ref": plan["plan_ref"],
            "plan_status": new_status, "completion_metrics": current,
            "overall_completion": overall,
            "message": "Evacuation complete!" if all_done else f"{overall}% evacuated.",
        }

    async def handle_route_blockage(
        self, plan_id: str, blocked_roads: List[str], affected_zone_ids: List[str]
    ) -> Dict[str, Any]:
        plan = await self._get_plan_or_404(plan_id)
        if plan["plan_status"] not in ("ACTIVE", "MONITORING"):
            raise HTTPException(status_code=400, detail="Plan is not active.")

        uc7_segments   = await self.db.get_blocked_roads(plan["disaster_id"])
        uc7_names      = [s["road_name"] for s in uc7_segments if s.get("road_name")]
        all_road_names = list(set(list(plan.get("blocked_roads") or []) + blocked_roads + uc7_names))

        affected_zones = [z for z in plan["impact_zones"] if z["zone_id"] in affected_zone_ids]
        if not affected_zones:
            raise HTTPException(status_code=400, detail="None of the specified zones are in this plan.")

        traffic_snapshot   = await self.fetch_traffic_data()
        new_routes         = await self._compute_all_zone_routes(
            affected_zones, plan["shelters_with_capacity"], uc7_segments, traffic_snapshot)
        updated_routes     = {**dict(plan["best_routes_per_zone"] or {}), **new_routes}

        affected_users     = await self.db.get_users_in_zones(affected_zones)
        updates_sent       = await self.send_route_updates(
            affected_users, new_routes, plan["disaster_id"])

        await self.db.update_plan(
            plan_id,
            best_routes_per_zone=updated_routes,
            blocked_roads=all_road_names,
        )
        await self.display_evacuation_on_map(
            plan_id, plan["impact_zones"], updated_routes, plan["shelters_with_capacity"])

        logger.info(f"[UC8-Phase4-Blockage] zones={len(affected_zone_ids)}, updates={updates_sent}")
        return {
            "plan_id": plan_id, "plan_ref": plan["plan_ref"],
            "new_routes": new_routes, "total_blocked_roads": len(all_road_names),
            "zones_affected": len(affected_zone_ids), "route_updates_sent": updates_sent,
            "message": (
                f"Routes recomputed for {len(affected_zone_ids)} zone(s). "
                f"{updates_sent} residents notified."
            ),
        }

    async def handle_disaster_escalation(
        self, plan_id: str, new_zone_ids: List[str], reason: str
    ) -> Dict[str, Any]:
        plan = await self._get_plan_or_404(plan_id)
        if plan["plan_status"] not in ("ACTIVE", "MONITORING"):
            raise HTTPException(status_code=400, detail="Plan is not active.")

        new_zones  = [_ZONE_MAP[zid] for zid in new_zone_ids if zid in _ZONE_MAP]
        if not new_zones:
            raise HTTPException(status_code=400, detail="No valid zone IDs provided.")

        existing_ids = {z["zone_id"] for z in plan["impact_zones"]}
        truly_new    = [z for z in new_zones if z["zone_id"] not in existing_ids]
        if not truly_new:
            return {"plan_id": plan_id,
                    "message": "All specified zones are already included in this plan."}

        updated_zones    = list(plan["impact_zones"]) + truly_new
        uc7_segments     = await self.db.get_blocked_roads(plan["disaster_id"])
        traffic_snapshot = await self.fetch_traffic_data()
        new_routes       = await self._compute_all_zone_routes(
            truly_new, plan["shelters_with_capacity"], uc7_segments, traffic_snapshot)
        updated_routes   = {**dict(plan["best_routes_per_zone"] or {}), **new_routes}
        new_pop          = get_population_profile(updated_zones)
        new_transport    = compute_transport_needs(new_pop, updated_zones, updated_routes)
        new_alloc        = allocate_resources(new_transport)

        existing_metrics = dict(plan.get("completion_metrics") or {})
        for z in truly_new:
            existing_metrics[z["zone_id"]] = {
                "percentage": 0, "evacuated": 0,
                "remaining": z["population"], "status": "in_progress",
            }

        now  = datetime.utcnow()
        note = f" | Escalation [{now.strftime('%H:%M')}]: {reason}"
        await self.db.update_plan(
            plan_id,
            impact_zones=updated_zones,
            best_routes_per_zone=updated_routes,
            transport_plan=new_transport,
            allocations=new_alloc,
            completion_metrics=existing_metrics,
            notes=(plan.get("notes") or "") + note,
        )

        new_users   = await self.db.get_users_in_zones(truly_new)
        alerts_sent = await self.broadcast_alerts(
            new_users, plan["disaster_id"], plan_id,
            new_routes, plan["shelters_with_capacity"],
        )
        await self.display_evacuation_on_map(
            plan_id, updated_zones, updated_routes, plan["shelters_with_capacity"])

        logger.info(f"[UC8-Escalation] {len(truly_new)} zones added, alerts={alerts_sent}")
        return {
            "plan_id": plan_id, "plan_ref": plan["plan_ref"],
            "new_zones_added": len(truly_new), "total_zones": len(updated_zones),
            "new_population": sum(z["population"] for z in truly_new),
            "alerts_sent": alerts_sent, "reason": reason,
            "message": f"{len(truly_new)} new zone(s) added to the evacuation.",
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CRUD
    # ═════════════════════════════════════════════════════════════════════════

    async def get_plan(self, plan_id: str) -> Dict[str, Any]:
        return await self._get_plan_or_404(plan_id)

    async def list_plans(self, disaster_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self.db.list_plans(disaster_id=disaster_id)

    # ═════════════════════════════════════════════════════════════════════════
    # HELPERS
    # ═════════════════════════════════════════════════════════════════════════

    async def fetch_traffic_data(self) -> Dict[str, Any]:
        """Step 4: traffic via self.external (same path as UC7)."""
        try:
            return await self.external.fetch_traffic_data(EVACUATION_REGION)
        except Exception as exc:
            logger.warning(f"[UC8] Traffic fetch degraded: {exc}")
            return {"source": "fallback", "available": False, "segments": []}

    async def broadcast_alerts(
        self,
        users: List[Dict],
        disaster_id: str,
        plan_id: str,
        best_routes_per_zone: Dict,
        shelters: List[Dict],
    ) -> int:
        """
        Step 11-style: publish via self.publisher (evacuation.triggered).
        Falls back to direct Twilio if MQ disconnected.
        """
        try:
            if self.publisher.is_connected:
                all_routes = [r for zr in best_routes_per_zone.values() for r in (zr or [])]
                await self.publisher.publish_reroute_triggered(
                    disaster_id=disaster_id,
                    vehicles=users,
                    routes=all_routes,
                    route_assignments={},
                    trigger_source="evacuation",
                    vehicles_affected=len(users),
                )
                return len(users)
        except Exception as exc:
            logger.warning(f"[UC8] RabbitMQ publish failed, falling back to Twilio: {exc}")

        # Twilio fallback
        from app.services.twilio_service import send_sms
        sent = 0
        shelter_map = {s["shelter_id"]: s["name"] for s in shelters}
        user_routes: Dict[str, Dict] = {}
        for zone_id, routes in best_routes_per_zone.items():
            if routes:
                for u in users:
                    if u.get("zone_id") == zone_id:
                        user_routes[u["id"]] = routes[0]

        for u in users:
            try:
                route   = user_routes.get(u["id"], {})
                shelter = shelter_map.get(route.get("destination_shelter_id", ""), "nearest shelter")
                msg     = (
                    f"EVACUATION ALERT: Please evacuate immediately. "
                    f"Proceed to {shelter}. Call 999 for help."
                )
                if await send_sms(u["phone_number"], msg):
                    sent += 1
            except Exception:
                pass
        logger.info(f"[UC8] Twilio fallback: {sent}/{len(users)} sent")
        return sent

    async def display_evacuation_on_map(
        self,
        plan_id: str,
        zones: List[Dict],
        routes: Dict,
        shelters: List[Dict],
    ) -> bool:
        """Push overlay via self.mapping (Socket.IO reroute_alert). Same channel as UC7."""
        try:
            all_routes = [r for zone_routes in routes.values()
                          for r in (zone_routes if isinstance(zone_routes, list) else [])]
            await self.mapping.highlight_alternative_routes(
                routes=all_routes, region_id=EVACUATION_REGION)
            logger.info(f"[UC8] Socket.IO reroute_alert emitted ({len(all_routes)} routes)")
        except Exception as exc:
            logger.warning(f"[UC8] MappingService failed (non-fatal): {exc}")
        return True

    async def send_route_updates(
        self, users: List[Dict], new_routes: Dict, disaster_id: str
    ) -> int:
        """Phase 4 blockage: notify affected users via publisher (route.updated)."""
        try:
            if self.publisher.is_connected:
                all_routes = [r for zr in new_routes.values() for r in (zr or [])]
                await self.publisher.publish_route_updated(
                    disaster_id=disaster_id,
                    reason="route_blockage",
                    vehicles=users,
                    route_assignments={},
                    routes=all_routes,
                )
                return len(users)
        except Exception as exc:
            logger.warning(f"[UC8] route_updated publish failed: {exc}")

        from app.services.twilio_service import send_sms
        sent = 0
        for user in users:
            try:
                if await send_sms(user["phone_number"],
                                  "ROUTE UPDATE: Your evacuation route has changed. "
                                  "Proceed to the nearest shelter. Call 999 for help."):
                    sent += 1
            except Exception:
                pass
        return sent

    async def _compute_all_zone_routes(
        self,
        impact_zones: List[Dict],
        shelters: List[Dict],
        blocked_roads: List[Dict],
        traffic_snapshot: Dict,
    ) -> Dict[str, Any]:
        """PAR block: concurrent route computation for all zones."""
        results = await asyncio.gather(
            *[self._compute_zone_routes(z, shelters, blocked_roads, traffic_snapshot)
              for z in impact_zones],
            return_exceptions=True,
        )
        return {
            zone["zone_id"]: ([] if isinstance(r, Exception) else r)
            for zone, r in zip(impact_zones, results)
        }

    async def _compute_zone_routes(
        self,
        zone: Dict,
        shelters: List[Dict],
        blocked_roads: List[Dict],
        traffic_snapshot: Dict,
    ) -> List[Dict]:
        """
        LOOP: call self.external.get_directions() per shelter.
        Falls back to straight-line estimate when TomTom unavailable.
        """
        candidates = []
        for shelter in shelters:
            try:
                result = await self.external.get_directions(
                    origin={"lat": zone["lat"], "lng": zone["lon"]},
                    destination={"lat": shelter["lat"], "lng": shelter["lon"]},
                    avoid=blocked_roads,
                    alternatives=False,
                )
                routes = result.get("routes", [])
                if routes:
                    r = routes[0]
                    candidates.append({
                        "route_id":               str(uuid.uuid4()),
                        "origin_zone_id":         zone["zone_id"],
                        "zone_name":              zone["name"],
                        "destination_shelter_id": shelter["shelter_id"],
                        "shelter_name":           shelter["name"],
                        "shelter_capacity":       shelter["capacity"],
                        "distance_km":            round(r.get("length_meters", 0) / 1000, 2),
                        "estimated_time_min":     round(r.get("travel_time_seconds", 0) / 60, 1),
                        "travel_time_seconds":    r.get("travel_time_seconds", 0),
                        "length_meters":          r.get("length_meters", 0),
                        "traffic_delay_seconds":  r.get("traffic_delay_seconds", 0),
                        "points":                 r.get("points", []),
                        "geojson":                r.get("geojson"),
                        "waypoints":              r.get("waypoints", []),
                        "fallback":               False,
                    })
            except Exception:
                fb = straight_line_fallback(zone, shelter)
                if fb:
                    candidates.append(fb)

        return score_and_select_routes(candidates, traffic_snapshot)

    async def _get_plan_or_404(self, plan_id: str) -> Dict[str, Any]:
        plan = await self.db.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail=f"Evacuation plan '{plan_id}' not found.")
        return plan