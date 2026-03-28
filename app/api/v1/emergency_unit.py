# File: app/api/v1/emergency_unit.py
"""
Emergency Unit API

CRUD for emergency units (fire engines, ambulances, patrol cars, etc.)
plus crew management endpoints.

Auth: all endpoints require emergency team Bearer token (get_current_team_member).

Route ordering note:
  Static: GET /available  → must come before /{unit_id}
  Static: GET /           → list all
  Static: POST /          → create
  Dynamic: /{unit_id}/*   → everything else
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.auth.dependencies import get_current_team_member
from app.services.emergency_unit_service import EmergencyUnitService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emergency-units", tags=["Emergency Units"])


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas
# ─────────────────────────────────────────────────────────────────────────────

class CreateUnitRequest(BaseModel):
    """Body for POST /emergency-units/"""
    unit_code:            str            = Field(..., description="Short code e.g. F-12, A-03")
    unit_name:            str            = Field(..., description="Full name e.g. Fire Engine Alpha")
    unit_type:            str            = Field(..., description="AMBULANCE | FIRE_ENGINE | PATROL_CAR | RESCUE | HAZMAT | COMMAND | RAPID_RESPONSE")
    department:           Optional[str]  = Field(None, description="FIRE | MEDICAL | POLICE | IT — auto-mapped from unit_type if omitted")
    capacity:             int            = Field(4,    description="Max crew size")
    station_name:         str            = Field(..., description="Name of the station this unit is based at")
    station_address:      Optional[str]  = None
    station_latitude:     Optional[float]= Field(53.3498, description="Station latitude (WGS-84)")
    station_longitude:    Optional[float]= Field(-6.2603, description="Station longitude (WGS-84)")
    commander_id:         Optional[str]  = Field(None, description="UUID of the team member to assign as commander")
    crew_member_ids:      Optional[List[str]] = Field(None, description="UUIDs of team members to add as initial crew")
    description:          Optional[str]  = None
    vehicle_model:        Optional[str]  = None
    vehicle_license_plate:Optional[str]  = None
    vehicle_year:         Optional[int]  = None
    equipment_checklist:  Optional[List[dict]] = None


class UpdateUnitRequest(BaseModel):
    """Body for PUT /emergency-units/{unit_id}"""
    unit_name:            Optional[str]   = None
    unit_type:            Optional[str]   = None
    department:           Optional[str]   = None
    unit_status:          Optional[str]   = Field(None, description="AVAILABLE | DEPLOYED | MAINTENANCE | OFFLINE | RETURNING")
    capacity:             Optional[int]   = None
    station_name:         Optional[str]   = None
    station_address:      Optional[str]   = None
    station_latitude:     Optional[float] = None
    station_longitude:    Optional[float] = None
    commander_id:         Optional[str]   = None
    description:          Optional[str]   = None
    vehicle_model:        Optional[str]   = None
    vehicle_license_plate:Optional[str]   = None
    vehicle_year:         Optional[int]   = None
    equipment_checklist:  Optional[List[dict]] = None
    crew_member_ids:      Optional[List[str]]  = None


class UpdateCrewRequest(BaseModel):
    """Body for PUT /emergency-units/{unit_id}/crew — replaces entire roster"""
    crew_member_ids: List[str]   = Field(..., description="Full list of team member UUIDs for the new roster")
    commander_id:    Optional[str] = Field(None, description="UUID of team member to set as commander")


class AddCrewMemberRequest(BaseModel):
    """Body for POST /emergency-units/{unit_id}/crew — add a single member"""
    team_member_id: str = Field(..., description="UUID of the team member to add")


# ─────────────────────────────────────────────────────────────────────────────
# Static routes first
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/available",
    summary="List available units for dispatch",
)
async def list_available_units(
    disaster_id:  Optional[str] = None,
    department:   Optional[str] = None,
    unit_type:    Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Returns all units with status=AVAILABLE, sorted by distance from the
    disaster location when disaster_id is provided.
    Optionally filter by department (FIRE | MEDICAL | POLICE) or
    unit_type (FIRE_ENGINE | AMBULANCE | PATROL_CAR etc.).
    Requires: emergency team Bearer token.
    """
    service = EmergencyUnitService(db)
    units   = await service.list_available_units(
        disaster_id=disaster_id,
        department=department,
        unit_type=unit_type,
    )
    return {"available_units": units, "count": len(units)}


