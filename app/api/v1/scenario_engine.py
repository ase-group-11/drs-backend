"""
app/api/v1/scenario_engine.py

Disaster Scenario Engine — replaces the Disaster Evaluation Service in dev/test.

Provides admin endpoints to:
  - Create named disaster scenarios with pre-defined affected road segments
  - List available scenarios
  - Activate a scenario (fires triggerRerouteTraffic on the ReRoute Service)
  - Deactivate a scenario (fires restore_normal_flow)

Pre-built scenario templates:
  - m50_flooding:         M50 motorway flood, J6–J9 northbound blocked
  - city_center_fire:     City centre fire, O'Connell St + surrounding roads
  - port_tunnel_closure:  Port tunnel closure, East Link + North Wall affected
  - multi_incident:       Two simultaneous incidents (M50 + N11)

Section 7 (Disaster Simulation Strategy) — Layer 1.
"""

import logging
import uuid
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scenarios", tags=["Scenario Engine"])


# ---------------------------------------------------------------------------
# Pre-built scenario templates
# ---------------------------------------------------------------------------

SCENARIO_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "m50_flooding": {
        "name": "M50 Northbound Flooding (J6–J9)",
        "disaster_type": "FLOOD",
        "severity": "HIGH",
        "region_id": "region-dublin-m50",
        "impact_polygon": {
            "south_west": {"lat": 53.2950, "lng": -6.3700},
            "north_east": {"lat": 53.3300, "lng": -6.3400},
        },
        "affected_roads": [
            {
                "segment_id": "seg-m50-j6-j7-nb",
                "road_name": "M50 Northbound J6–J7",
                "start_lat": 53.3020, "start_lng": -6.3615,
                "end_lat": 53.3120, "end_lng": -6.3580,
                "reason": "flood", "capacity": 2400,
            },
            {
                "segment_id": "seg-m50-j7-j8-nb",
                "road_name": "M50 Northbound J7–J8",
                "start_lat": 53.3120, "start_lng": -6.3580,
                "end_lat": 53.3250, "end_lng": -6.3540,
                "reason": "flood", "capacity": 2400,
            },
            {
                "segment_id": "seg-m50-j8-j9-nb",
                "road_name": "M50 Northbound J8–J9",
                "start_lat": 53.3250, "start_lng": -6.3540,
                "end_lat": 53.3380, "end_lng": -6.3510,
                "reason": "flood", "capacity": 2400,
            },
        ],
    },
    "city_center_fire": {
        "name": "City Centre Fire — O'Connell Street",
        "disaster_type": "FIRE",
        "severity": "CRITICAL",
        "region_id": "region-dublin-city",
        "impact_polygon": {
            "south_west": {"lat": 53.3420, "lng": -6.2680},
            "north_east": {"lat": 53.3530, "lng": -6.2530},
        },
        "affected_roads": [
            {
                "segment_id": "seg-oconnell-nb",
                "road_name": "O'Connell Street Northbound",
                "start_lat": 53.3440, "start_lng": -6.2603,
                "end_lat": 53.3490, "end_lng": -6.2600,
                "reason": "fire", "capacity": 600,
            },
            {
                "segment_id": "seg-oconnell-sb",
                "road_name": "O'Connell Street Southbound",
                "start_lat": 53.3490, "start_lng": -6.2600,
                "end_lat": 53.3440, "end_lng": -6.2603,
                "reason": "fire", "capacity": 600,
            },
            {
                "segment_id": "seg-parnell-st",
                "road_name": "Parnell Street",
                "start_lat": 53.3512, "start_lng": -6.2650,
                "end_lat": 53.3512, "end_lng": -6.2540,
                "reason": "fire", "capacity": 400,
            },
        ],
    },
    "port_tunnel_closure": {
        "name": "Dublin Port Tunnel Closure",
        "disaster_type": "OTHER",
        "severity": "MEDIUM",
        "region_id": "region-dublin-port",
        "impact_polygon": {
            "south_west": {"lat": 53.3540, "lng": -6.2350},
            "north_east": {"lat": 53.3700, "lng": -6.2100},
        },
        "affected_roads": [
            {
                "segment_id": "seg-port-tunnel-nb",
                "road_name": "Dublin Port Tunnel Northbound",
                "start_lat": 53.3560, "start_lng": -6.2250,
                "end_lat": 53.3680, "end_lng": -6.2180,
                "reason": "closure", "capacity": 3600,
            },
            {
                "segment_id": "seg-port-tunnel-sb",
                "road_name": "Dublin Port Tunnel Southbound",
                "start_lat": 53.3680, "start_lng": -6.2180,
                "end_lat": 53.3560, "end_lng": -6.2250,
                "reason": "closure", "capacity": 3600,
            },
        ],
    },
    "multi_incident": {
        "name": "Multi-Incident: M50 Flood + N11 Accident",
        "disaster_type": "OTHER",
        "severity": "HIGH",
        "region_id": "region-dublin-south",
        "impact_polygon": {
            "south_west": {"lat": 53.2800, "lng": -6.3700},
            "north_east": {"lat": 53.3400, "lng": -6.1800},
        },
        "affected_roads": [
            {
                "segment_id": "seg-m50-j12-j13-nb",
                "road_name": "M50 Northbound J12–J13",
                "start_lat": 53.2980, "start_lng": -6.3200,
                "end_lat": 53.3050, "end_lng": -6.3150,
                "reason": "flood", "capacity": 2400,
            },
            {
                "segment_id": "seg-n11-stillorgan",
                "road_name": "N11 Stillorgan Road",
                "start_lat": 53.2950, "start_lng": -6.2080,
                "end_lat": 53.3020, "end_lng": -6.2050,
                "reason": "accident", "capacity": 1800,
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ScenarioActivateRequest(BaseModel):
    scenario_type: str = Field(
        ...,
        description="One of: m50_flooding, city_center_fire, port_tunnel_closure, multi_incident"
    )
    region_id: Optional[str] = Field(
        None,
        description="Override the default region_id for this scenario"
    )
    severity: Optional[str] = Field(
        None,
        description="Override severity: low | medium | high | critical"
    )
    custom_roads: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Provide custom road segments instead of the template defaults"
    )

    @field_validator("severity", mode="before")
    @classmethod
    def normalise_severity(cls, v):
        """Normalise severity to UPPERCASE to match DisasterSeverity enum."""
        if v is None:
            return v
        from app.utils.enum_utils import normalize_enum_value
        from app.db.models.enums import DisasterSeverity
        try:
            return normalize_enum_value(DisasterSeverity, str(v))
        except ValueError:
            valid = [m.value for m in DisasterSeverity]
            raise ValueError(f"Severity must be one of: {valid} (case-insensitive)")


class ScenarioResponse(BaseModel):
    disaster_id: str
    scenario_type: str
    name: str
    region_id: str
    severity: str
    affected_roads: List[Dict[str, Any]]
    status: str
    message: str


# ---------------------------------------------------------------------------
# In-memory active scenarios registry (dev/test only)
# Production would persist to DB via DisasterRepository
# ---------------------------------------------------------------------------

_active_scenarios: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "",
    summary="List available scenario templates",
    response_model=List[Dict[str, Any]],
)
async def list_scenarios():
    """Return all pre-built scenario templates."""
    return [
        {
            "scenario_type": key,
            "name": val["name"],
            "disaster_type": val["disaster_type"],
            "severity": val["severity"],
            "region_id": val["region_id"],
            "road_count": len(val["affected_roads"]),
        }
        for key, val in SCENARIO_TEMPLATES.items()
    ]


@router.get(
    "/active",
    summary="List currently active scenarios",
    response_model=List[Dict[str, Any]],
)
async def list_active_scenarios():
    """Return all scenarios that have been activated and not yet deactivated."""
    return list(_active_scenarios.values())


@router.post(
    "/activate",
    summary="Activate a disaster scenario",
    response_model=ScenarioResponse,
    status_code=status.HTTP_201_CREATED,
)
async def activate_scenario(
    request: ScenarioActivateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Activate a disaster scenario.

    This is the test/demo equivalent of the Disaster Evaluation Service
    calling triggerRerouteTraffic. It:
    1. Resolves the scenario template
    2. Generates a disaster_id
    3. Registers the scenario as active
    4. Returns the trigger payload that RerouteService.trigger_reroute_traffic expects

    In Phase 1, this endpoint will also call RerouteService directly.
    For Phase 0 it returns the resolved payload so the caller can verify it.
    """
    if request.scenario_type not in SCENARIO_TEMPLATES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Unknown scenario type '{request.scenario_type}'. "
                f"Available: {list(SCENARIO_TEMPLATES.keys())}"
            ),
        )

    template = SCENARIO_TEMPLATES[request.scenario_type].copy()

    # Apply overrides
    region_id = request.region_id or template["region_id"]
    severity = request.severity or template["severity"]
    affected_roads = request.custom_roads or template["affected_roads"]

    disaster_id = str(uuid.uuid4())

    scenario_record = {
        "disaster_id": disaster_id,
        "scenario_type": request.scenario_type,
        "name": template["name"],
        "region_id": region_id,
        "severity": severity,
        "disaster_type": template["disaster_type"],
        "affected_roads": affected_roads,
        "status": "ACTIVE",
        "activated_at": str(uuid.uuid4()),  # timestamp placeholder
    }

    _active_scenarios[disaster_id] = scenario_record

    # Insert minimal disaster record so road_segments FK constraint is satisfied
    await _create_disaster_record(db, disaster_id, template, severity, region_id)

    # Insert road segments into DB so trigger can fetch them without affected_roads
    await _save_road_segments(db, disaster_id, affected_roads)

    logger.info(
        f"Scenario activated: {request.scenario_type} "
        f"disaster_id={disaster_id} region={region_id}"
    )

    return ScenarioResponse(
        disaster_id=disaster_id,
        scenario_type=request.scenario_type,
        name=template["name"],
        region_id=region_id,
        severity=severity,
        affected_roads=affected_roads,
        status="active",
        message=(
            f"Scenario '{template['name']}' activated. "
            f"disaster_id={disaster_id}. "
            f"Call POST /api/v1/reroute/trigger with this disaster_id to start rerouting."
        ),
    )


@router.post(
    "/deactivate/{disaster_id}",
    summary="Deactivate an active scenario",
    response_model=Dict[str, Any],
)
async def deactivate_scenario(disaster_id: str):
    """
    Deactivate a running scenario (equivalent to roads being cleared).

    In Phase 6 this will also call RerouteService.restore_normal_flow.
    """
    if disaster_id not in _active_scenarios:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active scenario found with disaster_id={disaster_id}",
        )

    scenario = _active_scenarios.pop(disaster_id)
    scenario["status"] = "deactivated"

    logger.info(f"Scenario deactivated: disaster_id={disaster_id}")

    return {
        "disaster_id": disaster_id,
        "status": "deactivated",
        "message": f"Scenario '{scenario['name']}' deactivated.",
    }


@router.get(
    "/vehicles",
    summary="List all seeded vehicles",
    response_model=Dict[str, Any],
)
async def list_vehicles():
    """
    Return all vehicles currently in the simulator pool.
    Useful for tracking specific user_ids before triggering a reroute.
    """
    from app.services.user_simulator import user_simulator
    vehicles = user_simulator.get_all_users()
    return {
        "total": len(vehicles),
        "vehicles": vehicles,
    }


@router.get(
    "/{disaster_id}",
    summary="Get a specific active scenario by disaster ID",
    response_model=Dict[str, Any],
)
async def get_scenario(disaster_id: str):
    """Retrieve the details of an active scenario."""
    if disaster_id not in _active_scenarios:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active scenario found with disaster_id={disaster_id}",
        )
    return _active_scenarios[disaster_id]

# ---------------------------------------------------------------------------
# Internal helper — create minimal disaster record for FK satisfaction
# ---------------------------------------------------------------------------

async def _create_disaster_record(
    db: AsyncSession,
    disaster_id: str,
    template: Dict[str, Any],
    severity: str,
    region_id: str,
) -> None:
    """
    Insert a minimal disaster record so road_segments.disaster_id FK is satisfied.

    Uses raw SQL to avoid needing to import the full Disaster ORM model
    and all its dependencies. The record is minimal — just enough to pass
    the FK constraint. The real Disaster Evaluation Service would create
    a proper record in production.
    """

    # Map region_id to a Dublin centre point as default location
    REGION_POINTS = {
        "region-dublin-m50":    "POINT(-6.3615 53.3020)",
        "region-dublin-city":   "POINT(-6.2603 53.3498)",
        "region-dublin-port":   "POINT(-6.2250 53.3560)",
        "region-dublin-south":  "POINT(-6.3200 53.2980)",
    }
    point_wkt = REGION_POINTS.get(region_id, "POINT(-6.2603 53.3498)")

    # Map template disaster_type to DB enum value — must be uppercase to match DB enum
    disaster_type = template.get("disaster_type", "OTHER").upper()

    tracking_id = f"SCN-{disaster_id[:8].upper()}"

    try:
        await db.execute(
            text("""
                INSERT INTO disasters (
                    id, tracking_id, type, severity, disaster_status,
                    location, description, people_affected,
                    multiple_casualties, structural_damage, road_blocked,
                    created_at, updated_at
                ) VALUES (
                    :id, :tracking_id,
                    CAST(:type AS disaster_type),
                    CAST(:severity AS disaster_severity),
                    CAST(:status AS disaster_status),
                    ST_GeogFromText(:location), :description, :people_affected,
                    :multiple_casualties, :structural_damage, :road_blocked,
                    now(), now()
                )
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "id": disaster_id,
                "tracking_id": tracking_id,
                "type": disaster_type,
                "severity": severity.upper() if severity else "HIGH",
                "status": "ACTIVE",
                "location": point_wkt,
                "description": f"Scenario: {template.get('name', 'Test scenario')}",
                "people_affected": 0,
                "multiple_casualties": False,
                "structural_damage": False,
                "road_blocked": True,
            }
        )
        await db.commit()
        logger.info(f"_create_disaster_record: created disaster {disaster_id}")
    except Exception as e:
        await db.rollback()
        logger.warning(f"_create_disaster_record: failed to create record — {e}")
        # Don't raise — scenario can still proceed if disaster record already exists

