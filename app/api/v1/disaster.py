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

class ResolveDisasterRequest(BaseModel):
    """Mark disaster as resolved."""
    resolution_notes: str = Field(..., description="Notes about how the disaster was resolved")


class EscalateDisasterRequest(BaseModel):
    """Escalate disaster severity."""
    new_severity: str = Field(..., description="New severity: LOW, MEDIUM, HIGH, CRITICAL")
    reason: Optional[str] = Field(None, description="Reason for escalation")


# ══════════════════════════════════════════════
# STATIC ROUTES FIRST
# ══════════════════════════════════════════════

@router.get(
    "/unverified",
    summary="List UNVERIFIED disasters",
    description="Admin panel: disasters awaiting field verification."
)
async def list_unverified(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    service = DisasterService(db)
    return await service.list_disasters(disaster_status="UNVERIFIED", limit=limit)


@router.get(
    "/active",
    summary="List ACTIVE disasters",
    description="Admin panel: confirmed active disasters."
)
async def list_active(
    severity: Optional[str] = None,
    disaster_type: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    service = DisasterService(db)
    return await service.list_disasters(
        disaster_status="ACTIVE",
        severity=severity,
        disaster_type=disaster_type,
        limit=limit,
    )


@router.get(
    "/all",
    summary="List all disasters",
    description="Admin panel: all disasters with optional filters."
)
async def list_all(
    disaster_status: Optional[str] = None,
    severity: Optional[str] = None,
    disaster_type: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    service = DisasterService(db)
    return await service.list_disasters(
        disaster_status=disaster_status,
        severity=severity,
        disaster_type=disaster_type,
        limit=limit,
    )


# ══════════════════════════════════════════════
# DYNAMIC ROUTES LAST
# ══════════════════════════════════════════════


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
    """Mark disaster as resolved."""
    service = DisasterService(db)
    return await service.resolve_disaster(
        disaster_id=disaster_id,
        resolution_notes=data.resolution_notes,
    )


@router.post(
    "/{disaster_id}/escalate",
    summary="Escalate disaster priority",
    description="Update severity level of an active disaster."
)
async def escalate_disaster(
    disaster_id: str,
    data: EscalateDisasterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Escalate disaster severity."""
    service = DisasterService(db)
    return await service.escalate_disaster(
        disaster_id=disaster_id,
        new_severity=data.new_severity,
        reason=data.reason,
    )


@router.get(
    "/{disaster_id}/photos",
    summary="Get disaster photos",
    description="Get all photos from reports linked to this disaster."
)
async def get_disaster_photos(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get photos for a disaster from linked reports."""
    service = DisasterService(db)
    photos = await service.get_disaster_photos(disaster_id)
    return {"photos": photos, "count": len(photos)}


@router.get(
    "/{disaster_id}/deployments",
    summary="Get disaster deployments",
    description="Get all units deployed to this disaster with status summary."
)
async def get_disaster_deployments(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get deployment summary for a disaster."""
    service = DisasterService(db)
    return await service.get_disaster_deployments(disaster_id)