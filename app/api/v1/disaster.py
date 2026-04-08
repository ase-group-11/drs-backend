# # File: app/api/v1/disaster.py
# """
# Disaster Management API — with Bearer token auth.

# All endpoints require emergency team Bearer token.
# """

# from fastapi import APIRouter, Depends, HTTPException, status
# from sqlalchemy.ext.asyncio import AsyncSession
# from pydantic import BaseModel, Field
# from typing import Optional, Dict, Any

# from app.db.session import get_db
# from app.auth.dependencies import get_current_user, get_current_team_member
# from app.services.disaster_service import DisasterService

# router = APIRouter(prefix="/disasters", tags=["Disaster Management"])


# class ResolveDisasterRequest(BaseModel):
#     resolution_notes: str = Field(...)


# class EscalateDisasterRequest(BaseModel):
#     new_severity: str = Field(..., description="LOW, MEDIUM, HIGH, CRITICAL")
#     reason: Optional[str] = None


# # ══════════════════════════════════════════════
# # STATIC ROUTES FIRST
# # ══════════════════════════════════════════════

# @router.get("/unverified", summary="List UNVERIFIED disasters (Admin)")
# async def list_unverified(
#     limit: int = 50,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_team_member),
# ):
#     service = DisasterService(db)
#     return await service.list_disasters(disaster_status="UNVERIFIED", limit=limit)


# @router.get("/active", summary="List ACTIVE disasters (Admin)")
# async def list_active(
#     severity: Optional[str] = None,
#     disaster_type: Optional[str] = None,
#     limit: int = 50,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_team_member),
# ):
#     service = DisasterService(db)
#     return await service.list_disasters(
#         disaster_status="ACTIVE", severity=severity,
#         disaster_type=disaster_type, limit=limit,
#     )


# @router.get("/all", summary="List all disasters (Admin)")
# async def list_all(
#     disaster_status: Optional[str] = None,
#     severity: Optional[str] = None,
#     disaster_type: Optional[str] = None,
#     limit: int = 50,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_team_member),
# ):
#     service = DisasterService(db)
#     return await service.list_disasters(
#         disaster_status=disaster_status, severity=severity,
#         disaster_type=disaster_type, limit=limit,
#     )


# # ══════════════════════════════════════════════
# # DYNAMIC ROUTES LAST
# # ══════════════════════════════════════════════

# @router.get("/{disaster_id}", summary="Get disaster details")
# async def get_disaster(
#     disaster_id: str,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_user),
# ):
#     service = DisasterService(db)
#     return await service.get_disaster(disaster_id)


# @router.post("/{disaster_id}/resolve", summary="Resolve disaster (Admin)")
# async def resolve_disaster(
#     disaster_id: str,
#     data: ResolveDisasterRequest,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_team_member),
# ):
#     service = DisasterService(db)
#     return await service.resolve_disaster(disaster_id=disaster_id, resolution_notes=data.resolution_notes)


# @router.post("/{disaster_id}/escalate", summary="Escalate priority (Admin)")
# async def escalate_disaster(
#     disaster_id: str,
#     data: EscalateDisasterRequest,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_team_member),
# ):
#     service = DisasterService(db)
#     return await service.escalate_disaster(
#         disaster_id=disaster_id, new_severity=data.new_severity, reason=data.reason,
#     )


# @router.get("/{disaster_id}/photos", summary="Get disaster photos")
# async def get_disaster_photos(
#     disaster_id: str,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_user),
# ):
#     service = DisasterService(db)
#     photos = await service.get_disaster_photos(disaster_id)
#     return {"photos": photos, "count": len(photos)}


# @router.get("/{disaster_id}/deployments", summary="Get disaster deployments (Admin)")
# async def get_disaster_deployments(
#     disaster_id: str,
#     db: AsyncSession = Depends(get_db),
#     current_user: Dict[str, Any] = Depends(get_current_team_member),
# ):
#     service = DisasterService(db)
#     return await service.get_disaster_deployments(disaster_id)




# File: app/api/v1/disaster.py
"""
Disaster Management API — with Bearer token auth.

All endpoints require emergency team Bearer token.
"""

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import logging

from app.db.session import get_db
from app.auth.dependencies import get_current_user, get_current_team_member
from app.services.disaster_service import DisasterService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/disasters", tags=["Disaster Management"])


def _publish_disaster_event(topic: str, payload: dict):
    """Publish a single disaster event AFTER get_db() has committed."""
    try:
        from app.services.rabbitmq_service import (
            publish_disaster_updated,
            publish_disaster_resolved,
        )
        if topic == "disaster.resolved":
            publish_disaster_resolved(payload)
        else:
            publish_disaster_updated(payload)
    except Exception as e:
        logger.error(f"Failed to publish {topic} event: {e}")


class ResolveDisasterRequest(BaseModel):
    resolution_notes: str = Field(...)


class EscalateDisasterRequest(BaseModel):
    new_severity: str = Field(..., description="LOW, MEDIUM, HIGH, CRITICAL")
    reason: Optional[str] = None


# ══════════════════════════════════════════════
# STATIC ROUTES FIRST
# ══════════════════════════════════════════════

@router.get("/active", summary="List ACTIVE disasters (Admin)")
async def list_active(
    severity: Optional[str] = None,
    disaster_type: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = DisasterService(db)
    return await service.list_disasters(
        disaster_status="ACTIVE", severity=severity,
        disaster_type=disaster_type, limit=limit,
    )


@router.get("/all", summary="List all disasters (Admin)")
async def list_all(
    disaster_status: Optional[str] = None,
    severity: Optional[str] = None,
    disaster_type: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    service = DisasterService(db)
    return await service.list_disasters(
        disaster_status=disaster_status, severity=severity,
        disaster_type=disaster_type, limit=limit,
    )


# ══════════════════════════════════════════════
# DYNAMIC ROUTES LAST
# ══════════════════════════════════════════════

@router.get("/{disaster_id}", summary="Get disaster details")
async def get_disaster(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    service = DisasterService(db)
    return await service.get_disaster(disaster_id)


@router.post("/{disaster_id}/resolve", summary="Resolve disaster (Admin)")
async def resolve_disaster(
    disaster_id: str,
    data: ResolveDisasterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = DisasterService(db)
    result = await service.resolve_disaster(disaster_id=disaster_id, resolution_notes=data.resolution_notes)
    event = result.pop("_pending_event", None)
    if event:
        background_tasks.add_task(_publish_disaster_event, event[0], event[1])
    return result


@router.post("/{disaster_id}/escalate", summary="Escalate priority (Admin)")
async def escalate_disaster(
    disaster_id: str,
    data: EscalateDisasterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = DisasterService(db)
    result = await service.escalate_disaster(
        disaster_id=disaster_id, new_severity=data.new_severity, reason=data.reason,
    )
    event = result.pop("_pending_event", None)
    if event:
        background_tasks.add_task(_publish_disaster_event, event[0], event[1])
    return result


@router.get("/{disaster_id}/photos", summary="Get disaster photos")
async def get_disaster_photos(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    service = DisasterService(db)
    photos = await service.get_disaster_photos(disaster_id)
    return {"photos": photos, "count": len(photos)}


@router.get("/{disaster_id}/deployments", summary="Get disaster deployments (Admin)")
async def get_disaster_deployments(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = DisasterService(db)
    return await service.get_disaster_deployments(disaster_id)