@router.get(
    "/",
    summary="List all emergency units",
)
async def list_units(
    department:  Optional[str] = None,
    unit_status: Optional[str] = None,
    unit_type:   Optional[str] = None,
    search:      Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Returns all units with optional filters.
    `search` matches against unit_code and unit_name.
    Requires: emergency team Bearer token.
    """
    service = EmergencyUnitService(db)
    return await service.list_units(
        department=department,
        unit_status=unit_status,
        unit_type=unit_type,
        search=search,
        limit=limit,
    )


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new emergency unit",
)
async def create_unit(
    data: CreateUnitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Creates a new unit and optionally assigns a commander and crew.

    Department is auto-mapped from unit_type if not provided:
      FIRE_ENGINE / RESCUE / HAZMAT → FIRE
      AMBULANCE / RAPID_RESPONSE    → MEDICAL
      PATROL_CAR                    → POLICE
      COMMAND                       → FIRE (override with department field)

    Commander and crew are validated against the emergency_teams table.
    A warning is returned (not an error) if crew members are from a different department.
    Requires: emergency team Bearer token.
    """
    service     = EmergencyUnitService(db)
    create_data = {k: v for k, v in data.model_dump().items() if v is not None}
    return await service.create_unit(create_data)


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic routes last ({unit_id} path parameter)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{unit_id}",
    summary="Get unit details",
)
async def get_unit(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Returns full unit detail including commander, crew roster, vehicle info,
    and current assignment (if deployed).
    Requires: emergency team Bearer token.
    """
    service = EmergencyUnitService(db)
    return await service.get_unit(unit_id)


@router.put(
    "/{unit_id}",
    summary="Update unit configuration",
)
async def update_unit(
    unit_id: str,
    data: UpdateUnitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Updates any combination of unit fields.
    Only non-null fields in the request body are applied.
    Requires: emergency team Bearer token.
    """
    service     = EmergencyUnitService(db)
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    return await service.update_unit(unit_id, update_data)


@router.delete(
    "/{unit_id}",
    summary="Decommission a unit (soft delete)",
)
async def decommission_unit(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Soft-deletes a unit (sets deleted_at). The unit will no longer appear
    in lists or be available for dispatch, but data is retained for audit.
    Cannot decommission a unit that is currently DEPLOYED.
    Requires: emergency team Bearer token.
    """
    service = EmergencyUnitService(db)
    return await service.decommission_unit(unit_id)


# ─────────────────────────────────────────────────────────────────────────────
# Crew management
# ─────────────────────────────────────────────────────────────────────────────

@router.put(
    "/{unit_id}/crew",
    summary="Replace entire crew roster",
)
async def update_crew(
    unit_id: str,
    data: UpdateCrewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Replaces the full crew roster in a single call.
    Optionally updates the commander at the same time.
    Existing crew members not in the new list are removed.
    Requires: emergency team Bearer token.
    """
    service     = EmergencyUnitService(db)
    update_data = {"crew_member_ids": data.crew_member_ids}
    if data.commander_id:
        update_data["commander_id"] = data.commander_id
    return await service.update_unit(unit_id, update_data)


@router.post(
    "/{unit_id}/crew",
    summary="Add a single crew member",
)
async def add_crew_member(
    unit_id: str,
    data: AddCrewMemberRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Adds one team member to the crew roster without replacing existing members.
    Returns 409 if the member is already on this unit.
    Requires: emergency team Bearer token.
    """
    service = EmergencyUnitService(db)
    return await service.add_crew_member(unit_id, data.team_member_id)


@router.delete(
    "/{unit_id}/crew/{member_id}",
    summary="Remove a crew member",
)
async def remove_crew_member(
    unit_id:   str,
    member_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Removes a specific team member from the crew roster.
    Returns 404 if the member is not on this unit.
    Requires: emergency team Bearer token.
    """
    service = EmergencyUnitService(db)
    return await service.remove_crew_member(unit_id, member_id)