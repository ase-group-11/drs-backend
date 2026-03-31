"""
DROP IN: app/api/v1/vehicles.py

Mobile app calls POST /api/v1/vehicles/register when user sets destination.
Stores lat/lng — no region_id. get_users_in_affected_area() derives the
bounding box from the disaster at query time.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import get_db
from app.db.models.active_trip import ActiveTrip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vehicles", tags=["Vehicle Registration"])

VALID_VEHICLE_TYPES = {"general", "public_transport", "emergency"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegisterVehicleRequest(BaseModel):
    user_id: str = Field(..., description="User UUID from auth system")
    current_lat: float
    current_lng: float
    dest_lat: float
    dest_lng: float
    vehicle_type: str = Field("general", description="general | public_transport | emergency")

    @field_validator("vehicle_type")
    @classmethod
    def validate_vehicle_type(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in VALID_VEHICLE_TYPES:
            raise ValueError(f"vehicle_type must be one of: {sorted(VALID_VEHICLE_TYPES)}")
        return v


class RegisterVehicleResponse(BaseModel):
    user_id: str
    vehicle_type: str
    dest_lat: float
    dest_lng: float
    expires_at: str
    message: str


class TripStatusResponse(BaseModel):
    registered: bool
    user_id: str
    vehicle_type: Optional[str] = None
    dest_lat: Optional[float] = None
    dest_lng: Optional[float] = None
    expires_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/register",
    response_model=RegisterVehicleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register or update a user's active trip",
)
async def register_vehicle(
    body: RegisterVehicleRequest,
    db: AsyncSession = Depends(get_db),
) -> RegisterVehicleResponse:
    """
    Called when user sets a destination and taps Confirm.
    Upserts — safe to call again when destination changes.
    After this, user appears in get_users_in_affected_area()
    if a disaster hits their current position.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(hours=4)

    stmt = (
        pg_insert(ActiveTrip)
        .values(
            user_id=body.user_id,
            current_lat=body.current_lat,
            current_lng=body.current_lng,
            dest_lat=body.dest_lat,
            dest_lng=body.dest_lng,
            vehicle_type=body.vehicle_type,
            expires_at=expires_at,
        )
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "current_lat": body.current_lat,
                "current_lng": body.current_lng,
                "dest_lat": body.dest_lat,
                "dest_lng": body.dest_lng,
                "vehicle_type": body.vehicle_type,
                "expires_at": expires_at,
                "updated_at": datetime.now(timezone.utc),
            },
        )
    )

    await db.execute(stmt)
    await db.commit()

    logger.info(f"register_vehicle: user={body.user_id} type={body.vehicle_type}")

    return RegisterVehicleResponse(
        user_id=body.user_id,
        vehicle_type=body.vehicle_type,
        dest_lat=body.dest_lat,
        dest_lng=body.dest_lng,
        expires_at=expires_at.isoformat(),
        message="Registered. You will be notified if a disaster affects your route.",
    )


@router.delete(
    "/register/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Deregister — user arrived, closed app, or changed destination",
)
async def deregister_vehicle(
    user_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Safe to call even if user was never registered (returns deregistered=False).
    Call before re-registering with a new destination.
    """
    result = await db.execute(
        delete(ActiveTrip).where(ActiveTrip.user_id == user_id)
    )
    await db.commit()
    deregistered = result.rowcount > 0
    logger.info(f"deregister_vehicle: user={user_id} was_registered={deregistered}")
    return {
        "user_id": user_id,
        "deregistered": deregistered,
        "message": "Trip deregistered." if deregistered else "No active trip found.",
    }


@router.get(
    "/register/{user_id}",
    response_model=TripStatusResponse,
    summary="Check if user has an active trip registered",
)
async def get_registration_status(
    user_id: str,
    db: AsyncSession = Depends(get_db),
) -> TripStatusResponse:
    """
    Called on app startup to restore 'heading to X' state
    if the user registered in a previous session and the trip hasn't expired.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ActiveTrip).where(
            ActiveTrip.user_id == user_id,
            ActiveTrip.expires_at > now,
        )
    )
    trip = result.scalar_one_or_none()

    if not trip:
        return TripStatusResponse(registered=False, user_id=user_id)

    return TripStatusResponse(
        registered=True,
        user_id=user_id,
        vehicle_type=trip.vehicle_type,
        dest_lat=trip.dest_lat,
        dest_lng=trip.dest_lng,
        expires_at=trip.expires_at.isoformat(),
    )