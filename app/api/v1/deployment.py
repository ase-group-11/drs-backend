# File: app/api/v1/deployment.py
"""
Deployment API endpoints.

Supports:
  - Admin Panel: Dispatch units to disasters
  - Responder App: Active missions, mission progress, update status
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List

from app.db.session import get_db
from app.services.deployment_service import DeploymentService

router = APIRouter(tags=["Deployments"])


# ── Request Models ──

class DispatchRequest(BaseModel):
    unit_ids: List[str] = Field(..., description="List of unit UUIDs to dispatch")
    priority_level: str = Field("STANDARD", description="STANDARD, HIGH, or CRITICAL")
    special_instructions: Optional[str] = Field(None, description="Instructions for the team")


class UpdateStatusRequest(BaseModel):
    new_status: str = Field(..., description="DISPATCHED, EN_ROUTE, ON_SCENE, IN_PROGRESS, COMPLETED")
    situation_report: Optional[str] = Field(None, description="Description of current situation")
    tags: Optional[List[str]] = Field(None, description="Quick tags: scene_secured, casualties_present, etc.")
    minor_injuries: int = Field(0)
    serious_injuries: int = Field(0)
    additional_resources: Optional[List[str]] = Field(None, description="ADDITIONAL_UNITS, MEDICAL_SUPPORT, etc.")
    location_verified: bool = Field(False)
    request_immediate_backup: bool = Field(False)
    assessment_notes: Optional[str] = None


# ══════════════════════════════════════════════
# DISASTER-SCOPED: Dispatch units
# ══════════════════════════════════════════════

@router.post(
    "/disasters/{disaster_id}/dispatch",
    summary="Dispatch units to disaster",
    description="Admin dispatches one or more units. Supports Dispatch Modal."
)
async def dispatch_units(
    disaster_id: str,
    data: DispatchRequest,
    db: AsyncSession = Depends(get_db),
):
    if not data.unit_ids:
        raise HTTPException(status_code=400, detail="unit_ids cannot be empty.")

    service = DeploymentService(db)
    return await service.dispatch_units(
        disaster_id=disaster_id,
        unit_ids=data.unit_ids,
        priority_level=data.priority_level,
        special_instructions=data.special_instructions,
    )


# ══════════════════════════════════════════════
# DEPLOYMENT-SCOPED: Status updates, details
# ══════════════════════════════════════════════

@router.post(
    "/deployments/{deployment_id}/update-status",
    summary="Update deployment status",
    description="Responder updates status: EN_ROUTE → ON_SCENE → IN_PROGRESS → COMPLETED"
)
async def update_deployment_status(
    deployment_id: str,
    data: UpdateStatusRequest,
    db: AsyncSession = Depends(get_db),
):
    service = DeploymentService(db)
    return await service.update_status(
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


@router.get(
    "/deployments/{deployment_id}",
    summary="Get deployment details",
    description="Full deployment details with timeline and situation report. Supports Mission Progress page."
)
async def get_deployment(
    deployment_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = DeploymentService(db)
    return await service.get_deployment(deployment_id)


# ══════════════════════════════════════════════
# UNIT-SCOPED: Active missions for responder
# ══════════════════════════════════════════════

@router.get(
    "/deployments/unit/{unit_id}/active",
    summary="Get active missions for a unit",
    description="Responder's Active Missions page — shows all assigned deployments."
)
async def get_active_missions(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = DeploymentService(db)
    missions = await service.get_active_missions(unit_id)
    return {"active_missions": missions, "count": len(missions)}


@router.get(
    "/deployments/unit/{unit_id}/completed",
    summary="Get completed missions for a unit",
    description="Responder's Completed tab — shows finished deployments."
)
async def get_completed_missions(
    unit_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    service = DeploymentService(db)
    missions = await service.get_completed_missions(unit_id, limit=limit)
    return {"completed_missions": missions, "count": len(missions)}