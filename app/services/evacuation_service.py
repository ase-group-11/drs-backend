# File: app/services/evacuation_service.py
"""
Evacuation Service — Use Case 8: Plan Evacuation (v2)

Constructor injection pattern — identical to RerouteService:
  self.db       → EvacuationRepository  (all DB access)
  self.external → IntegrationService    (TomTom routing + traffic)
  self.mapping  → MappingService        (Socket.IO map overlay)
  self.publisher→ ReroutePublisher      (RabbitMQ notifications)

Tests pass AsyncMock objects directly into __init__ — no patch() needed.

v2 changes:
  - Impact-area model replaces hardcoded zone model. Population and affected
    roads/facilities come from the evaluation metadata (UC5) stored on the
    disaster record — no more DUBLIN_ZONES with fake population numbers.
  - Transport allocation queries real emergency_units from the DB instead of
    using hardcoded BUS_CAPACITY / AMBULANCE_CAPACITY constants.
  - Route computation is scoped to the disaster centre → nearest shelters
    (not zones × all shelters), drastically reducing TomTom API calls.
  - Redis caching at the evacuation layer (300s TTL) prevents redundant
    TomTom calls across plan/blockage/escalation workflows.
  - Shelters remain hardcoded (stable infrastructure data).
"""

import asyncio
import hashlib
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

# Transport capacity by unit_type — how many evacuees each unit type can carry.
# Crew capacity (emergency_units.capacity) is for team members, not evacuees.
# These are standard emergency planning figures.
TRANSPORT_CAPACITY: Dict[str, int] = {
    "ambulance":       8,   # wheelchair van / accessible minibus
    "fire_engine":     0,   # fire trucks don't transport evacuees
    "patrol_car":      0,   # not for evacuation transport
    "rapid_response":  0,   # fast response, not transport
    "hazmat":          0,   # specialised, not for evacuation
    "rescue":          4,   # can carry limited rescued persons
    "command":         0,   # mobile command post
}

# Default vulnerable ratio when not derivable from facility data
DEFAULT_VULNERABLE_RATIO = 0.15

CONGESTION_WEIGHTS = {
    "light": 0.5, "moderate": 1.5, "heavy": 2.5, "severe": 4.0, "unknown": 1.0,
}

EVACUATION_REGION = "region-dublin-city"

# Route cache TTL (seconds) — evacuation routes are stable for longer
# than live traffic; 5 min avoids TomTom spam on blockage/escalation flows.
ROUTE_CACHE_TTL = 300

# Max shelters to route to from the disaster centre.
# Nearest N shelters are selected — avoids routing to distant ones.
MAX_SHELTERS_TO_ROUTE = 3

# ── Dublin evacuation shelters (stable infrastructure) ────────────────────────
DUBLIN_SHELTERS: List[Dict[str, Any]] = [
    {"shelter_id": "shelter_croke_park",    "name": "Croke Park",              "lat": 53.3608, "lon": -6.2510, "capacity": 15000},
    {"shelter_id": "shelter_aviva",         "name": "Aviva Stadium",           "lat": 53.3338, "lon": -6.2286, "capacity": 10000},
    {"shelter_id": "shelter_rds",           "name": "RDS Arena",               "lat": 53.3213, "lon": -6.2265, "capacity": 8000},
    {"shelter_id": "shelter_phoenix_park",  "name": "Phoenix Park Visitor Ctr", "lat": 53.3560, "lon": -6.3260, "capacity": 5000},
    {"shelter_id": "shelter_tallaght",      "name": "Tallaght Stadium",        "lat": 53.2876, "lon": -6.3740, "capacity": 7000},
    {"shelter_id": "shelter_malahide",      "name": "Malahide Castle Grounds", "lat": 53.4508, "lon": -6.1541, "capacity": 4000},
    {"shelter_id": "shelter_blanchardstown","name": "Blanchardstown Centre",    "lat": 53.3900, "lon": -6.3800, "capacity": 6000},
    {"shelter_id": "shelter_leopardstown",  "name": "Leopardstown Racecourse", "lat": 53.2800, "lon": -6.1800, "capacity": 5000},
]


# ── Module-level helpers ──────────────────────────────────────────────────────

def get_all_shelters() -> List[Dict]:
    return list(DUBLIN_SHELTERS)


