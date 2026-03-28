# File: app/api/v1/reroute.py
"""
Re-Route Traffic API — UC7

Triggered automatically by the Disaster Evaluation Service, or manually
in development via POST /reroute/trigger.

Endpoints:
  POST /reroute/trigger    → run full reroute pipeline for a disaster
  POST /reroute/restore    → restore normal flow after clearance
  POST /reroute/override   → apply manual operator override
  GET  /reroute/status/{id}→ get active plan for a disaster
  GET  /reroute/plans      → get all active plans (admin dashboard)
  GET  /reroute/health     → TomTom / circuit breaker health check

Dependency factory (get_reroute_service) mirrors get_evacuation_service exactly —
constructor injection so all 4 dependencies are swappable AsyncMock in tests.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.enums import OverrideType, RoadSegmentStatus, OverridePriority
from app.repositories.reroute_repository import RerouteRepository
from app.providers.integration_service import IntegrationService, get_integration_service
from app.services.reroute_service import RerouteService
from app.services.instant_map_updates import MappingService
from app.workers.reroute_publisher import ReroutePublisher, get_publisher
from app.socket.manager import sio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reroute", tags=["Re-Route Traffic — UC7"])


# ─────────────────────────────────────────────────────────────────────────────
# Dependency factory
# ─────────────────────────────────────────────────────────────────────────────

def get_reroute_service(
    db: AsyncSession = Depends(get_db),
    external: IntegrationService = Depends(get_integration_service),
    publisher: ReroutePublisher = Depends(get_publisher),
) -> RerouteService:
    return RerouteService(
        db=RerouteRepository(db),
        external=external,
        mapping=MappingService(sio=sio),
        publisher=publisher,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas
# ─────────────────────────────────────────────────────────────────────────────

class RoadSegmentInput(BaseModel):
    """A single blocked road segment."""
    segment_id: str
    road_name:  Optional[str]  = None
    start_lat:  float
    start_lng:  float
    end_lat:    float
    end_lng:    float
    status:     Optional[str]  = "closed"
    reason:     Optional[str]  = "disaster"
    capacity:   Optional[int]  = 300

    @field_validator("status", mode="before")
    @classmethod
    def normalise_status(cls, v):
        if v is None:
            return "closed"
        from app.utils.enum_utils import normalize_enum_value
        return normalize_enum_value(RoadSegmentStatus, str(v))


class TriggerRerouteRequest(BaseModel):
    """Body for POST /reroute/trigger"""
    disaster_id:    str                          = Field(..., description="ID of the active disaster")
    affected_roads: Optional[List[RoadSegmentInput]] = Field(
        None, description="Blocked road segments. If omitted, fetched from DB by disaster_id."
    )


class RestoreFlowRequest(BaseModel):
    """Body for POST /reroute/restore"""
    disaster_id:       str                              = Field(..., description="ID of the disaster being cleared")
    cleared_segments:  Optional[List[RoadSegmentInput]] = Field(
        None, description="Segments that have been cleared. If omitted, fetched from DB."
    )


class OverrideRequest(BaseModel):
    """Body for POST /reroute/override"""
    disaster_id: str
    type:        str            = Field(..., description="close_lane | open_lane | pin_detour | corridor_priority (case-insensitive)")
    operator_id: str
    segment_id:  Optional[str] = None
    route_id:    Optional[str] = None
    priority:    Optional[str] = None

    @field_validator("type", mode="before")
    @classmethod
    def normalise_type(cls, v):
        from app.utils.enum_utils import normalize_enum_value
        return normalize_enum_value(OverrideType, str(v))

    @field_validator("priority", mode="before")
    @classmethod
    def normalise_priority(cls, v):
        if v is None:
            return v
        from app.utils.enum_utils import normalize_enum_value
        return normalize_enum_value(OverridePriority, str(v))


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/trigger",
    summary="Trigger reroute traffic pipeline",
    status_code=status.HTTP_200_OK,
)
async def trigger_reroute(
    request: TriggerRerouteRequest,
    service: RerouteService = Depends(get_reroute_service),
):
    """
    Runs the full reroute pipeline for a disaster:
      1. Fetch blocked roads (from request or DB)
      2. Get live traffic from TomTom
      3. Get affected vehicles in the area
      4. Calculate alternative routes (concurrent per vehicle group)
      5. Score and rank routes
      6. Assign vehicles to routes
      7. Push map overlay via Socket.IO
      8. Publish reroute.triggered to RabbitMQ
      9. Persist reroute plan

    Called by: Disaster Evaluation Service (production), or manually for dev/test.
    No auth required — called server-to-server.
    """
    affected_roads = [r.model_dump() for r in request.affected_roads] if request.affected_roads else None
    try:
        return await service.trigger_reroute_traffic(
            disaster_id=request.disaster_id,
            affected_roads=affected_roads,
        )
    except Exception as exc:
        logger.exception(f"trigger_reroute failed: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/restore",
    summary="Restore normal traffic flow after disaster clearance",
)
async def restore_flow(
    request: RestoreFlowRequest,
    service: RerouteService = Depends(get_reroute_service),
    db: AsyncSession = Depends(get_db),
):
    """
    Restores normal traffic flow when a disaster is resolved:
      - Sets all road segments for this disaster → open
      - Clears Socket.IO map overlays
      - Sends all-clear notification to affected vehicles
      - Marks reroute plan as cleared

    No auth required — called server-to-server or triggered by disaster resolution.
    """
    if request.cleared_segments:
        cleared_segments = [s.model_dump() for s in request.cleared_segments]
    else:
        repo = RerouteRepository(db)
        cleared_segments = await repo.get_blocked_roads(request.disaster_id)

    try:
        return await service.restore_normal_flow(
            disaster_id=request.disaster_id,
            cleared_segments=cleared_segments,
        )
    except Exception as exc:
        logger.exception(f"restore_flow failed: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/override",
    summary="Apply a manual operator traffic override",
)
async def apply_override(
    request: OverrideRequest,
    service: RerouteService = Depends(get_reroute_service),
):
    """
    Applies a manual operator override to the active reroute plan:
      - close_lane:          Force a segment closed
      - open_lane:           Force a segment open
      - pin_detour:          Lock a specific route as the preferred detour
      - corridor_priority:   Reserve a corridor for emergency vehicles only

    Recomputes affected routes and notifies vehicles of the change.
    No auth required — internal operator tool.
    """
    try:
        return await service.receive_override(request.model_dump())
    except Exception as exc:
        logger.exception(f"apply_override failed: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get(
    "/plans",
    summary="Get all active reroute plans (Admin dashboard)",
)
async def get_all_active_plans(
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all currently active reroute plans across all disasters.
    Used by the admin dashboard to get a system-wide traffic overview.
    No auth required — read-only status endpoint.
    """
    repo = RerouteRepository(db)
    return await repo.get_all_active_plans()


@router.get(
    "/status/{disaster_id}",
    summary="Get active reroute plan for a specific disaster",
)
async def get_reroute_status(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the currently active reroute plan for a disaster,
    including route assignments, affected vehicle count, and plan status.
    Returns 404 if no active plan exists for this disaster.
    No auth required — read-only status endpoint.
    """
    repo = RerouteRepository(db)
    plan = await repo.get_active_reroute_plan(disaster_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active reroute plan found for disaster_id={disaster_id}",
        )
    return plan


@router.get(
    "/health",
    summary="TomTom integration service health check",
)
async def reroute_health(
    external: IntegrationService = Depends(get_integration_service),
):
    """
    Returns the health status of the TomTom integration service,
    including circuit breaker state and API key configuration.
    Useful for monitoring and debugging TomTom connectivity issues.
    """
    return await external.health_check()