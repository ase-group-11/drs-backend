"""
app/api/v1/deploy.py

UC6 Deploy Services — New API endpoints.

DO NOT modify deployment.py or emergency_unit.py — those are complete and tested.
This file adds only the missing pieces described in the handoff document.

Endpoints:
  GET  /disasters/{disaster_id}/suggested-units     ← recommend unit types + check shortages
  GET  /disasters/{disaster_id}/unit-positions      ← all unit positions for admin map
  POST /routes/calculate                            ← general A→B route (no deployment needed)
  GET  /deployments/{deployment_id}/route           ← driving route for a specific deployment
  POST /deployments/{deployment_id}/location        ← GPS update from responder's phone
  POST /deployments/{deployment_id}/recall          ← admin recalls a unit

Auth rules (same as deployment.py):
  get_current_team_member  → admin-only: suggested-units, recall
  get_current_user         → any logged-in user: GPS update, positions, route

Route ordering: static paths (/disasters, /routes) BEFORE dynamic paths (/deployments/{id}/*)
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.auth.dependencies import get_current_user, get_current_team_member
from app.services.deploy_service import DeployService
from app.services.route_service import RouteService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Deploy Services"])


# ─────────────────────────────────────────────────────────────────────────────
# RabbitMQ helper — same _publish_pending_events pattern as deployment.py
# ─────────────────────────────────────────────────────────────────────────────

def _publish(events: list) -> None:
    """
    Publish a list of (topic, payload) tuples to RabbitMQ.
    Called via BackgroundTasks AFTER the DB transaction has committed.
    Failure is logged but never propagates — the HTTP response has already sent.
    """
    try:
        from app.services.rabbitmq_service import get_rabbitmq_service
        svc = get_rabbitmq_service()
        for topic, payload in events:
            svc.publish(topic, payload)
    except Exception as exc:
        logger.error(f"deploy.py: RabbitMQ publish failed (non-fatal): {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas (inline — same style as deployment.py)
# ─────────────────────────────────────────────────────────────────────────────

class GpsUpdateRequest(BaseModel):
    """Body for POST /deployments/{id}/location"""
    latitude:  float = Field(..., ge=-90,  le=90,  description="Current latitude (WGS-84)")
    longitude: float = Field(..., ge=-180, le=180, description="Current longitude (WGS-84)")
    heading:   Optional[float] = Field(None, ge=0, le=360, description="Direction of travel in degrees (0=North)")
    speed_kmh: Optional[float] = Field(None, ge=0,         description="Current speed in km/h")


class RouteCalculateRequest(BaseModel):
    """Body for POST /routes/calculate"""
    origin_lat: float = Field(..., ge=-90,  le=90,  description="Origin latitude")
    origin_lon: float = Field(..., ge=-180, le=180, description="Origin longitude")
    dest_lat:   float = Field(..., ge=-90,  le=90,  description="Destination latitude")
    dest_lon:   float = Field(..., ge=-180, le=180, description="Destination longitude")


class RecallRequest(BaseModel):
    """Body for POST /deployments/{id}/recall"""
    reason: str = Field(..., min_length=3, description="Reason for recalling the unit")


# ─────────────────────────────────────────────────────────────────────────────
# STATIC routes first (FastAPI matches top-to-bottom; /disasters and /routes
# must come before /deployments/{id}/route etc.)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/disasters/{disaster_id}/suggested-units",
    summary="Get suggested unit types for a disaster",
    response_model=Dict[str, Any],
)
async def get_suggested_units(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Returns recommended unit types and counts based on disaster type + severity.

    Logic:
      1. Look up disaster type + severity in SERVICE_MAP
      2. Adjust for disaster flags (multiple_casualties → +ambulance, road_blocked → +patrol)
      3. Cross-reference with AVAILABLE units in the DB
      4. Flag shortages and suggest mutual aid if needed

    Requires: emergency team Bearer token (admin-only)
    """
    service = DeployService(db)
    return await service.get_suggested_units(disaster_id)


@router.get(
    "/disasters/{disaster_id}/unit-positions",
    summary="Get live positions of all deployed units for a disaster (map polling)",
    response_model=Dict[str, Any],
)
async def get_unit_positions(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Returns current GPS positions of all non-completed units for a disaster.

    - If GPS has been received from the unit: returns live position
    - Otherwise: falls back to the unit's station location
    - Includes ETA estimate for DISPATCHED/EN_ROUTE units

    Designed for polling every ~10 seconds from the admin map.
    Requires: any valid Bearer token
    """
    service = DeployService(db)
    return await service.get_unit_positions(disaster_id)


@router.post(
    "/routes/calculate",
    summary="Calculate a driving route between two points",
    response_model=Dict[str, Any],
)
async def calculate_route(
    data: RouteCalculateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    General-purpose A → B route calculation.

    Uses TomTom via IntegrationService (circuit breaker + retry + caching).
    Falls back to haversine × 1.4 road factor at 40 km/h if TomTom is unavailable.

    Returns: distance_km, duration_minutes, polyline, geojson
    Requires: any valid Bearer token
    """
    service = RouteService(db)
    return await service.calculate_route(
        origin_lat=data.origin_lat,
        origin_lon=data.origin_lon,
        dest_lat=data.dest_lat,
        dest_lon=data.dest_lon,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DYNAMIC routes last ({deployment_id} is a path parameter)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/deployments/{deployment_id}/route",
    summary="Get the driving route for a specific deployment",
    response_model=Dict[str, Any],
)
async def get_deployment_route(
    deployment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Calculates the driving route from the unit's current position (or station)
    to the disaster location.

    Uses TomTom via IntegrationService with haversine fallback.
    Requires: any valid Bearer token
    """
    service = RouteService(db)
    return await service.get_deployment_route(deployment_id)


@router.post(
    "/deployments/{deployment_id}/location",
    summary="Update GPS position for a deployed unit",
    response_model=Dict[str, Any],
)
async def update_gps_location(
    deployment_id: str,
    data: GpsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Called by the responder's mobile app every ~10 seconds while driving.
    Stores current lat/lon, heading, and speed in the deployments table.
    Only works for active (non-completed, non-cancelled) deployments.
    Requires: any valid Bearer token
    """
    service = DeployService(db)
    return await service.update_gps_location(
        deployment_id=deployment_id,
        latitude=data.latitude,
        longitude=data.longitude,
        heading=data.heading,
        speed_kmh=data.speed_kmh,
    )


@router.post(
    "/deployments/{deployment_id}/recall",
    summary="Recall a deployed unit back to base",
    response_model=Dict[str, Any],
)
async def recall_unit(
    deployment_id: str,
    data: RecallRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Admin recalls a unit — sets deployment to CANCELLED, unit to AVAILABLE.
    Publishes disaster.unit_recalled to RabbitMQ after DB commit (BackgroundTasks).
    Requires: emergency team Bearer token (admin-only)
    """
    service = DeployService(db)
    result  = await service.recall_unit(deployment_id=deployment_id, reason=data.reason)

    # _pending_event pattern: publish AFTER get_db() commits
    event = result.pop("_pending_event", None)
    if event:
        background_tasks.add_task(_publish, [(event["topic"], event["payload"])])

    return result