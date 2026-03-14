# File: app/api/v1/emergency_unit.py
"""
Emergency Unit API — with Bearer token auth.

All endpoints require emergency team Bearer token.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from app.db.session import get_db
from app.auth.dependencies import get_current_team_member
from app.services.emergency_unit_service import EmergencyUnitService

router = APIRouter(prefix="/emergency-units", tags=["Emergency Units"])


# ── Request Models ──

class CreateUnitRequest(BaseModel):
    unit_code: str = Field(...)
    unit_name: str = Field(...)
    unit_type: str = Field(...)
    department: str = Field(...)
    capacity: int = Field(4)
    station_name: str = Field(...)
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


class UpdateCrewRequest(BaseModel):
    crew_member_ids: List[str] = Field(...)
    commander_id: Optional[str] = None


class AddCrewMemberRequest(BaseModel):
    team_member_id: str = Field(...)


# ── STATIC ROUTES FIRST ──

@router.get("/available", summary="List available units for dispatch")
async def list_available_units(
    disaster_id: Optional[str] = None,
    department: Optional[str] = None,
    unit_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = EmergencyUnitService(db)
    units = await service.list_available_units(
        disaster_id=disaster_id, department=department, unit_type=unit_type,
    )
    return {"available_units": units, "count": len(units)}


@router.get("/", summary="List all emergency units")
async def list_units(
    department: Optional[str] = None,
    unit_status: Optional[str] = None,
    unit_type: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = EmergencyUnitService(db)
    return await service.list_units(
        department=department, unit_status=unit_status,
        unit_type=unit_type, search=search, limit=limit,
    )


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create new unit")
async def create_unit(
    data: CreateUnitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = EmergencyUnitService(db)
    return await service.create_unit(data.model_dump())


# ── DYNAMIC ROUTES LAST ──

@router.get("/{unit_id}", summary="Get unit details")
async def get_unit(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = EmergencyUnitService(db)
    return await service.get_unit(unit_id)


@router.put("/{unit_id}", summary="Update unit configuration")
async def update_unit(
    unit_id: str,
    data: UpdateUnitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = EmergencyUnitService(db)
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    return await service.update_unit(unit_id, update_data)


@router.delete("/{unit_id}", summary="Decommission unit")
async def decommission_unit(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = EmergencyUnitService(db)
    return await service.decommission_unit(unit_id)


# ── CREW MANAGEMENT ──

@router.put("/{unit_id}/crew", summary="Replace entire crew roster")
async def update_crew(
    unit_id: str,
    data: UpdateCrewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = EmergencyUnitService(db)
    update_data = {"crew_member_ids": data.crew_member_ids}
    if data.commander_id:
        update_data["commander_id"] = data.commander_id
    return await service.update_unit(unit_id, update_data)


@router.post("/{unit_id}/crew", summary="Add a crew member")
async def add_crew_member(
    unit_id: str,
    data: AddCrewMemberRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = EmergencyUnitService(db)
    return await service.add_crew_member(unit_id, data.team_member_id)


@router.delete("/{unit_id}/crew/{member_id}", summary="Remove a crew member")
async def remove_crew_member(
    unit_id: str,
    member_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    service = EmergencyUnitService(db)
    return await service.remove_crew_member(unit_id, member_id)