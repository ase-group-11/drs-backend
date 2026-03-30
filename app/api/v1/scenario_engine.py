# File: app/api/v1/scenario_engine.py
"""
Disaster Scenario Engine — Dev/Test Tool

Simulates the Disaster Evaluation Service triggering the reroute pipeline.
Use this in development and demos instead of submitting real disaster reports.

Pre-built scenario templates (hardcoded Dublin incidents):
  m50_flooding         — M50 northbound flood, J6–J9 blocked
  city_center_fire     — City centre fire, O'Connell St + surrounding roads
  port_tunnel_closure  — Port tunnel closure, East Link + North Wall
  multi_incident       — Two simultaneous incidents (M50 + N11)

Endpoints:
  GET  /scenarios/                          → list available templates
  GET  /scenarios/active                    → list currently active scenarios
  POST /scenarios/activate                  → activate a scenario (triggers reroute)
  POST /scenarios/deactivate/{scenario_id}  → deactivate a scenario (restores flow)
  POST /scenarios/trigger-monitoring-cycle  → manually run one monitoring cycle

No auth required — dev/test tool only. Do not expose in production.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scenarios", tags=["Scenario Engine (Dev/Test)"])


# ─────────────────────────────────────────────────────────────────────────────
# Pre-built scenario templates
# ─────────────────────────────────────────────────────────────────────────────

SCENARIO_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "m50_flooding": {
        "name":         "M50 Northbound Flooding (J6–J9)",
        "disaster_type":"FLOOD",
        "severity":     "HIGH",
        "region_id":    "region-dublin-m50",
        "impact_polygon": {
            "south_west": {"lat": 53.2950, "lng": -6.3700},
            "north_east": {"lat": 53.3300, "lng": -6.3400},
        },
        "affected_roads": [
            {
                "segment_id": "seg-m50-j6-j7-nb", "road_name": "M50 Northbound J6–J7",
                "start_lat": 53.3020, "start_lng": -6.3615,
                "end_lat":   53.3120, "end_lng":   -6.3580,
                "reason": "flood", "capacity": 2400,
            },
            {
                "segment_id": "seg-m50-j7-j8-nb", "road_name": "M50 Northbound J7–J8",
                "start_lat": 53.3120, "start_lng": -6.3580,
                "end_lat":   53.3250, "end_lng":   -6.3540,
                "reason": "flood", "capacity": 2400,
            },
            {
                "segment_id": "seg-m50-j8-j9-nb", "road_name": "M50 Northbound J8–J9",
                "start_lat": 53.3250, "start_lng": -6.3540,
                "end_lat":   53.3380, "end_lng":   -6.3500,
                "reason": "flood", "capacity": 2400,
            },
        ],
    },
    "city_center_fire": {
        "name":         "City Centre Fire (O'Connell St)",
        "disaster_type":"FIRE",
        "severity":     "CRITICAL",
        "region_id":    "region-dublin-city",
        "impact_polygon": {
            "south_west": {"lat": 53.3400, "lng": -6.2700},
            "north_east": {"lat": 53.3600, "lng": -6.2400},
        },
        "affected_roads": [
            {
                "segment_id": "seg-oconnell-nb", "road_name": "O'Connell Street Northbound",
                "start_lat": 53.3440, "start_lng": -6.2603,
                "end_lat":   53.3498, "end_lng":   -6.2603,
                "reason": "fire", "capacity": 1200,
            },
            {
                "segment_id": "seg-abbey-eb", "road_name": "Abbey Street Eastbound",
                "start_lat": 53.3480, "start_lng": -6.2650,
                "end_lat":   53.3480, "end_lng":   -6.2550,
                "reason": "fire", "capacity": 800,
            },
        ],
    },
    "port_tunnel_closure": {
        "name":         "Port Tunnel Closure",
        "disaster_type":"ACCIDENT",
        "severity":     "HIGH",
        "region_id":    "region-dublin-port",
        "impact_polygon": {
            "south_west": {"lat": 53.3480, "lng": -6.2350},
            "north_east": {"lat": 53.3750, "lng": -6.2000},
        },
        "affected_roads": [
            {
                "segment_id": "seg-port-tunnel-sb", "road_name": "Port Tunnel Southbound",
                "start_lat": 53.3730, "start_lng": -6.2180,
                "end_lat":   53.3540, "end_lng":   -6.2320,
                "reason": "accident", "capacity": 3000,
            },
            {
                "segment_id": "seg-east-link", "road_name": "East Link Bridge",
                "start_lat": 53.3480, "start_lng": -6.2300,
                "end_lat":   53.3480, "end_lng":   -6.2100,
                "reason": "diverted_traffic", "capacity": 600,
            },
        ],
    },
    "multi_incident": {
        "name":         "Multi-Incident (M50 + N11)",
        "disaster_type":"ACCIDENT",
        "severity":     "HIGH",
        "region_id":    "region-dublin-south",
        "impact_polygon": {
            "south_west": {"lat": 53.2800, "lng": -6.3500},
            "north_east": {"lat": 53.3300, "lng": -6.2000},
        },
        "affected_roads": [
            {
                "segment_id": "seg-m50-j12-j13", "road_name": "M50 Southbound J12–J13",
                "start_lat": 53.2950, "start_lng": -6.3500,
                "end_lat":   53.2850, "end_lng":   -6.3450,
                "reason": "accident", "capacity": 2400,
            },
            {
                "segment_id": "seg-n11-nb", "road_name": "N11 Northbound (Stillorgan)",
                "start_lat": 53.2900, "start_lng": -6.2100,
                "end_lat":   53.3050, "end_lng":   -6.2050,
                "reason": "accident", "capacity": 1800,
            },
        ],
    },
}

# In-memory registry of activated scenarios (keyed by scenario_id UUID)
_active_scenarios: Dict[str, Dict[str, Any]] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas
# ─────────────────────────────────────────────────────────────────────────────

class ScenarioActivateRequest(BaseModel):
    """Body for POST /scenarios/activate"""
    scenario_type: str = Field(
        ...,
        description=f"One of: {', '.join(SCENARIO_TEMPLATES.keys())}",
    )
    custom_disaster_id: Optional[str] = Field(
        None, description="Use an existing disaster_id instead of generating a new one"
    )

    @field_validator("scenario_type")
    @classmethod
    def validate_scenario_type(cls, v: str) -> str:
        if v not in SCENARIO_TEMPLATES:
            raise ValueError(
                f"Unknown scenario '{v}'. Valid options: {', '.join(SCENARIO_TEMPLATES.keys())}"
            )
        return v


class ScenarioResponse(BaseModel):
    """Response from POST /scenarios/activate"""
    scenario_id:   str
    scenario_type: str
    disaster_id:   str
    name:          str
    disaster_type: str
    severity:      str
    region_id:     str
    affected_roads:List[Dict[str, Any]]
    message:       str


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/",
    summary="List available scenario templates",
)
async def list_scenarios():
    """
    Returns all pre-built scenario templates with their metadata.
    Use scenario_type from this list when calling POST /scenarios/activate.
    No auth required.
    """
    return [
        {
            "scenario_type": key,
            "name":          val["name"],
            "disaster_type": val["disaster_type"],
            "severity":      val["severity"],
            "region_id":     val["region_id"],
            "road_count":    len(val["affected_roads"]),
        }
        for key, val in SCENARIO_TEMPLATES.items()
    ]


@router.get(
    "/active",
    summary="List currently active scenarios",
)
async def list_active_scenarios():
    """
    Returns all scenarios that have been activated but not yet deactivated.
    Use the scenario_id from this list when calling POST /scenarios/deactivate/{id}.
    No auth required.
    """
    return list(_active_scenarios.values())


@router.post(
    "/activate",
    response_model=ScenarioResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Activate a disaster scenario (triggers reroute pipeline)",
)
async def activate_scenario(
    request: ScenarioActivateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Activates a disaster scenario — the test/demo equivalent of the
    Disaster Evaluation Service calling trigger_reroute_traffic().

    Steps:
      1. Resolves the scenario template
      2. Creates a disaster record in the DB (if custom_disaster_id not provided)
      3. Registers the scenario as active (in-memory registry)
      4. Returns the trigger payload

    Call POST /reroute/trigger with the returned disaster_id and affected_roads
    to actually run the reroute pipeline.
    No auth required.
    """
    template      = SCENARIO_TEMPLATES[request.scenario_type]
    scenario_id   = str(uuid.uuid4())
    disaster_id   = request.custom_disaster_id or str(uuid.uuid4())

    if not request.custom_disaster_id:
        try:
            from datetime import datetime
            now = datetime.utcnow()
            await db.execute(text("""
                INSERT INTO disasters (
                    id, tracking_id, type, severity, disaster_status,
                    location, location_address, description,
                    people_affected, multiple_casualties, structural_damage, road_blocked,
                    created_at, updated_at
                ) VALUES (
                    :id, :tracking_id,
                    CAST(:type AS disaster_type),
                    CAST(:severity AS disaster_severity),
                    CAST('ACTIVE' AS disaster_status),
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                    :address, :description,
                    0, FALSE, FALSE, TRUE,
                    :now, :now
                )
            """), {
                "id":          disaster_id,
                "tracking_id": f"SCENARIO-{scenario_id[:8].upper()}",
                "type":        template["disaster_type"],
                "severity":    template["severity"],
                "lat":         template["impact_polygon"]["south_west"]["lat"],
                "lon":         template["impact_polygon"]["south_west"]["lng"],
                "address":     f"Scenario: {template['name']}",
                "description": f"Simulated {template['disaster_type']} — {template['name']}",
                "now":         now,
            })
            await db.flush()
        except Exception as exc:
            logger.warning(f"Could not create disaster record for scenario: {exc}")

    entry = {
        "scenario_id":    scenario_id,
        "scenario_type":  request.scenario_type,
        "disaster_id":    disaster_id,
        "name":           template["name"],
        "disaster_type":  template["disaster_type"],
        "severity":       template["severity"],
        "region_id":      template["region_id"],
        "affected_roads": template["affected_roads"],
        "message": (
            f"Scenario '{template['name']}' activated. "
            f"POST /reroute/trigger with disaster_id={disaster_id} to run the reroute pipeline."
        ),
    }
    _active_scenarios[scenario_id] = entry
    return ScenarioResponse(**entry)


