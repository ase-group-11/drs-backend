# File: app/api/v1/evacuation.py
"""
Evacuation API Router — Use Case 8: Plan Evacuation

Mirrors app/api/v1/reroute.py exactly:
  - get_evacuation_service() factory function uses Depends() injection
  - All dependencies (DB, TomTom, mapping, publisher) come from the factory
  - Auth: app.auth.dependencies.get_current_team_member
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_team_member
from app.db.session import get_db
from app.providers.integration_service import IntegrationService, get_integration_service
from app.repositories.evacuation_repository import EvacuationRepository
from app.schemas.evacuation import (
    ApproveEvacuationRequest,
    EscalationRequest,
    PlanEvacuationRequest,
    RouteBlockageRequest,
    UpdateProgressRequest,
)
from app.services.evacuation_service import EvacuationService
from app.services.instant_map_updates import MappingService
from app.socket.manager import sio
from app.workers.reroute_publisher import ReroutePublisher, get_publisher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evacuations", tags=["Evacuation — UC8"])


# ---------------------------------------------------------------------------
# Dependency factory — mirrors get_reroute_service() in reroute.py
# ---------------------------------------------------------------------------

def get_evacuation_service(
    db: AsyncSession = Depends(get_db),
    external: IntegrationService = Depends(get_integration_service),
    publisher: ReroutePublisher = Depends(get_publisher),
) -> EvacuationService:
    return EvacuationService(
        db=EvacuationRepository(db),
        external=external,
        mapping=MappingService(sio=sio),
        publisher=publisher,
    )


# ── Phase 1 ───────────────────────────────────────────────────────────────────

@router.post(
    "/plan",
    status_code=status.HTTP_201_CREATED,
    summary="Phase 1 — Create evacuation plan",
)
async def plan_evacuation(
    data: PlanEvacuationRequest,
    service: EvacuationService = Depends(get_evacuation_service),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    return await service.plan_evacuation(
        disaster_id=data.disaster_id,
        auto_approve=data.auto_approve,
    )


# ── Phase 2 ───────────────────────────────────────────────────────────────────

@router.post("/{plan_id}/approve", summary="Phase 2 — Approve evacuation plan")
async def approve_evacuation(
    plan_id: str,
    data: ApproveEvacuationRequest,
    service: EvacuationService = Depends(get_evacuation_service),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    return await service.approve_evacuation(
        plan_id=plan_id, approved_by=data.approved_by, notes=data.notes)


# ── Phase 3 ───────────────────────────────────────────────────────────────────

@router.post("/{plan_id}/activate", summary="Phase 3 — Activate approved plan")
async def activate_evacuation(
    plan_id: str,
    service: EvacuationService = Depends(get_evacuation_service),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    return await service.activate_evacuation(plan_id=plan_id)


# ── Phase 4 ───────────────────────────────────────────────────────────────────

@router.get("/{plan_id}/progress", summary="Phase 4 — Live evacuation progress")
async def get_progress(
    plan_id: str,
    service: EvacuationService = Depends(get_evacuation_service),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    return await service.get_progress(plan_id=plan_id)


@router.post("/{plan_id}/progress", summary="Phase 4 — Push completion update")
async def update_progress(
    plan_id: str,
    data: UpdateProgressRequest,
    service: EvacuationService = Depends(get_evacuation_service),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    return await service.update_progress(
        plan_id=plan_id, completion_metrics=data.completion_metrics)


@router.post("/{plan_id}/route-blockage", summary="Phase 4 alt — Handle route blockage")
async def handle_route_blockage(
    plan_id: str,
    data: RouteBlockageRequest,
    service: EvacuationService = Depends(get_evacuation_service),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    return await service.handle_route_blockage(
        plan_id=plan_id,
        blocked_roads=data.blocked_roads,
        affected_zone_ids=data.affected_zone_ids,
    )


@router.post("/{plan_id}/escalate", summary="Phase 4 alt — Disaster escalation")
async def handle_escalation(
    plan_id: str,
    data: EscalationRequest,
    service: EvacuationService = Depends(get_evacuation_service),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    return await service.handle_disaster_escalation(
        plan_id=plan_id, new_zone_ids=data.new_zone_ids, reason=data.reason)


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("/", summary="List evacuation plans")
async def list_plans(
    disaster_id: Optional[str] = Query(None, description="Filter by disaster ID"),
    service: EvacuationService = Depends(get_evacuation_service),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    plans = await service.list_plans(disaster_id=disaster_id)
    return {"evacuation_plans": plans, "count": len(plans)}


@router.get("/{plan_id}", summary="Get full evacuation plan detail")
async def get_plan(
    plan_id: str,
    service: EvacuationService = Depends(get_evacuation_service),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    return await service.get_plan(plan_id=plan_id)
