# # File: app/api/v1/deployment.py
# """
# Deployment API — with Bearer token auth.

# - Dispatch: requires emergency team token (admin)
# - Status updates: requires any token (responder)
# - View missions: requires any token
# """

# from fastapi import APIRouter, Depends, HTTPException, status
# from sqlalchemy.ext.asyncio import AsyncSession
# from pydantic import BaseModel, Field
# from typing import Optional, List, Dict, Any

# from app.db.session import get_db
# from app.auth.dependencies import get_current_user, get_current_team_member
# from app.services.deployment_service import DeploymentService

# router = APIRouter(tags=["Deployments"])


# # ── Request Models ──

# class DispatchRequest(BaseModel):
#     unit_ids: List[str] = Field(...)
#     priority_level: str = Field("STANDARD")
#     special_instructions: Optional[str] = None


# class UpdateStatusRequest(BaseModel):
#     new_status: str = Field(...)
#     situation_report: Optional[str] = None
#     tags: Optional[List[str]] = None
#     minor_injuries: int = Field(0)
#     serious_injuries: int = Field(0)
#     additional_resources: Optional[List[str]] = None
#     location_verified: bool = Field(False)
#     request_immediate_backup: bool = Field(False)
#     assessment_notes: Optional[str] = None


# # ══════════════════════════════════════════════
# # ADMIN: Dispatch units (requires team token)
# # ══════════════════════════════════════════════

# @router.post("/disasters/{disaster_id}/dispatch", summary="Dispatch units (Admin)")
# async def dispatch_units(
#     disaster_id: str,
#     data: DispatchRequest,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_team_member),
# ):
#     """Admin dispatches units. Requires emergency team Bearer token."""
#     if not data.unit_ids:
#         raise HTTPException(status_code=400, detail="unit_ids cannot be empty.")
#     service = DeploymentService(db)
#     return await service.dispatch_units(
#         disaster_id=disaster_id, unit_ids=data.unit_ids,
#         priority_level=data.priority_level, special_instructions=data.special_instructions,
#     )


# # ══════════════════════════════════════════════
# # STATIC DEPLOYMENT ROUTES FIRST
# # ══════════════════════════════════════════════

# @router.get("/deployments/unit/{unit_id}/active", summary="Active missions (Responder)")
# async def get_active_missions(
#     unit_id: str,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_user),
# ):
#     """Responder: active missions. Requires Bearer token."""
#     service = DeploymentService(db)
#     missions = await service.get_active_missions(unit_id)
#     return {"active_missions": missions, "count": len(missions)}


# @router.get("/deployments/unit/{unit_id}/completed", summary="Completed missions (Responder)")
# async def get_completed_missions(
#     unit_id: str,
#     limit: int = 20,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_user),
# ):
#     """Responder: completed missions. Requires Bearer token."""
#     service = DeploymentService(db)
#     missions = await service.get_completed_missions(unit_id, limit=limit)
#     return {"completed_missions": missions, "count": len(missions)}


# # ══════════════════════════════════════════════
# # DYNAMIC DEPLOYMENT ROUTES LAST
# # ══════════════════════════════════════════════

# @router.post("/deployments/{deployment_id}/update-status", summary="Update status (Responder)")
# async def update_deployment_status(
#     deployment_id: str,
#     data: UpdateStatusRequest,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_user),
# ):
#     """Responder updates deployment status. Requires Bearer token."""
#     service = DeploymentService(db)
#     return await service.update_status(
#         deployment_id=deployment_id, new_status=data.new_status,
#         situation_report=data.situation_report, tags=data.tags,
#         minor_injuries=data.minor_injuries, serious_injuries=data.serious_injuries,
#         additional_resources=data.additional_resources,
#         location_verified=data.location_verified,
#         request_immediate_backup=data.request_immediate_backup,
#         assessment_notes=data.assessment_notes,
#     )


# @router.get("/deployments/{deployment_id}", summary="Get deployment details")
# async def get_deployment(
#     deployment_id: str,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_user),
# ):
#     """Get deployment details. Requires Bearer token."""
#     service = DeploymentService(db)
#     return await service.get_deployment(deployment_id)




# File: app/api/v1/deployment.py
"""
Deployment API — with Bearer token auth.

- Dispatch: requires emergency team token (admin)
- Status updates: requires any token (responder)
- View missions: requires any token
"""

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging

