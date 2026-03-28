# File: app/api/v1/evacuation.py
"""
Evacuation API Router — UC8: Plan Evacuation

4-phase lifecycle:
  Phase 1 POST /evacuations/plan              → create plan (auto-computes zones, routes, transport)
  Phase 2 POST /evacuations/{id}/approve      → ERT commander reviews and approves
  Phase 3 POST /evacuations/{id}/activate     → sends alerts to residents, updates map
  Phase 4 GET  /evacuations/{id}/progress     → monitor live completion %
           POST /evacuations/{id}/progress    → push zone completion update
           POST /evacuations/{id}/route-blockage → recompute routes when road blocked
           POST /evacuations/{id}/escalate    → add new zones if disaster spreads

Auth: all endpoints require emergency team Bearer token (get_current_team_member).

Dependency factory mirrors get_reroute_service() in reroute.py exactly —
constructor injection means all 4 dependencies (db, external, mapping, publisher)
are swappable AsyncMock objects in tests.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

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


# ─────────────────────────────────────────────────────────────────────────────
# Dependency factory
# Mirrors get_reroute_service() — constructor injection so tests can swap mocks.
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Create plan
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/plan",
    status_code=status.HTTP_201_CREATED,
    summary="Phase 1 — Create evacuation plan",
)
async def plan_evacuation(
    data: PlanEvacuationRequest,
    service: EvacuationService = Depends(get_evacuation_service),
):
    """
    Automatically computes evacuation plan for a disaster:
      1. Find impact zones near disaster (by severity radius)
      2. Calculate population + vulnerable counts per zone
      3. Check blocked roads from UC7's road_segments table
      4. Get live traffic from TomTom
      5. Get available shelters
      6. Compute optimal routes per zone (concurrent TomTom calls)
      7. Calculate transport needs (buses + accessible transport)
      8. Save plan — status = PENDING (or APPROVED if auto_approve=true)

    Requires: emergency team Bearer token.
    """
    return await service.plan_evacuation(
        disaster_id=data.disaster_id,
        auto_approve=data.auto_approve,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Approve
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{plan_id}/approve",
    summary="Phase 2 — Approve evacuation plan",
)
async def approve_evacuation(
    plan_id: str,
    data: ApproveEvacuationRequest,
    service: EvacuationService = Depends(get_evacuation_service),
):
    """
    ERT Commander reviews and approves the plan.
    Plan must be in PENDING status. Moves to APPROVED.
    Nothing is sent to citizens until Phase 3 (activate).
    Requires: emergency team Bearer token.
    """
    return await service.approve_evacuation(
        plan_id=plan_id,
        approved_by=data.approved_by,
        notes=data.notes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Activate
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{plan_id}/activate",
    summary="Phase 3 — Activate approved plan (sends alerts to residents)",
)
async def activate_evacuation(
    plan_id: str,
    service: EvacuationService = Depends(get_evacuation_service),
):
    """
    Activates an APPROVED plan — this is the moment everything goes live:
      1. Sends evacuation alerts to all residents in affected zones (RabbitMQ + Twilio fallback)
      2. Updates the live map with evacuation routes and shelter markers (Socket.IO)
      3. Notifies transport coordinators (RabbitMQ evacuation.triggered event)
      4. Initialises zone completion metrics at 0%

    Requires: emergency team Bearer token.
    """
    return await service.activate_evacuation(plan_id=plan_id)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — Monitor
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{plan_id}/progress",
    summary="Phase 4 — Get live evacuation progress",
)
async def get_progress(
    plan_id: str,
    service: EvacuationService = Depends(get_evacuation_service),
):
    """
    Returns current completion % per zone plus live traffic snapshot.
    Poll this every 30 seconds from the admin dashboard.
    Requires: emergency team Bearer token.
    """
    return await service.get_progress(plan_id=plan_id)


@router.post(
    "/{plan_id}/progress",
    summary="Phase 4 — Push zone completion update",
)
async def update_progress(
    plan_id: str,
    data: UpdateProgressRequest,
    service: EvacuationService = Depends(get_evacuation_service),
):
    """
    Field coordinators push zone completion data as buses finish runs.
    When ALL zones reach 100%, the plan automatically moves to COMPLETED.
    Requires: emergency team Bearer token.
    """
    return await service.update_progress(
        plan_id=plan_id,
        completion_metrics=data.completion_metrics,
    )


@router.post(
    "/{plan_id}/route-blockage",
    summary="Phase 4 alt — Handle route blockage (recompute affected zones)",
)
async def handle_route_blockage(
    plan_id: str,
    data: RouteBlockageRequest,
    service: EvacuationService = Depends(get_evacuation_service),
):
    """
    Called when a road used in an evacuation route becomes blocked.
    Recomputes routes for affected zones and re-alerts residents with new routes.
    Merges with UC7's live blocked roads from road_segments table.
    Requires: emergency team Bearer token.
    """
    return await service.handle_route_blockage(
        plan_id=plan_id,
        blocked_roads=data.blocked_roads,
        affected_zone_ids=data.affected_zone_ids,
    )


@router.post(
    "/{plan_id}/escalate",
    summary="Phase 4 alt — Add new zones when disaster spreads",
)
async def handle_escalation(
    plan_id: str,
    data: EscalationRequest,
    service: EvacuationService = Depends(get_evacuation_service),
):
    """
    Expands the evacuation plan to cover additional zones.
    Recomputes transport needs and sends alerts to newly affected residents.
    Requires: emergency team Bearer token.
    """
    return await service.handle_disaster_escalation(
        plan_id=plan_id,
        new_zone_ids=data.new_zone_ids,
        reason=data.reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/",
    summary="List evacuation plans",
)
async def list_plans(
    disaster_id: Optional[str] = Query(None, description="Filter by disaster ID"),
    service: EvacuationService = Depends(get_evacuation_service),
):
    """
    Returns the 50 most recent plans, newest first.
    Optionally filter by disaster_id.
    Requires: emergency team Bearer token.
    """
    plans = await service.list_plans(disaster_id=disaster_id)
    return {"evacuation_plans": plans, "count": len(plans)}


@router.get(
    "/{plan_id}",
    summary="Get full evacuation plan detail",
)
async def get_plan(
    plan_id: str,
    service: EvacuationService = Depends(get_evacuation_service),
):
    """
    Returns the full plan including zones, routes, shelters, transport plan,
    and completion metrics. Used by the admin detail view.
    Requires: emergency team Bearer token.
    """
    return await service.get_plan(plan_id=plan_id)