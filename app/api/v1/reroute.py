"""
app/api/v1/reroute.py

ReRoute Traffic API.

Endpoints:
  POST /reroute/trigger        — trigger reroute pipeline (called by Disaster Evaluation Service or manually)
  POST /reroute/restore        — restore normal flow after clearance
  POST /reroute/override       — apply operator override
  GET  /reroute/status/{id}    — get active reroute plan for a disaster
  GET  /reroute/health         — integration service health check
"""

import logging
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
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

router = APIRouter(prefix="/reroute", tags=["Reroute Traffic"])


# ---------------------------------------------------------------------------
# Dependency — build RerouteService with all dependencies injected
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RoadSegmentInput(BaseModel):
    segment_id: str
    road_name: Optional[str] = None
    start_lat: float
    start_lng: float
    end_lat: float
    end_lng: float
    status: Optional[RoadSegmentStatus] = RoadSegmentStatus.CLOSED
    reason: Optional[str] = "disaster"
    capacity: Optional[int] = 300


class TriggerRerouteRequest(BaseModel):
    disaster_id: str = Field(..., description="ID of the active disaster")
    region_id: str = Field(..., description="Region identifier for traffic data")
    affected_roads: Optional[List[RoadSegmentInput]] = Field(
        None,
        description="Blocked road segments. If omitted, fetched from DB by disaster_id."
    )


class RestoreFlowRequest(BaseModel):
    disaster_id: str = Field(..., description="ID of the disaster being cleared")
    cleared_segments: List[RoadSegmentInput] = Field(
        ..., description="Road segments that have been cleared"
    )


class OverrideRequest(BaseModel):
    disaster_id: str
    type: OverrideType = Field(..., description="Type of operator override")
    operator_id: str
    segment_id: Optional[str] = None
    route_id: Optional[str] = None
    priority: Optional[OverridePriority] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/trigger",
    summary="Trigger reroute traffic pipeline",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def trigger_reroute(
    request: TriggerRerouteRequest,
    service: RerouteService = Depends(get_reroute_service),
):
    """
    Trigger the full reroute traffic pipeline for a disaster.

    Called by:
      - Disaster Evaluation Service (production)
      - Manual POST with mock data (development / testing)

    If affected_roads is provided, those segments are used directly.
    Otherwise, blocked roads are fetched from the DB by disaster_id.
    """
    affected_roads = None
    if request.affected_roads:
        affected_roads = [r.model_dump() for r in request.affected_roads]

    try:
        result = await service.trigger_reroute_traffic(
            disaster_id=request.disaster_id,
            region_id=request.region_id,
            affected_roads=affected_roads,
        )
        return result
    except Exception as e:
        logger.exception(f"trigger_reroute failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post(
    "/restore",
    summary="Restore normal traffic flow after disaster clearance",
    response_model=Dict[str, Any],
)
async def restore_flow(
    request: RestoreFlowRequest,
    service: RerouteService = Depends(get_reroute_service),
):
    """
    Restore normal traffic flow when a disaster is cleared.

    Updates road status → open, clears map overlays, sends all-clear to users.
    """
    cleared_segments = [s.model_dump() for s in request.cleared_segments]

    try:
        result = await service.restore_normal_flow(
            disaster_id=request.disaster_id,
            cleared_segments=cleared_segments,
        )
        return result
    except Exception as e:
        logger.exception(f"restore_flow failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post(
    "/override",
    summary="Apply operator traffic override",
    response_model=Dict[str, Any],
)
async def apply_override(
    request: OverrideRequest,
    service: RerouteService = Depends(get_reroute_service),
):
    """
    Apply a manual operator override to the active reroute plan.

    Override types:
      - close_lane:          Force a segment closed
      - open_lane:           Force a segment open
      - pin_detour:          Lock a specific route as preferred
      - corridor_priority:   Reserve corridor for emergency vehicles
    """
    override = request.model_dump()

    try:
        result = await service.receive_override(override)
        return result
    except Exception as e:
        logger.exception(f"apply_override failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get(
    "/status/{disaster_id}",
    summary="Get active reroute plan for a disaster",
    response_model=Dict[str, Any],
)
async def get_reroute_status(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return the currently active reroute plan for a disaster."""
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
    summary="Integration service health check",
    response_model=Dict[str, Any],
)
async def reroute_health(
    external: IntegrationService = Depends(get_integration_service),
):
    """Check TomTom integration service status and circuit breaker state."""
    return await external.health_check()