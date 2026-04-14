"""
DROP IN: app/api/v1/vehicles.py

Mobile app calls POST /api/v1/vehicles/register when user sets destination.
Stores lat/lng — no region_id. get_users_in_affected_area() derives the
bounding box from the disaster at query time.

Late-join reroute notification
──────────────────────────────
After the trip is saved, _notify_if_reroute_active() checks whether the
user's current position falls inside any live reroute plan's impact radius.
If yes, a targeted reroute.triggered alert is published directly to Redis
(target_user_ids = [user_id]) so the WebSocket fan-out reaches only this user.
No new TomTom calls are made — the existing plan's chosen_routes are reused.
"""

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import get_db
from app.db.models.active_trip import ActiveTrip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vehicles", tags=["Vehicle Registration"])

_REDIS_URL     = os.getenv("REDIS_URL", "redis://20.90.162.121:7001")
_REDIS_CHANNEL = "app_alerts"

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
# Late-join reroute helper
# ---------------------------------------------------------------------------

async def _notify_if_reroute_active(
    db: AsyncSession,
    user_id: str,
    user_lat: float,
    user_lng: float,
) -> None:
    """
    Check whether the user's current position falls inside any live reroute
    plan's impact radius.  If so, push a targeted reroute.triggered alert
    directly to Redis so the WebSocket fan-out reaches only this user.

    • Runs as a fire-and-forget asyncio task — never blocks registration.
    • No TomTom calls — reuses chosen_routes already stored in the plan.
    • Only notifies for the closest matching plan (avoids duplicate alerts
      when multiple disasters overlap).
    """
    try:
        result = await db.execute(text("""
            SELECT
                rp.id                                               AS plan_id,
                rp.disaster_id,
                rp.chosen_routes,
                rp.vehicles_affected,
                ST_Y(d.location::geometry)                          AS disaster_lat,
                ST_X(d.location::geometry)                          AS disaster_lon,
                COALESCE(
                    (d.disaster_metadata -> 'evaluation' ->> 'impact_radius_km')::float,
                    3.0
                )                                                   AS radius_km,
                d.tracking_id
            FROM reroute_plans rp
            JOIN disasters d ON rp.disaster_id = d.id
            WHERE rp.status = 'active'
              AND d.disaster_status = 'ACTIVE'
        """))
        plans = result.fetchall()
    except Exception as exc:
        logger.warning(f"[late-join] DB query failed for user={user_id}: {exc}")
        return

    if not plans:
        return

    # Find closest plan within impact radius
    best_plan = None
    best_dist = float("inf")

    for p in plans:
        dlat = math.radians(user_lat - p.disaster_lat)
        dlon = math.radians(user_lng - p.disaster_lon)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(user_lat))
            * math.cos(math.radians(p.disaster_lat))
            * math.sin(dlon / 2) ** 2
        )
        dist_km = 6371.0 * 2 * math.asin(math.sqrt(max(0.0, a)))

        if dist_km <= (p.radius_km or 3.0) and dist_km < best_dist:
            best_dist = dist_km
            best_plan = p

    if best_plan is None:
        return

    # Build the standard reroute.triggered envelope understood by the app,
    # but with target_user_ids so only this user's WebSocket receives it.
    payload = json.dumps(
        {
            "service":    "reroute",
            "event_type": "reroute.triggered",
            "severity":   "HIGH",
            "colour":     "orange",
            "title":      "Active reroute in your area",
            "message":    (
                "A traffic reroute is in effect on your route. "
                "Follow the highlighted alternative path."
            ),
            "data": {
                "disaster_id":      best_plan.disaster_id,
                "plan_id":          best_plan.plan_id,
                "tracking_id":      best_plan.tracking_id,
                "vehicles_count":   best_plan.vehicles_affected or 0,
                "route_assignments": {},      # no personal assignment for late joiners
                "routes":           best_plan.chosen_routes or [],
                "overflow_count":   0,
                "late_join":        True,     # app can distinguish from initial broadcast
                "dist_km":          round(best_dist, 2),
            },
            "target_user_ids": [user_id],     # WebSocket fan-out to this user only
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        default=str,
    )

    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(_REDIS_URL, decode_responses=True)
        await client.publish(_REDIS_CHANNEL, payload)
        await client.aclose()
        logger.info(
            f"[late-join reroute] user={user_id} notified "
            f"plan={best_plan.plan_id} dist={best_dist:.2f} km "
            f"radius={best_plan.radius_km:.1f} km"
        )
    except Exception as exc:
        logger.warning(f"[late-join] Redis publish failed for user={user_id}: {exc}")


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

    After saving the trip, fires a background check for any active reroute
    plan covering the user's current position.  If found, the user receives
    a targeted reroute.triggered WebSocket alert immediately — even if the
    disaster was already active before they registered.
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

    # Fire-and-forget: check for active reroute plans covering this position.
    # Never raises — a failure here must not fail the registration response.
    asyncio.create_task(
        _notify_if_reroute_active(db, body.user_id, body.current_lat, body.current_lng)
    )

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