@router.post(
    "/seed-vehicles",
    summary="Seed vehicle pool for testing",
    response_model=Dict[str, Any],
)
async def seed_vehicles(count: int = 200):
    """
    Register simulated vehicles in the UserSimulator for a region.

    Call this before triggering a reroute to get a non-empty
    vehicles_affected count. Vehicles are spread across Dublin bounds.

    Args:
        count: Number of vehicles to register (default 200)
    """
    from app.services.user_simulator import user_simulator
    user_simulator.bulk_register(count)
    summary = user_simulator.summary()
    return {
        "registered": summary["total_users"],
        "by_type": summary["by_type"],
        "message": f"Seeded {summary['total_users']} vehicles. Now trigger a reroute.",
    }


@router.post(
    "/reset-vehicles",
    summary="Reset vehicle pool",
    response_model=Dict[str, Any],
)
async def reset_vehicles():
    """Clear all simulated vehicles from the pool."""    
    from app.services.user_simulator import user_simulator
    user_simulator.reset()
    return {"status": "reset", "total": 0}

async def _save_road_segments(
    db: AsyncSession,
    disaster_id: str,
    road_segments: List[Dict[str, Any]],
) -> None:
    """
    Insert road segments into DB on scenario activation.
    This allows trigger_reroute_traffic to fetch them without needing affected_roads.
    """
    for road in road_segments:
        try:
            await db.execute(
                text("""
                    INSERT INTO road_segments (
                        id, segment_id, road_name,
                        start_lat, start_lng, end_lat, end_lng,
                        status, reason, disaster_id, capacity,
                        created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), :segment_id, :road_name,
                        :start_lat, :start_lng, :end_lat, :end_lng,
                        'closed', :reason, :disaster_id, :capacity,
                        now(), now()
                    )
                    ON CONFLICT (segment_id, disaster_id) DO UPDATE
                        SET status = 'closed', updated_at = now()
                """),
                {
                    "segment_id": road.get("segment_id"),
                    "road_name": road.get("road_name", ""),
                    "start_lat": road.get("start_lat", 0.0),
                    "start_lng": road.get("start_lng", 0.0),
                    "end_lat": road.get("end_lat", 0.0),
                    "end_lng": road.get("end_lng", 0.0),
                    "reason": road.get("reason", "disaster"),
                    "disaster_id": disaster_id,
                    "capacity": road.get("capacity", 300),
                }
            )
        except Exception as e:
            await db.rollback()
            logger.warning(f"_save_road_segments: failed for {road.get('segment_id')} — {e}")

    await db.commit()
    logger.info(f"_save_road_segments: saved {len(road_segments)} segments for disaster {disaster_id}")