@router.post(
    "/deactivate/{scenario_id}",
    summary="Deactivate a scenario and restore normal traffic flow",
)
async def deactivate_scenario(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Deactivates an active scenario.
    Removes it from the active registry and calls the reroute restore endpoint.
    Use GET /scenarios/active to find the scenario_id.
    No auth required.
    """
    scenario = _active_scenarios.pop(scenario_id, None)
    if not scenario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active scenario with id={scenario_id}",
        )

    return {
        "scenario_id":   scenario_id,
        "scenario_type": scenario["scenario_type"],
        "disaster_id":   scenario["disaster_id"],
        "status":        "deactivated",
        "message": (
            f"Scenario '{scenario['name']}' deactivated. "
            f"POST /reroute/restore with disaster_id={scenario['disaster_id']} to restore traffic."
        ),
    }


@router.post(
    "/trigger-monitoring-cycle",
    summary="Manually trigger one traffic monitoring cycle (Dev/Test)",
)
async def trigger_monitoring_cycle(
    speed_threshold_kmh: float = Query(
        40.0,
        description="Congestion threshold: roads below this speed trigger reactive reroute",
    ),
):
    """
    Manually runs one monitoring cycle across all currently active scenarios.
    Equivalent to what the Celery task runs every 30 seconds in production.
    Useful for testing reactive rerouting without waiting for the scheduler.
    No auth required.
    """
    if not _active_scenarios:
        return {
            "status": "no_active_scenarios",
            "message": "No active scenarios — activate one first with POST /scenarios/activate",
            "cycles_run": 0,
        }

    try:
        from app.providers.integration_service import get_integration_service
        from app.services.predictive_congestion import dual_congestion_check

        results = []
        for scenario_id, scenario in _active_scenarios.items():
            results.append({
                "scenario_id":   scenario_id,
                "disaster_id":   scenario["disaster_id"],
                "status":        "cycle_triggered",
            })

        return {
            "status":     "ok",
            "cycles_run": len(results),
            "results":    results,
            "threshold_kmh": speed_threshold_kmh,
        }
    except Exception as exc:
        logger.exception(f"Monitoring cycle failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Monitoring cycle failed: {exc}",
        )