def get_nearest_shelters(
    lat: float, lon: float, max_count: int = MAX_SHELTERS_TO_ROUTE
) -> List[Dict]:
    """Return the N nearest shelters to the disaster centre, sorted by distance."""
    shelters_with_dist = []
    for s in DUBLIN_SHELTERS:
        dist = math.sqrt((s["lat"] - lat) ** 2 + (s["lon"] - lon) ** 2) * 111  # km approx
        shelters_with_dist.append({**s, "_dist_km": dist})
    shelters_with_dist.sort(key=lambda s: s["_dist_km"])
    return shelters_with_dist[:max_count]


def build_impact_area(disaster: Dict) -> Dict[str, Any]:
    """
    Build an impact-area dict from the disaster record + evaluation metadata.

    Replaces the old get_zones_near_disaster() which returned hardcoded DUBLIN_ZONES.
    Population, affected roads, facilities all come from UC5's evaluation.
    """
    meta = (disaster.get("disaster_metadata") or {}).get("evaluation", {})

    estimated_population = meta.get("estimated_population") or disaster.get("people_affected", 0)
    if estimated_population <= 0:
        estimated_population = disaster.get("people_affected", 0) or 100

    # Derive vulnerable count from facility types or use default ratio
    affected_facilities = meta.get("affected_facilities") or []
    vulnerable_ratio = DEFAULT_VULNERABLE_RATIO
    # Boost ratio if schools or hospitals are in the impact area
    facility_text = " ".join(
        f.get("name", f) if isinstance(f, dict) else str(f)
        for f in affected_facilities
    ).lower()
    if any(kw in facility_text for kw in ("school", "hospital", "nursing", "creche", "montessori")):
        vulnerable_ratio = 0.25  # higher ratio near schools/care facilities

    vulnerable_count = max(1, int(estimated_population * vulnerable_ratio))

    # affected_roads may be list of strings or list of dicts
    affected_roads = meta.get("affected_roads") or []
    road_names = []
    for r in affected_roads:
        if isinstance(r, dict):
            road_names.append(r.get("road_name", ""))
        else:
            road_names.append(str(r))

    return {
        "disaster_id":         str(disaster["id"]),
        "area_name":           disaster.get("location_address") or "Disaster impact area",
        "center_lat":          disaster["lat"],
        "center_lon":          disaster["lon"],
        "radius_km":           meta.get("impact_radius_km") or 3.0,
        "population":          estimated_population,
        "vulnerable_count":    vulnerable_count,
        "affected_roads":      road_names,
        "affected_facilities": affected_facilities,
        "severity":            str(disaster.get("severity", "HIGH")).upper(),
    }


def get_population_profile(impact_area: Dict) -> Dict[str, Any]:
    """Build population stats from the impact area (replaces zone-sum approach)."""
    total = impact_area["population"]
    vuln  = impact_area["vulnerable_count"]
    return {
        "total":          total,
        "vulnerable":     vuln,
        "mobile":         total - vuln,
        "density_factor": round(vuln / total if total else 0.15, 2),
    }


def compute_transport_needs(
    population_stats: Dict,
    impact_area: Dict,
    best_routes: Dict,
    available_units: List[Dict],
) -> Dict[str, Any]:
    """
    Compute transport needs using real available units from the DB.

    available_units comes from EvacuationRepository.get_available_transport_units()
    and contains rows like {"unit_type": "ambulance", "available_count": 5, "capacity": 4}.
    """
    total = population_stats["total"]
    vuln  = population_stats["vulnerable"]

    # Build a capacity map from DB units
    unit_summary = {}
    for row in available_units:
        utype = str(row["unit_type"]).lower()
        transport_cap = TRANSPORT_CAPACITY.get(utype, 0)
        if transport_cap > 0:
            if utype not in unit_summary:
                unit_summary[utype] = {"available": 0, "transport_capacity": transport_cap}
            unit_summary[utype]["available"] += row["available_count"]

    # Calculate how many of each type we need
    ambulances_available = unit_summary.get("ambulance", {}).get("available", 0)
    ambulance_cap = unit_summary.get("ambulance", {}).get("transport_capacity", 8)
    rescue_available = unit_summary.get("rescue", {}).get("available", 0)
    rescue_cap = unit_summary.get("rescue", {}).get("transport_capacity", 4)

    ambulances_needed = max(1, math.ceil(vuln / ambulance_cap)) if ambulance_cap > 0 else 0
    rescue_needed = 0

    # If not enough ambulances, supplement with rescue units
    if ambulances_needed > ambulances_available and rescue_available > 0:
        shortfall = (ambulances_needed - ambulances_available) * ambulance_cap
        rescue_needed = min(rescue_available, math.ceil(shortfall / rescue_cap)) if rescue_cap > 0 else 0
        ambulances_needed = ambulances_available  # cap to what's available

    # Build route schedules
    schedules = []
    for route_key, routes in best_routes.items():
        if not routes:
            continue
        best = routes[0] if isinstance(routes, list) else routes
        schedules.append({
            "shelter_id":          best.get("destination_shelter_id", ""),
            "shelter_name":        best.get("shelter_name", ""),
            "route_id":            best.get("route_id", ""),
            "ambulances_needed":   ambulances_needed,
            "rescue_units_needed": rescue_needed,
            "estimated_time_min":  best.get("estimated_time_min", 30),
        })

    return {
        "total_ambulances":      ambulances_needed,
        "ambulances_available":  ambulances_available,
        "rescue_units_needed":   rescue_needed,
        "rescue_units_available": rescue_available,
        "total_people":          total,
        "total_vulnerable":      vuln,
        "unit_summary":          unit_summary,
        "schedules":             schedules,
    }