@router.post(
    "/trigger-monitoring-cycle",
    summary="Manually trigger one monitoring cycle",
    response_model=Dict[str, Any],
)
async def trigger_monitoring_cycle(
    speed_threshold_kmh: float = 200.0,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually fire one monitoring cycle for all active reroute regions.

    Args:
        speed_threshold_kmh: Speed below which reactive congestion fires.
                             Default 200 = always triggers (any road speed).
                             Set to 50 for realistic threshold.

    Useful for testing without waiting for Celery beat (30s interval).
    """
    from app.workers.tasks import _active_reroute_regions
    from app.providers.integration_service import get_integration_service
    from app.services.predictive_congestion import dual_congestion_check
    from app.repositories.reroute_repository import RerouteRepository
    from app.services.instant_map_updates import MappingService
    from app.workers.reroute_publisher import get_publisher
    from app.services.reroute_service import RerouteService

    if not _active_reroute_regions:
        return {
            "status": "no_active_regions",
            "message": "No active reroute regions registered. Trigger a reroute first.",
        }

    external = get_integration_service()
    publisher = get_publisher()
    mapping = MappingService()
    repo = RerouteRepository(db)
    service = RerouteService(db=repo, external=external, mapping=mapping, publisher=publisher)

    results = []
    for disaster_id, region_data in _active_reroute_regions.items():
        region_id = region_data["region_id"]

        # Fetch live traffic
        try:
            traffic_data = await external.fetch_traffic_data(region_id)
            live_segments = traffic_data.get("segments", [])
        except Exception as e:
            live_segments = []

        # Run dual check with provided threshold
        check = dual_congestion_check(
            live_traffic_data=live_segments,
            route_plan=region_data.get("route_plan", {}),
            segment_capacities=region_data.get("segment_capacities", {}),
            congestion_speed_threshold_kmh=speed_threshold_kmh,
            predictive_threshold_pct=0.8,
        )

        recalculated = False
        if check["should_recalculate"]:
            result = await service.run_monitoring_cycle(region_id=region_id)
            recalculated = result.get("should_recalculate", False)

        results.append({
            "disaster_id": disaster_id,
            "region_id": region_id,
            "live_segments_fetched": len(live_segments),
            "should_recalculate": check["should_recalculate"],
            "triggered_by": check["triggered_by"],
            "reactive_segments": check["reactive_segments"],
            "predicted_breaches": len(check["predicted_breaches"]),
            "recalculated": recalculated,
        })

    return {
        "status": "ok",
        "speed_threshold_kmh": speed_threshold_kmh,
        "regions_checked": len(results),
        "results": results,
    }


@router.post(
    "/register-vehicle",
    summary="Register a specific test vehicle",
    response_model=Dict[str, Any],
)
async def register_vehicle(
    user_id: str,
    lat: float = 53.295,
    lng: float = -6.380,
    dest_lat: float = 53.390,
    dest_lng: float = -6.320,
    vehicle_type: str = "general",
):
    """
    Register a single vehicle with a known user_id and location.
    Use this instead of seed-vehicles when you want to track specific users.

    Args:
        user_id:      ID to assign (e.g. test-user-001)
        lat/lng:      Current location (default: south of M50)
        dest_lat/lng: Destination (default: north of M50)
        vehicle_type: general | public_transport | emergency
    """
    from app.services.user_simulator import user_simulator
    vehicle = user_simulator.register_user(
        user_id=user_id,
        lat=lat,
        lng=lng,
        dest_lat=dest_lat,
        dest_lng=dest_lng,
        vehicle_type=vehicle_type,
    )
    return {
        "registered": True,
        "vehicle": vehicle,
        "message": f"Vehicle {user_id} registered. Now trigger a reroute to see it assigned to a route.",
    }