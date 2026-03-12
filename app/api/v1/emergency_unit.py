# File: app/api/v1/emergency_unit.py
"""
Emergency Unit API endpoints.

Supports:
  - Admin Panel: Emergency Teams page (list, create, details, config)
  - Dispatch Modal: List available units with distance/ETA
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List

from app.db.session import get_db
from app.services.emergency_unit_service import EmergencyUnitService

router = APIRouter(prefix="/emergency-units", tags=["Emergency Units"])


# ── Request Models ──

class CreateUnitRequest(BaseModel):
    unit_code: str = Field(..., description="Unit code e.g. F-12")
    unit_name: str = Field(..., description="Unit name e.g. Fire Response Unit")
    unit_type: str = Field(..., description="AMBULANCE, FIRE_ENGINE, PATROL_CAR, RESCUE, HAZMAT, COMMAND")
    department: str = Field(..., description="FIRE, MEDICAL, POLICE, IT")
    capacity: int = Field(4, description="Max crew size")
    station_name: str = Field(..., description="Station name")
    station_address: Optional[str] = None
    station_latitude: Optional[float] = 53.3498
    station_longitude: Optional[float] = -6.2603
    commander_id: Optional[str] = None
    description: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_license_plate: Optional[str] = None
    vehicle_year: Optional[int] = None
    crew_member_ids: Optional[List[str]] = None


class UpdateUnitRequest(BaseModel):
    unit_name: Optional[str] = None
    unit_type: Optional[str] = None
    department: Optional[str] = None
    unit_status: Optional[str] = None
    capacity: Optional[int] = None
    station_name: Optional[str] = None
    station_address: Optional[str] = None
    station_latitude: Optional[float] = None
    station_longitude: Optional[float] = None
    commander_id: Optional[str] = None
    description: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_license_plate: Optional[str] = None
    vehicle_year: Optional[int] = None
    equipment_checklist: Optional[List[dict]] = None
    crew_member_ids: Optional[List[str]] = None


# ── STATIC ROUTES FIRST ──

@router.get(
    "/available",
    summary="List available units for dispatch",
    description="Returns AVAILABLE units sorted by distance from disaster. Used by Dispatch Modal."
)
async def list_available_units(
    disaster_id: Optional[str] = None,
    department: Optional[str] = None,
    unit_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyUnitService(db)
    units = await service.list_available_units(
        disaster_id=disaster_id,
        department=department,
        unit_type=unit_type,
    )
    return {"available_units": units, "count": len(units)}


@router.get(
    "/",
    summary="List all emergency units",
    description="Filterable list for Emergency Teams admin page."
)
async def list_units(
    department: Optional[str] = None,
    unit_status: Optional[str] = None,
    unit_type: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
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
    summary="Create new emergency unit",
    description="Creates a new unit. Used by 'Add New Unit' button."
)
async def create_unit(
    data: CreateUnitRequest,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyUnitService(db)
    return await service.create_unit(data.model_dump())


# ── DYNAMIC ROUTES LAST ──

@router.get(
    "/{unit_id}",
    summary="Get unit details",
    description="Full unit details with crew roster, stats, and current assignment."
)
async def get_unit(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyUnitService(db)
    return await service.get_unit(unit_id)


@router.put(
    "/{unit_id}",
    summary="Update unit configuration",
    description="Update any unit fields. Only provided fields are changed."
)
async def update_unit(
    unit_id: str,
    data: UpdateUnitRequest,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyUnitService(db)
    # Only pass non-None fields
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    return await service.update_unit(unit_id, update_data)


@router.delete(
    "/{unit_id}",
    summary="Decommission unit",
    description="Soft delete a unit. Cannot decommission while deployed."
)
async def decommission_unit(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyUnitService(db)
    return await service.decommission_unit(unit_id)


# ── CREW MANAGEMENT ──

class UpdateCrewRequest(BaseModel):
    crew_member_ids: List[str] = Field(..., description="List of emergency_team member UUIDs")
    commander_id: Optional[str] = Field(None, description="New commander UUID (optional)")


class AddCrewMemberRequest(BaseModel):
    team_member_id: str = Field(..., description="Emergency team member UUID to add")


@router.put(
    "/{unit_id}/crew",
    summary="Replace entire crew roster",
    description="Replace all crew members. Used by Edit Configuration → Crew tab."
)
async def update_crew(
    unit_id: str,
    data: UpdateCrewRequest,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyUnitService(db)
    update_data = {"crew_member_ids": data.crew_member_ids}
    if data.commander_id:
        update_data["commander_id"] = data.commander_id
    return await service.update_unit(unit_id, update_data)


@router.post(
    "/{unit_id}/crew",
    summary="Add a crew member",
    description="Add a single crew member to the unit."
)
async def add_crew_member(
    unit_id: str,
    data: AddCrewMemberRequest,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyUnitService(db)
    return await service.add_crew_member(unit_id, data.team_member_id)


@router.delete(
    "/{unit_id}/crew/{member_id}",
    summary="Remove a crew member",
    description="Remove a single crew member from the unit."
)
async def remove_crew_member(
    unit_id: str,
    member_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = EmergencyUnitService(db)
    return await service.remove_crew_member(unit_id, member_id)