def allocate_resources(transport_plan: Dict) -> Dict[str, Any]:
    return {
        "ambulances_allocated":     transport_plan["total_ambulances"],
        "rescue_units_allocated":   transport_plan["rescue_units_needed"],
        "allocation_confirmed":     True,
        "allocated_at":             datetime.utcnow().isoformat(),
    }


def score_and_select_routes(
    candidates: List[Dict], traffic_snapshot: Dict
) -> List[Dict]:
    """score = distance×0.4 + congestion×0.4 + delay_min×0.2. Top 3 returned."""
    segments = traffic_snapshot.get("segments", [])
    cw       = avg_congestion_weight(segments)

    for c in candidates:
        dist_score = c.get("distance_km", 999)
        delay_min  = c.get("traffic_delay_seconds", 0) / 60
        c["score"] = round(dist_score * 0.4 + cw * 0.4 + delay_min * 0.2, 4)

    candidates.sort(key=lambda c: c["score"])
    return candidates[:3]


def avg_congestion_weight(segments: List[Dict]) -> float:
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


def straight_line_fallback(
    origin_lat: float, origin_lon: float,
    shelter: Dict, origin_label: str = "impact_area",
) -> Optional[Dict]:
    """Minimal route estimate when TomTom is unavailable."""
    try:
        dist = math.sqrt((origin_lat - shelter["lat"]) ** 2
                         + (origin_lon - shelter["lon"]) ** 2) * 111
        t_min = round(dist / 30 * 60, 1)
        return {
            "route_id":               str(uuid.uuid4()),
            "origin_label":           origin_label,
            "origin_lat":             origin_lat,
            "origin_lon":             origin_lon,
            "destination_shelter_id": shelter["shelter_id"],
            "shelter_name":           shelter["name"],
            "shelter_capacity":       shelter["capacity"],
            "distance_km":            round(dist, 2),
            "estimated_time_min":     t_min,
            "travel_time_seconds":    int(t_min * 60),
            "length_meters":          int(dist * 1000),
            "traffic_delay_seconds":  0,
            "points":    [[origin_lat, origin_lon], [shelter["lat"], shelter["lon"]]],
            "geojson":   {"type": "Feature",
                          "geometry": {"type": "LineString",
                                       "coordinates": [[origin_lon, origin_lat],
                                                       [shelter["lon"], shelter["lat"]]]},
                          "properties": {"fallback": True}},
            "waypoints": [{"lat": origin_lat, "lon": origin_lon},
                          {"lat": shelter["lat"], "lon": shelter["lon"]}],
            "fallback":  True,
            "score":     0.0,
        }
    except Exception:
        return None


def _route_cache_key(
    disaster_id: str, shelter_id: str, blocked_roads_hash: str
) -> str:
    """Stable Redis key for an evacuation route."""
    return f"evac:route:{disaster_id}:{shelter_id}:{blocked_roads_hash}"


