# File: app/api/v1/deploy.py
"""
Deploy Services API — UC6 (new endpoints)

Complements deployment.py without modifying it.
deployment.py owns: dispatch, update-status, view missions.
This file owns: suggested units, GPS tracking, route calculation, recall.

Auth rules:
  get_current_team_member → suggested-units, recall (admin decision)
  get_current_user        → GPS update, unit positions, route (responder/map)

Route ordering: static paths (/disasters/*, /routes/*) BEFORE dynamic
paths (/deployments/{id}/*) so FastAPI doesn't greedily match wrong routes.

RabbitMQ: disaster.unit_recalled published via BackgroundTasks after DB commit.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_team_member, get_current_user
from app.db.session import get_db
from app.services.deploy_service import DeployService
from app.services.route_service import RouteService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Deploy Services — UC6"])


# ─────────────────────────────────────────────────────────────────────────────
# RabbitMQ helper
# ─────────────────────────────────────────────────────────────────────────────

def _publish(events: list) -> None:
    """
    Publish (topic, payload) pairs to RabbitMQ.
    Called via BackgroundTasks AFTER get_db() commits — never inside the service.
    Failure is logged and swallowed so the HTTP response is never affected.
    """
    try:
        from app.services.rabbitmq_service import get_rabbitmq_service
        svc = get_rabbitmq_service()
        for topic, payload in events:
            svc.publish(topic, payload)
    except Exception as exc:
        logger.error(f"deploy.py: RabbitMQ publish failed (non-fatal): {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas
# ─────────────────────────────────────────────────────────────────────────────

class GpsUpdateRequest(BaseModel):
    """Body for POST /deployments/{id}/location"""
    latitude:  float         = Field(..., ge=-90,  le=90,  description="Current latitude (WGS-84)")
    longitude: float         = Field(..., ge=-180, le=180, description="Current longitude (WGS-84)")
    heading:   Optional[float] = Field(None, ge=0,  le=360, description="Direction of travel in degrees (0 = North)")
    speed_kmh: Optional[float] = Field(None, ge=0,          description="Current speed in km/h")


class RouteCalculateRequest(BaseModel):
    """Body for POST /routes/calculate"""
    origin_lat: float = Field(..., ge=-90,  le=90,  description="Origin latitude")
    origin_lon: float = Field(..., ge=-180, le=180, description="Origin longitude")
    dest_lat:   float = Field(..., ge=-90,  le=90,  description="Destination latitude")
    dest_lon:   float = Field(..., ge=-180, le=180, description="Destination longitude")


class RecallRequest(BaseModel):
    """Body for POST /deployments/{id}/recall"""
    reason: str = Field(..., min_length=3, description="Reason for recalling the unit (recorded in audit log)")


# ─────────────────────────────────────────────────────────────────────────────
# Static routes first
# /disasters/* and /routes/* must come before /deployments/{id}/*
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/disasters/{disaster_id}/suggested-units",
    summary="Suggest unit types + check availability for a disaster",
)
async def get_suggested_units(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Recommends which unit types to dispatch based on disaster type × severity,
    then cross-references against AVAILABLE unit counts in the DB.

    Logic:
      1. Look up (disaster_type, severity) in SERVICE_MAP
      2. Adjust for flags: multiple_casualties → +AMBULANCE, road_blocked → +PATROL_CAR
      3. Query AVAILABLE unit counts per type from emergency_units
      4. Flag shortages — suggest mutual aid if required_count > available_count

    Requires: emergency team Bearer token.
    """
    service = DeployService(db)
    return await service.get_suggested_units(disaster_id)


@router.get(
    "/disasters/{disaster_id}/unit-positions",
    summary="Get live GPS positions of all deployed units (admin map polling)",
)
async def get_unit_positions(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Returns the current position of every non-completed unit for a disaster.
    Falls back to the unit's station location when no GPS ping has been received.
    Includes ETA estimate (haversine + reported speed) for DISPATCHED/EN_ROUTE units.

    Poll this every 10 seconds from the admin map to move unit icons.
    Requires: any valid Bearer token.
    """
    service = DeployService(db)
    return await service.get_unit_positions(disaster_id)


@router.post(
    "/routes/calculate",
    summary="Calculate a driving route between two arbitrary points",
)
async def calculate_route(
    data: RouteCalculateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    General-purpose A → B route calculation via TomTom (IntegrationService).
    Falls back to haversine × 1.4 road factor at 40 km/h if TomTom is unavailable.
    Useful when the admin draws a custom route on the map without a deployment.
    Returns: source, distance_km, duration_minutes, polyline, geojson.
    Requires: any valid Bearer token.
    """
    service = RouteService(db)
    return await service.calculate_route(
        origin_lat=data.origin_lat,
        origin_lon=data.origin_lon,
        dest_lat=data.dest_lat,
        dest_lon=data.dest_lon,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic routes last ({deployment_id} path parameter)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/deployments/{deployment_id}/route",
    summary="Get the driving route for a specific deployment",
)
async def get_deployment_route(
    deployment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Calculates the driving route from the unit's current GPS position
    (or station if no GPS ping yet) to the disaster location.
    Uses TomTom via IntegrationService with haversine fallback.
    Returns: source, distance_km, duration_minutes, polyline, geojson.
    Requires: any valid Bearer token.
    """
    service = RouteService(db)
    return await service.get_deployment_route(deployment_id)


@router.post(
    "/deployments/{deployment_id}/location",
    summary="Push a GPS position update from the responder's phone",
)
async def update_gps_location(
    deployment_id: str,
    data: GpsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Stores the responder's current position in the deployments table.
    Called automatically by the mobile app every ~10 seconds while driving.
    Only accepted for active (non-COMPLETED, non-CANCELLED) deployments.
    After a successful ping, GET /unit-positions will show is_gps=true for this unit.
    Requires: any valid Bearer token.
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
)
async def recall_unit(
    deployment_id: str,
    data: RecallRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Recalls a unit — sets deployment → CANCELLED, unit → AVAILABLE.
    Writes an audit log entry and publishes disaster.unit_recalled to
    RabbitMQ via BackgroundTasks (after DB commit).
    Returns 400 if the deployment is already COMPLETED or CANCELLED.
    Requires: emergency team Bearer token.
    """
    service = DeployService(db)
    result  = await service.recall_unit(
        deployment_id=deployment_id,
        reason=data.reason,
    )
    event = result.pop("_pending_event", None)
    if event:
        background_tasks.add_task(_publish, [(event["topic"], event["payload"])])
    return result