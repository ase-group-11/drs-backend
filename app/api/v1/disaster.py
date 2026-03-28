# File: app/api/v1/disaster.py
"""
Disaster Management API

Handles the lifecycle of verified disasters — created by ERT after
approving a citizen report (UC2) or triggered by the evaluation service (UC5).

Auth rules:
  - GET endpoints (read-only)  → any logged-in user (get_current_user)
  - POST / state-change        → emergency team only (get_current_team_member)

RabbitMQ: disaster.resolved / disaster.updated published AFTER DB commit
via BackgroundTasks — never inside the service method.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.auth.dependencies import get_current_user, get_current_team_member
from app.services.disaster_service import DisasterService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/disasters", tags=["Disaster Management"])


# ─────────────────────────────────────────────────────────────────────────────
# RabbitMQ helper
# ─────────────────────────────────────────────────────────────────────────────

def _publish_disaster_event(topic: str, payload: dict) -> None:
    """
    Publish a single disaster event to RabbitMQ.
    Called as a BackgroundTask AFTER get_db() commits.
    """
    try:
        from app.services.rabbitmq_service import get_rabbitmq_service
        get_rabbitmq_service().publish(topic, payload)
    except Exception as exc:
        logger.error(f"disaster.py: RabbitMQ publish failed (non-fatal): {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas
# ─────────────────────────────────────────────────────────────────────────────

class ResolveDisasterRequest(BaseModel):
    """Body for POST /disasters/{id}/resolve"""
    resolution_notes: str = Field(..., min_length=5, description="Summary of how the disaster was resolved")


class EscalateDisasterRequest(BaseModel):
    """Body for POST /disasters/{id}/escalate"""
    new_severity: str   = Field(..., description="LOW | MEDIUM | HIGH | CRITICAL")
    reason:       Optional[str] = Field(None, description="Reason for escalation (shown in audit log)")


# ─────────────────────────────────────────────────────────────────────────────
# Static routes first
# /active and /all must come before /{disaster_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/active",
    summary="List ACTIVE disasters",
)
async def list_active_disasters(
    severity:     Optional[str] = None,
    disaster_type: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Returns all disasters with status=ACTIVE, newest first.
    Optionally filter by severity (CRITICAL | HIGH | MEDIUM | LOW)
    or disaster_type (FIRE | FLOOD | EARTHQUAKE etc.).
    Requires: emergency team Bearer token.
    """
    service = DisasterService(db)
    return await service.list_disasters(
        disaster_status="ACTIVE",
        severity=severity,
        disaster_type=disaster_type,
        limit=limit,
    )


@router.get(
    "/all",
    summary="List all disasters (any status)",
)
async def list_all_disasters(
    disaster_status: Optional[str] = None,
    severity:        Optional[str] = None,
    disaster_type:   Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Returns all disasters across all statuses, newest first.
    Useful for admin dashboards and reporting.
    Requires: emergency team Bearer token.
    """
    service = DisasterService(db)
    return await service.list_disasters(
        disaster_status=disaster_status,
        severity=severity,
        disaster_type=disaster_type,
        limit=limit,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic routes last ({disaster_id} path parameter)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{disaster_id}",
    summary="Get disaster details",
)
async def get_disaster(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Returns full disaster detail including PostGIS location, assigned team,
    report count, and metadata from the evaluation pipeline.
    Requires: any valid Bearer token.
    """
    service = DisasterService(db)
    return await service.get_disaster(disaster_id)


@router.get(
    "/{disaster_id}/photos",
    summary="Get photos submitted with disaster reports",
)
async def get_disaster_photos(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Returns all photos from disaster_reports linked to this disaster.
    URLs are refreshed (SAS tokens) before returning.
    Requires: any valid Bearer token.
    """
    service = DisasterService(db)
    photos  = await service.get_disaster_photos(disaster_id)
    return {"photos": photos, "count": len(photos)}


@router.get(
    "/{disaster_id}/deployments",
    summary="Get all unit deployments for a disaster",
)
async def get_disaster_deployments(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Returns all deployments for a disaster with unit details and status summary.
    Shows breakdown: how many units are DISPATCHED / EN_ROUTE / ON_SCENE / COMPLETED.
    Requires: emergency team Bearer token.
    """
    service = DisasterService(db)
    return await service.get_disaster_deployments(disaster_id)


@router.post(
    "/{disaster_id}/resolve",
    summary="Resolve a disaster (Admin)",
)
async def resolve_disaster(
    disaster_id: str,
    data: ResolveDisasterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Marks a disaster as RESOLVED — sets status, records resolution time,
    frees all deployed units back to AVAILABLE, and publishes
    disaster.resolved to RabbitMQ after DB commit.
    Requires: emergency team Bearer token.
    """
    service = DisasterService(db)
    result  = await service.resolve_disaster(
        disaster_id=disaster_id,
        resolution_notes=data.resolution_notes,
    )
    event = result.pop("_pending_event", None)
    if event:
        background_tasks.add_task(_publish_disaster_event, event[0], event[1])
    return result


@router.post(
    "/{disaster_id}/escalate",
    summary="Escalate disaster severity (Admin)",
)
async def escalate_disaster(
    disaster_id: str,
    data: EscalateDisasterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Upgrades the severity of an active disaster (e.g. HIGH → CRITICAL).
    Publishes disaster.updated to RabbitMQ after DB commit.
    Cannot escalate a RESOLVED disaster.
    Requires: emergency team Bearer token.
    """
    service = DisasterService(db)
    result  = await service.escalate_disaster(
        disaster_id=disaster_id,
        new_severity=data.new_severity,
        reason=data.reason,
    )
    event = result.pop("_pending_event", None)
    if event:
        background_tasks.add_task(_publish_disaster_event, event[0], event[1])
    return result