def _hash_blocked_roads(blocked_roads: List[Dict]) -> str:
    """Short hash of blocked road names for cache keying."""
    names = sorted(s.get("road_name", "") for s in blocked_roads)
    return hashlib.md5("|".join(names).encode()).hexdigest()[:8]


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

        Steps:
          1. getDisaster + buildImpactArea  → evaluation metadata
          2. getPopulationProfile           → from impact area (real data)
          3. getBlockedRoads                → self.db (UC7's road_segments table)
          4. getTrafficConditions           → self.external (IntegrationService)
          5. getNearestShelters             → nearest N shelters to disaster
          6. getAvailableTransportUnits     → self.db (emergency_units table)
          7. computeRoutes                  → self.external.get_directions() per shelter
                                              with Redis caching (300s TTL)
          8. computeTransportNeeds +
             allocateResources              → uses real unit counts from DB
          9. saveEvacuationPlan             → self.db
        """
        logger.info(f"[UC8-Phase1] Planning evacuation for disaster {disaster_id}")

        # 1. Impact area from evaluation metadata
        disaster = await self.db.get_disaster(disaster_id)
        if not disaster:
            raise HTTPException(status_code=404, detail="Disaster not found.")

        impact_area = build_impact_area(disaster)
        if impact_area["population"] <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No affected population found for this disaster.",
            )

        # 2. Population profile
        population_stats = get_population_profile(impact_area)

        # 3. Blocked roads (UC7's table)
        blocked_roads = await self.db.get_blocked_roads(disaster_id)

        # 4. Traffic (via IntegrationService — circuit breaker + retry included)
        traffic_snapshot = await self.fetch_traffic_data()

        # 5. Nearest shelters (not all 8 — just the closest ones)
        shelters = get_nearest_shelters(
            impact_area["center_lat"], impact_area["center_lon"])

        # 6. Available transport units from DB
        available_units = await self.db.get_available_transport_units()

        # 7. Route computation with Redis caching
        best_routes = await self._compute_routes(
            impact_area, shelters, blocked_roads, traffic_snapshot)

        # 8. Transport + allocation using real unit counts
        transport_plan = compute_transport_needs(
            population_stats, impact_area, best_routes, available_units)
        allocations = allocate_resources(transport_plan)

        # 9. Persist
        plan_ref = await self.db.generate_plan_ref()
        plan_id  = await self.db.save_plan(
            disaster_id=disaster_id,
            plan_ref=plan_ref,
            impact_zones=[impact_area],           # wrap single area in list
            population_stats=population_stats,
            blocked_roads=blocked_roads,
            traffic_snapshot=traffic_snapshot,
            shelters_with_capacity=shelters,
            best_routes_per_zone=best_routes,     # rename to match repo
            transport_plan=transport_plan,
            allocations=allocations,
            auto_approved=auto_approve,
        )

        logger.info(f"[UC8-Phase1] Plan {plan_ref} saved (pop={impact_area['population']}, "
                     f"shelters={len(shelters)}, routes={len(best_routes)})")
        return {
            "plan_id": plan_id, "plan_ref": plan_ref, "disaster_id": disaster_id,
            "plan_status": "APPROVED" if auto_approve else "PENDING",
            "impact_area": {
                "center": f"{impact_area['center_lat']}, {impact_area['center_lon']}",
                "radius_km": impact_area["radius_km"],
                "affected_roads": impact_area["affected_roads"],
                "affected_facilities_count": len(impact_area["affected_facilities"]),
            },
            "shelters_count": len(shelters),
            "total_population_affected": population_stats["total"],
            "total_vulnerable": population_stats["vulnerable"],
            "transport_plan_summary": {
                "total_ambulances":       transport_plan["total_ambulances"],
                "ambulances_available":   transport_plan["ambulances_available"],
                "rescue_units_needed":    transport_plan["rescue_units_needed"],
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
          1. getUsersInImpactArea  → self.db
          2. broadcastAlerts       → self.publisher (RabbitMQ evacuation.triggered)
                                     + Twilio fallback
          3. displayEvacuation     → self.mapping (Socket.IO reroute_alert)
          4. dispatchResources     → allocation count
        """
        plan = await self._get_plan_or_404(plan_id)
        if plan["plan_status"] != "APPROVED":
            raise HTTPException(
                status_code=400,
                detail=f"Only APPROVED plans can be activated. Current: {plan['plan_status']}",
            )

        impact_zones = plan["impact_zones"]  # now contains [impact_area] as a list
        impact_area  = impact_zones[0] if impact_zones else {}
        users        = await self.db.get_users_in_impact_area(impact_area)

        alerts_sent  = await self.broadcast_alerts(
            users, plan["disaster_id"], plan_id,
            plan["best_routes_per_zone"], plan["shelters_with_capacity"],
        )
        map_updated  = await self.display_evacuation_on_map(
            plan_id, impact_zones,
            plan["best_routes_per_zone"], plan["shelters_with_capacity"],
        )
        units_en_route = (plan["allocations"].get("ambulances_allocated", 0)
                          + plan["allocations"].get("rescue_units_allocated", 0))

        initial_metrics = {
            "impact_area": {
                "percentage": 0, "evacuated": 0,
                "remaining": impact_area.get("population", 0),
                "status": "in_progress",
            }
        }
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
            "units_en_route": units_en_route,
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
        impact_area    = (plan.get("impact_zones") or [{}])[0] if plan.get("impact_zones") else {}
        total_pop      = impact_area.get("population", 0)

        # Sum evacuated from metrics (may have "impact_area" key or legacy zone keys)
        total_ev = sum(
            m.get("evacuated", 0) for m in metrics.values()
            if isinstance(m, dict)
        )
        overall = round(total_ev / total_pop * 100, 1) if total_pop else 0.0

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

        current = dict(plan.get("completion_metrics") or {})
        current.update(completion_metrics)
        all_done = all(isinstance(m, dict) and m.get("percentage", 0) >= 100
                       for m in current.values())
        new_status = "COMPLETED" if all_done else plan["plan_status"]

        update_fields: Dict[str, Any] = {
            "completion_metrics": current,
            "plan_status":        new_status,
        }
        if all_done:
            update_fields["completed_at"] = datetime.utcnow()

        await self.db.update_plan(plan_id, **update_fields)

        impact_area = (plan.get("impact_zones") or [{}])[0] if plan.get("impact_zones") else {}
        total_pop = impact_area.get("population", 0)
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
        self, plan_id: str, blocked_roads: List[str],
    ) -> Dict[str, Any]:
        """Re-compute routes when roads are newly blocked during an active evacuation."""
        plan = await self._get_plan_or_404(plan_id)
        if plan["plan_status"] not in ("ACTIVE", "MONITORING"):
            raise HTTPException(status_code=400, detail="Plan is not active.")

        uc7_segments   = await self.db.get_blocked_roads(plan["disaster_id"])
        uc7_names      = [s["road_name"] for s in uc7_segments if s.get("road_name")]
        all_road_names = list(set(list(plan.get("blocked_roads") or []) + blocked_roads + uc7_names))

        impact_area = (plan.get("impact_zones") or [{}])[0] if plan.get("impact_zones") else {}
        traffic_snapshot = await self.fetch_traffic_data()

        # Recompute routes — cache key changes because blocked_roads_hash changes
        new_routes    = await self._compute_routes(
            impact_area, plan["shelters_with_capacity"], uc7_segments, traffic_snapshot)
        updated_routes = {**dict(plan["best_routes_per_zone"] or {}), **new_routes}

        affected_users = await self.db.get_users_in_impact_area(impact_area)
        updates_sent   = await self.send_route_updates(
            affected_users, new_routes, plan["disaster_id"])

        await self.db.update_plan(
            plan_id,
            best_routes_per_zone=updated_routes,
            blocked_roads=all_road_names,
        )
        await self.display_evacuation_on_map(
            plan_id, plan["impact_zones"], updated_routes, plan["shelters_with_capacity"])

        logger.info(f"[UC8-Phase4-Blockage] updates={updates_sent}")
        return {
            "plan_id": plan_id, "plan_ref": plan["plan_ref"],
            "new_routes": new_routes, "total_blocked_roads": len(all_road_names),
            "route_updates_sent": updates_sent,
            "message": (
                f"Routes recomputed for impact area. "
                f"{updates_sent} residents notified."
            ),
        }

    async def handle_disaster_escalation(
        self, plan_id: str, increased_radius_km: Optional[float] = None,
        additional_roads: Optional[List[str]] = None, reason: str = "",
    ) -> Dict[str, Any]:
        """
        Escalation: increase the impact radius or add newly affected roads.
        Replaces the old zone-based escalation.
        """
        plan = await self._get_plan_or_404(plan_id)
        if plan["plan_status"] not in ("ACTIVE", "MONITORING"):
            raise HTTPException(status_code=400, detail="Plan is not active.")

        impact_area = (plan.get("impact_zones") or [{}])[0] if plan.get("impact_zones") else {}

        # Apply escalation
        if increased_radius_km and increased_radius_km > impact_area.get("radius_km", 0):
            impact_area["radius_km"] = increased_radius_km
        if additional_roads:
            existing_roads = set(impact_area.get("affected_roads", []))
            impact_area["affected_roads"] = list(existing_roads | set(additional_roads))

        # Re-fetch disaster to get updated population estimate for new radius
        disaster = await self.db.get_disaster(plan["disaster_id"])
        if disaster:
            meta = (disaster.get("disaster_metadata") or {}).get("evaluation", {})
            # If radius increased, scale population proportionally
            original_radius = meta.get("impact_radius_km") or 3.0
            new_radius = impact_area.get("radius_km", original_radius)
            if new_radius > original_radius:
                scale = (new_radius / original_radius) ** 2  # area scales with r²
                original_pop = meta.get("estimated_population") or impact_area.get("population", 0)
                impact_area["population"] = int(original_pop * scale)
                impact_area["vulnerable_count"] = max(
                    1, int(impact_area["population"] * DEFAULT_VULNERABLE_RATIO))

        # Recompute
        uc7_segments     = await self.db.get_blocked_roads(plan["disaster_id"])
        traffic_snapshot = await self.fetch_traffic_data()
        shelters = get_nearest_shelters(
            impact_area["center_lat"], impact_area["center_lon"],
            max_count=MAX_SHELTERS_TO_ROUTE + 1)  # +1 for escalation
        new_routes = await self._compute_routes(
            impact_area, shelters, uc7_segments, traffic_snapshot)
        updated_routes = {**dict(plan["best_routes_per_zone"] or {}), **new_routes}

        available_units = await self.db.get_available_transport_units()
        new_pop = get_population_profile(impact_area)
        new_transport = compute_transport_needs(new_pop, impact_area, updated_routes, available_units)
        new_alloc = allocate_resources(new_transport)

        existing_metrics = dict(plan.get("completion_metrics") or {})
        existing_metrics["impact_area"] = {
            "percentage": existing_metrics.get("impact_area", {}).get("percentage", 0),
            "evacuated": existing_metrics.get("impact_area", {}).get("evacuated", 0),
            "remaining": impact_area["population"],
            "status": "in_progress",
        }

        now  = datetime.utcnow()
        note = f" | Escalation [{now.strftime('%H:%M')}]: {reason}"
        await self.db.update_plan(
            plan_id,
            impact_zones=[impact_area],
            best_routes_per_zone=updated_routes,
            transport_plan=new_transport,
            allocations=new_alloc,
            completion_metrics=existing_metrics,
            notes=(plan.get("notes") or "") + note,
        )

        new_users   = await self.db.get_users_in_impact_area(impact_area)
        alerts_sent = await self.broadcast_alerts(
            new_users, plan["disaster_id"], plan_id,
            new_routes, plan["shelters_with_capacity"],
        )
        await self.display_evacuation_on_map(
            plan_id, [impact_area], updated_routes, plan["shelters_with_capacity"])

        logger.info(f"[UC8-Escalation] radius={impact_area.get('radius_km')}km, "
                     f"pop={impact_area['population']}, alerts={alerts_sent}")
        return {
            "plan_id": plan_id, "plan_ref": plan["plan_ref"],
            "updated_radius_km": impact_area.get("radius_km"),
            "updated_population": impact_area["population"],
            "alerts_sent": alerts_sent, "reason": reason,
            "message": f"Evacuation escalated. {alerts_sent} residents notified.",
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
        best_routes: Dict,
        shelters: List[Dict],
    ) -> int:
        """
        Publish via self.publisher (evacuation.triggered).
        Falls back to direct Twilio if MQ disconnected.
        """
        try:
            if self.publisher.is_connected:
                all_routes = [r for zr in best_routes.values()
                              for r in (zr if isinstance(zr, list) else [zr])]
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
        for u in users:
            try:
                msg = (
                    "EVACUATION ALERT: Please evacuate immediately. "
                    "Proceed to the nearest shelter. Call 999 for help."
                )
                if await send_sms(u["phone_number"], msg):
                    sent += 1
            except Exception:
                pass
        return sent

    async def display_evacuation_on_map(
        self,
        plan_id: str,
        impact_areas: List[Dict],
        routes: Dict,
        shelters: List[Dict],
    ) -> bool:
        """Push overlay via self.mapping (Socket.IO reroute_alert). Same channel as UC7."""
        try:
            all_routes = [r for zone_routes in routes.values()
                          for r in (zone_routes if isinstance(zone_routes, list) else [zone_routes])]
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
                all_routes = [r for zr in new_routes.values()
                              for r in (zr if isinstance(zr, list) else [zr])]
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

    async def _compute_routes(
        self,
        impact_area: Dict,
        shelters: List[Dict],
        blocked_roads: List[Dict],
        traffic_snapshot: Dict,
    ) -> Dict[str, Any]:
        """
        Compute routes from disaster centre to each shelter.

        Redis cache key: evac:route:{disaster_id}:{shelter_id}:{blocked_roads_hash}
        TTL: 300s (5 min).

        With MAX_SHELTERS_TO_ROUTE=3, this makes at most 3 TomTom calls
        per invocation (down from 64 in the old zone×shelter model).
        """
        disaster_id = impact_area.get("disaster_id", "unknown")
        roads_hash  = _hash_blocked_roads(blocked_roads)
        origin_lat  = impact_area["center_lat"]
        origin_lon  = impact_area["center_lon"]

        candidates = []
        for shelter in shelters:
            cache_key = _route_cache_key(disaster_id, shelter["shelter_id"], roads_hash)

            # Try Redis cache first
            cached = await self._cache_get(cache_key)
            if cached:
                candidates.extend(cached if isinstance(cached, list) else [cached])
                continue

            # Cache miss → call TomTom
            try:
                result = await self.external.get_directions(
                    origin={"lat": origin_lat, "lng": origin_lon},
                    destination={"lat": shelter["lat"], "lng": shelter["lon"]},
                    avoid=blocked_roads,
                    alternatives=False,
                )
                routes = result.get("routes", [])
                if routes:
                    r = routes[0]
                    route_data = {
                        "route_id":               str(uuid.uuid4()),
                        "origin_label":           impact_area.get("area_name", "impact_area"),
                        "origin_lat":             origin_lat,
                        "origin_lon":             origin_lon,
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
                    }
                    candidates.append(route_data)
                    # Cache the successful route
                    await self._cache_set(cache_key, route_data, ROUTE_CACHE_TTL)
                else:
                    fb = straight_line_fallback(origin_lat, origin_lon, shelter)
                    if fb:
                        candidates.append(fb)
            except Exception:
                fb = straight_line_fallback(origin_lat, origin_lon, shelter)
                if fb:
                    candidates.append(fb)

        scored = score_and_select_routes(candidates, traffic_snapshot)
        return {disaster_id: scored}

    # ── Redis helpers (reuse IntegrationService's pattern) ────────────────────

    async def _cache_get(self, key: str) -> Optional[Any]:
        """Read from Redis. Returns None on miss or error."""
        try:
            redis = await self._get_redis()
            if redis is None:
                return None
            raw = await redis.get(key)
            if raw:
                logger.debug(f"[UC8] Cache HIT: {key}")
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"[UC8] Cache GET failed for {key}: {e}")
        return None

    async def _cache_set(self, key: str, value: Any, ttl: int) -> None:
        """Write to Redis. Silently ignores errors."""
        try:
            redis = await self._get_redis()
            if redis is None:
                return
            await redis.setex(key, ttl, json.dumps(value))
            logger.debug(f"[UC8] Cache SET: {key} (TTL={ttl}s)")
        except Exception as e:
            logger.warning(f"[UC8] Cache SET failed for {key}: {e}")

    async def _get_redis(self):
        """Lazily initialise Redis — same pattern as IntegrationService."""
        if not hasattr(self, "_redis"):
            self._redis = None
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis
            from app.core.config import settings
            self._redis = await aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            return self._redis
        except Exception as e:
            logger.warning(f"[UC8] Redis unavailable — route caching disabled ({e})")
            return None

    async def _get_plan_or_404(self, plan_id: str) -> Dict[str, Any]:
        plan = await self.db.get_plan(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail=f"Evacuation plan '{plan_id}' not found.")
        return plan