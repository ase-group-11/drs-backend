# File: app/api/v1/disaster.py
"""
Disaster Management API endpoints.

Handles disaster lifecycle AFTER approval:
  - Assign emergency team + department
  - Record response time (team arrived on scene)
  - Resolve disaster with notes
  - Get full disaster details
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional

from app.db.session import get_db
from app.services.disaster_service import DisasterService

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/disasters", tags=["Disaster Management"])


# ── Request Models ──

class AssignDisasterRequest(BaseModel):
    """Assign emergency team and department to a disaster."""
    assigned_to_id: str = Field(..., description="Emergency team member UUID from emergency_teams table")
    assigned_department: str = Field(..., description="Department: FIRE, MEDICAL, POLICE, IT")


class RespondDisasterRequest(BaseModel):
    """Record when first responder arrives on scene."""
    response_notes: Optional[str] = Field(None, description="Optional notes about arrival")


class ResolveDisasterRequest(BaseModel):
    """Mark disaster as resolved."""
    resolution_notes: str = Field(..., description="Notes about how the disaster was resolved")


# ──────────────────────────────────────────────
# GET: Disaster details
# ──────────────────────────────────────────────
@router.get(
    "/{disaster_id}",
    summary="Get disaster details",
    description="Fetch full disaster details including assignment, response time, and resolution info."
)
async def get_disaster(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get full disaster details by ID."""
    service = DisasterService(db)
    return await service.get_disaster(disaster_id)


# ──────────────────────────────────────────────
# ASSIGN: Assign team + department
# ──────────────────────────────────────────────
@router.post(
    "/{disaster_id}/assign",
    summary="Assign emergency team to disaster",
    description="Assign a team member and department to an active disaster."
)
async def assign_disaster(
    disaster_id: str,
    data: AssignDisasterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Assign emergency team to a disaster.

    Sets assigned_to_id and assigned_department.
    Only works on ACTIVE disasters.
    """
    service = DisasterService(db)
    return await service.assign_disaster(
        disaster_id=disaster_id,
        assigned_to_id=data.assigned_to_id,
        assigned_department=data.assigned_department,
    )


# ──────────────────────────────────────────────
# RESPOND: Record response time
# ──────────────────────────────────────────────
@router.post(
    "/{disaster_id}/respond",
    summary="Record response time",
    description="Record when the first responder arrives on scene. Automatically sets response_time to now."
)
async def respond_disaster(
    disaster_id: str,
    data: RespondDisasterRequest = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Record that emergency team has arrived on scene.

    Sets response_time to current timestamp.
    Only works on ACTIVE disasters that have been assigned.
    """
    service = DisasterService(db)
    return await service.respond_disaster(
        disaster_id=disaster_id,
        response_notes=data.response_notes if data else None,
    )


# ──────────────────────────────────────────────
# RESOLVE: Mark disaster as resolved
# ──────────────────────────────────────────────
@router.post(
    "/{disaster_id}/resolve",
    summary="Mark disaster as resolved",
    description="Mark an active disaster as resolved with resolution notes."
)
async def resolve_disaster(
    disaster_id: str,
    data: ResolveDisasterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Mark disaster as resolved.

    Sets disaster_status to RESOLVED, resolved_time to now,
    and saves resolution_notes.
    """
    service = DisasterService(db)
    return await service.resolve_disaster(
        disaster_id=disaster_id,
        resolution_notes=data.resolution_notes,
    )