from app.db.session import get_db
from app.auth.dependencies import get_current_user, get_current_team_member
from app.services.deployment_service import DeploymentService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Deployments"])


def _publish_pending_events(events: list):
    """Publish RabbitMQ events. Called as a BackgroundTask AFTER get_db() commits."""
    try:
        from app.services.rabbitmq_service import get_rabbitmq_service
        svc = get_rabbitmq_service()
        for topic, payload in events:
            svc.publish(topic, payload)
    except Exception as e:
        logger.error(f"Failed to publish post-commit events: {e}")


# ── Request Models ──

class DispatchRequest(BaseModel):
    unit_ids: List[str] = Field(...)
    priority_level: str = Field("STANDARD")
    special_instructions: Optional[str] = None


class UpdateStatusRequest(BaseModel):
    new_status: str = Field(...)
    situation_report: Optional[str] = None
    tags: Optional[List[str]] = None
    minor_injuries: int = Field(0)
    serious_injuries: int = Field(0)
    additional_resources: Optional[List[str]] = None
    location_verified: bool = Field(False)
    request_immediate_backup: bool = Field(False)
    assessment_notes: Optional[str] = None


# ══════════════════════════════════════════════
# ADMIN: Dispatch units (requires team token)
# ══════════════════════════════════════════════

@router.post("/disasters/{disaster_id}/dispatch", summary="Dispatch units (Admin)")
async def dispatch_units(
    disaster_id: str,
    data: DispatchRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """Admin dispatches units. Requires emergency team Bearer token."""
    if not data.unit_ids:
        raise HTTPException(status_code=400, detail="unit_ids cannot be empty.")
    service = DeploymentService(db)
    result = await service.dispatch_units(
        disaster_id=disaster_id, unit_ids=data.unit_ids,
        priority_level=data.priority_level, special_instructions=data.special_instructions,
    )
    # FIX #8: Publish after get_db() commits by using BackgroundTasks.
    # BackgroundTasks run after the response AND after dependencies are finalized.
    event = result.pop("_pending_event", None)
    if event:
        background_tasks.add_task(_publish_pending_events, [(event["topic"], event["payload"])])
    return result


# ══════════════════════════════════════════════
# STATIC DEPLOYMENT ROUTES FIRST
# ══════════════════════════════════════════════

@router.get("/deployments/unit/{unit_id}/active", summary="Active missions (Responder)")
async def get_active_missions(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Responder: active missions. Requires Bearer token."""
    service = DeploymentService(db)
    missions = await service.get_active_missions(unit_id)
    return {"active_missions": missions, "count": len(missions)}


@router.get("/deployments/unit/{unit_id}/completed", summary="Completed missions (Responder)")
async def get_completed_missions(
    unit_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Responder: completed missions. Requires Bearer token."""
    service = DeploymentService(db)
    missions = await service.get_completed_missions(unit_id, limit=limit)
    return {"completed_missions": missions, "count": len(missions)}


# ══════════════════════════════════════════════
# DYNAMIC DEPLOYMENT ROUTES LAST
# ══════════════════════════════════════════════

@router.post("/deployments/{deployment_id}/update-status", summary="Update status (Responder)")
async def update_deployment_status(
    deployment_id: str,
    data: UpdateStatusRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Responder updates deployment status. Requires Bearer token."""
    service = DeploymentService(db)
    result = await service.update_status(
        deployment_id=deployment_id, new_status=data.new_status,
        situation_report=data.situation_report, tags=data.tags,
        minor_injuries=data.minor_injuries, serious_injuries=data.serious_injuries,
        additional_resources=data.additional_resources,
        location_verified=data.location_verified,
        request_immediate_backup=data.request_immediate_backup,
        assessment_notes=data.assessment_notes,
    )
    # FIX #8: Publish after commit via BackgroundTasks
    pending_events = result.pop("_pending_events", [])
    if pending_events:
        background_tasks.add_task(_publish_pending_events, pending_events)
    return result


@router.get("/deployments/{deployment_id}", summary="Get deployment details")
async def get_deployment(
    deployment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Get deployment details. Requires Bearer token."""
    service = DeploymentService(db)
    return await service.get_deployment(deployment_id)