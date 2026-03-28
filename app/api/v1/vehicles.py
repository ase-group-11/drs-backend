# File: app/api/v1/vehicles.py
"""
Vehicle Trip Registration API

Citizens register their active trip (current position + destination) so the
reroute service knows which vehicles are in a disaster-affected area.

How it fits into UC7 (Re-Route Traffic):
  1. Mobile app calls POST /vehicles/register when user sets a destination.
  2. ActiveTrip record is stored with a 2-hour expiry.
  3. When a disaster is evaluated, RerouteService calls
     get_users_in_affected_area() which reads from active_trips.
  4. Those users are rerouted / notified.

No auth required — citizens register before or without logging in.
Trip records expire automatically (expires_at column).

Vehicle types:
  general          → standard private vehicle
  public_transport → bus, tram, taxi
  emergency        → reserved for emergency vehicles (not dispatched through this endpoint)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.active_trip import ActiveTrip
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vehicles", tags=["Vehicle Registration"])

VALID_VEHICLE_TYPES = {"general", "public_transport", "emergency"}
TRIP_EXPIRY_HOURS   = 2


# ─────────────────────────────────────────────────────────────────────────────
# Request / response schemas
# ─────────────────────────────────────────────────────────────────────────────

class RegisterVehicleRequest(BaseModel):
    """Body for POST /vehicles/register"""
    user_id:      str   = Field(..., description="User UUID from the auth system")
    current_lat:  float = Field(..., description="Current latitude (WGS-84)")
    current_lng:  float = Field(..., description="Current longitude (WGS-84)")
    dest_lat:     float = Field(..., description="Destination latitude (WGS-84)")
    dest_lng:     float = Field(..., description="Destination longitude (WGS-84)")
    vehicle_type: str   = Field("general", description="general | public_transport | emergency")

    @field_validator("vehicle_type")
    @classmethod
    def validate_vehicle_type(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in VALID_VEHICLE_TYPES:
            raise ValueError(f"vehicle_type must be one of: {sorted(VALID_VEHICLE_TYPES)}")
        return v


class RegisterVehicleResponse(BaseModel):
    user_id:      str
    vehicle_type: str
    dest_lat:     float
    dest_lng:     float
    expires_at:   str
    message:      str


class TripStatusResponse(BaseModel):
    registered:   bool
    user_id:      str
    vehicle_type: Optional[str]  = None
    dest_lat:     Optional[float]= None
    dest_lng:     Optional[float]= None
    expires_at:   Optional[str]  = None


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=RegisterVehicleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register or update an active trip",
)
async def register_vehicle(
    body: RegisterVehicleRequest,
    db: AsyncSession = Depends(get_db),
) -> RegisterVehicleResponse:
    """
    Called when a citizen sets a destination and taps Confirm on the mobile app.
    Upserts — safe to call again when the destination changes mid-journey.

    After registration, this user appears in get_users_in_affected_area()
    if a disaster occurs near their current_lat/lng. The trip expires after
    2 hours and is automatically excluded from future reroute lookups.
    No auth required.
    """
    now        = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=TRIP_EXPIRY_HOURS)

    stmt = pg_insert(ActiveTrip).values(
        user_id=body.user_id,
        current_lat=body.current_lat,
        current_lng=body.current_lng,
        dest_lat=body.dest_lat,
        dest_lng=body.dest_lng,
        vehicle_type=body.vehicle_type,
        registered_at=now,
        expires_at=expires_at,
    ).on_conflict_do_update(
        index_elements=["user_id"],
        set_={
            "current_lat":   body.current_lat,
            "current_lng":   body.current_lng,
            "dest_lat":      body.dest_lat,
            "dest_lng":      body.dest_lng,
            "vehicle_type":  body.vehicle_type,
            "registered_at": now,
            "expires_at":    expires_at,
        },
    )
    await db.execute(stmt)
    await db.flush()

    return RegisterVehicleResponse(
        user_id=body.user_id,
        vehicle_type=body.vehicle_type,
        dest_lat=body.dest_lat,
        dest_lng=body.dest_lng,
        expires_at=expires_at.isoformat(),
        message=f"Trip registered. Valid for {TRIP_EXPIRY_HOURS} hours.",
    )


@router.delete(
    "/register/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Deregister a trip when the user arrives at their destination",
)
async def deregister_vehicle(
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Called when the user arrives or cancels their trip.
    Removes the active_trip record so the user is no longer included in
    reroute lookups.
    No auth required.
    """
    result = await db.execute(
        delete(ActiveTrip).where(ActiveTrip.user_id == user_id)
    )
    await db.flush()
    deregistered = result.rowcount > 0
    return {
        "user_id":      user_id,
        "deregistered": deregistered,
        "message":      "Trip deregistered." if deregistered else "No active trip found.",
    }


@router.get(
    "/register/{user_id}",
    response_model=TripStatusResponse,
    summary="Check if a user has an active trip registered",
)
async def get_registration_status(
    user_id: str,
    db: AsyncSession = Depends(get_db),
) -> TripStatusResponse:
    """
    Called on app startup to restore the 'heading to X' state if the user
    registered in a previous session and the trip hasn't expired yet.
    No auth required.
    """
    now    = datetime.now(timezone.utc)
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