# File: app/api/v1/deployment.py
"""
Deployment API — UC6 (existing endpoints).

Auth rules:
  - POST /disasters/{id}/dispatch        → team member (admin)
  - POST /deployments/{id}/update-status → any logged-in user (responder)
  - GET  /deployments/*                  → any logged-in user

RabbitMQ events are published AFTER the DB transaction commits by using
FastAPI's BackgroundTasks — never inside the service method.

NOTE: The six new UC6 endpoints (suggested-units, GPS, route, recall) live
in deploy.py. This file owns dispatch + status-update only.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.auth.dependencies import get_current_user, get_current_team_member
from app.services.deployment_service import DeploymentService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Deployments — UC6"])


# ─────────────────────────────────────────────────────────────────────────────
# RabbitMQ helper
# ─────────────────────────────────────────────────────────────────────────────

def _publish_pending_events(events: list) -> None:
    """
    Publish (topic, payload) pairs to RabbitMQ.
    Called as a BackgroundTask AFTER get_db() commits — never inside the service.
    Failure is logged and swallowed so the HTTP response is never affected.
    """
    try:
        from app.services.rabbitmq_service import get_rabbitmq_service
        svc = get_rabbitmq_service()
        for topic, payload in events:
            svc.publish(topic, payload)
    except Exception as exc:
        logger.error(f"deployment.py: RabbitMQ publish failed (non-fatal): {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas
# ─────────────────────────────────────────────────────────────────────────────

class DispatchRequest(BaseModel):
    """Body for POST /disasters/{id}/dispatch"""
    unit_ids:              List[str]           = Field(..., min_items=1, description="UUIDs of units to dispatch")
    priority_level:        str                 = Field("STANDARD",        description="STANDARD | HIGH | CRITICAL")
    special_instructions:  Optional[str]       = Field(None,             description="Instructions shown to responder")


class UpdateStatusRequest(BaseModel):
    """Body for POST /deployments/{id}/update-status"""
    new_status:              str              = Field(..., description="EN_ROUTE | ON_SCENE | IN_PROGRESS | COMPLETED | CANCELLED")
    situation_report:        Optional[str]    = Field(None, description="Free-text field update from the scene")
    tags:                    Optional[List[str]] = Field(None)
    minor_injuries:          int              = Field(0,   ge=0)
    serious_injuries:        int              = Field(0,   ge=0)
    additional_resources:    Optional[List[str]] = Field(None, description="Additional resource types needed")
    location_verified:       bool             = Field(False, description="Responder confirms they are on scene")
    request_immediate_backup:bool             = Field(False, description="Triggers backup.requested RabbitMQ event")
    assessment_notes:        Optional[str]   = Field(None)


# ─────────────────────────────────────────────────────────────────────────────
# Static routes first (FastAPI matches top-to-bottom)
# /deployments/unit/{unit_id}/* must come before /deployments/{deployment_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/disasters/{disaster_id}/dispatch",
    summary="Dispatch units to a disaster (Admin)",
)
async def dispatch_units(
    disaster_id: str,
    data: DispatchRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Admin dispatches one or more units to an ACTIVE disaster.
    Each unit gets its own deployment record (status = DISPATCHED).
    Publishes disaster.dispatched to RabbitMQ after DB commit.
    Requires: emergency team Bearer token.
    """
    service = DeploymentService(db)
    result  = await service.dispatch_units(
        disaster_id=disaster_id,
        unit_ids=data.unit_ids,
        priority_level=data.priority_level,
        special_instructions=data.special_instructions,
    )
    # Pop pending event and publish AFTER get_db() commits via BackgroundTasks
    event = result.pop("_pending_event", None)
    if event:
        background_tasks.add_task(
            _publish_pending_events,
            [(event["topic"], event["payload"])],
        )
    return result


@router.get(
    "/deployments/unit/{unit_id}/active",
    summary="Active missions for a unit (Responder)",
)
async def get_active_missions(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Returns all non-completed deployments for a unit — the Responder's mission list.
    Sorted by priority (CRITICAL first), then dispatch time.
    Requires: any valid Bearer token.
    """
    service  = DeploymentService(db)
    missions = await service.get_active_missions(unit_id)
    return {"active_missions": missions, "count": len(missions)}


@router.get(
    "/deployments/unit/{unit_id}/completed",
    summary="Completed missions for a unit (Responder)",
)
async def get_completed_missions(
    unit_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Returns the most recent completed/cancelled deployments for a unit.
    Requires: any valid Bearer token.
    """
    service  = DeploymentService(db)
    missions = await service.get_completed_missions(unit_id, limit=limit)
    return {"completed_missions": missions, "count": len(missions)}


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic routes last ({deployment_id} must come after static paths above)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/deployments/{deployment_id}/update-status",
    summary="Update deployment status (Responder)",
)
async def update_deployment_status(
    deployment_id: str,
    data: UpdateStatusRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Responder updates their deployment through the lifecycle:
      DISPATCHED → EN_ROUTE → ON_SCENE → IN_PROGRESS → COMPLETED

    Optionally submits situation report, injury counts, and backup requests.
    Publishes status-change events to RabbitMQ after DB commit.
    Requires: any valid Bearer token.
    """
    service = DeploymentService(db)
    result  = await service.update_status(
        deployment_id=deployment_id,
        new_status=data.new_status,
        situation_report=data.situation_report,
        tags=data.tags,
        minor_injuries=data.minor_injuries,
        serious_injuries=data.serious_injuries,
        additional_resources=data.additional_resources,
        location_verified=data.location_verified,
        request_immediate_backup=data.request_immediate_backup,
        assessment_notes=data.assessment_notes,
    )
    pending_events = result.pop("_pending_events", [])
    if pending_events:
        background_tasks.add_task(_publish_pending_events, pending_events)
    return result


@router.get(
    "/deployments/{deployment_id}",
    summary="Get deployment details (Mission Progress)",
)
async def get_deployment(
    deployment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Returns full deployment detail including timeline, situation report,
    and injury counts. Used by the Mission Progress screen.
    Requires: any valid Bearer token.
    """
    service = DeploymentService(db)
    return await service.get_deployment(deployment_id)