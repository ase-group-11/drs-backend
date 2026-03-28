"""
app/services/simulation_service.py

Demo simulation service — moves registered vehicles along their assigned
reroute routes step by step.

How it works:
1. Reads the active reroute plan for a disaster (one DB read)
2. For each vehicle, finds its assigned route geometry (points array)
3. Steps through the waypoints at configurable speed
4. Each step:
   - Writes position to Redis hash (fast, no DB)
   - Publishes vehicle.location_updated via Redis → WebSocket
5. On complete/cancel: batch writes final positions to active_trips (one DB write per vehicle)
6. Deletes Redis key

Speeds:
  slow   — 1 step every 3 seconds
  normal — 1 step every 1.5 seconds
  fast   — 1 step every 0.5 seconds
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import redis as sync_redis
from sqlalchemy import text

from app.core.config import settings
from app.repositories.reroute_repository import RerouteRepository

logger = logging.getLogger(__name__)

REDIS_CHANNEL = "app_alerts"

SPEED_INTERVALS = {
    "slow":   3.0,
    "normal": 1.5,
    "fast":   0.5,
}

# Global registry of running simulation tasks keyed by disaster_id
_running_simulations: Dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

_redis_client: Optional[sync_redis.Redis] = None


def _get_redis() -> sync_redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = sync_redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_keepalive=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )
    return _redis_client


def _publish_sync(payload: dict) -> None:
    try:
        _get_redis().publish(REDIS_CHANNEL, json.dumps(payload, default=str))
    except Exception as exc:
        logger.warning(f"Simulation Redis publish failed: {exc}")


async def _publish(payload: dict) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _publish_sync, payload)


# ---------------------------------------------------------------------------
# DB flush — called once at end of simulation
# ---------------------------------------------------------------------------

async def _flush_to_db(disaster_id: str, redis_key: str) -> None:
    """
    Read final positions from Redis and batch write to active_trips.
    One DB transaction total — not per step.
    """
    try:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None, lambda: _get_redis().hgetall(redis_key)
        )
        if not raw:
            return

        positions = {uid: json.loads(pos) for uid, pos in raw.items()}

        from app.db.session import async_session_factory
        async with async_session_factory() as db:
            for user_id, pos in positions.items():
                await db.execute(
                    text("""
                        UPDATE active_trips
                        SET current_lat = :lat,
                            current_lng = :lng,
                            updated_at  = now()
                        WHERE user_id = :user_id
                    """),
                    {"lat": pos["lat"], "lng": pos["lng"], "user_id": user_id},
                )
            await db.commit()

        logger.info(f"Flush to DB: {len(positions)} vehicle(s) for disaster {disaster_id}")
    except Exception as exc:
        logger.error(f"_flush_to_db failed for {disaster_id}: {exc}")


# ---------------------------------------------------------------------------
# Core simulation loop
# ---------------------------------------------------------------------------

async def _run_simulation(disaster_id: str, interval: float) -> None:
    """
    Background task — steps all vehicles along their routes.
    Uses Redis for position storage during animation.
    Only touches DB twice: once to load the plan, once to flush final positions.
    """
    from app.db.session import async_session_factory

    redis_key = f"sim:vehicle_pos:{disaster_id}"

    try:
        # ── Phase 1: Load plan — one DB read ───────────────────────────────
        async with async_session_factory() as db:
            repo = RerouteRepository(db)
            plan = await repo.get_active_reroute_plan(disaster_id)

        if not plan:
            logger.warning(f"Simulation: no active plan for disaster {disaster_id}")
            return

        route_assignments: Dict[str, str] = plan.get("route_assignments", {})
        chosen_routes: list = plan.get("chosen_routes", [])

        if not route_assignments or not chosen_routes:
            logger.warning(f"Simulation: no route_assignments or routes for {disaster_id}")
            return

        # Build route_id → points lookup
        route_geometry: Dict[str, list] = {}
        for route in chosen_routes:
            rid = route.get("route_id")
            pts = route.get("points", [])
            if rid and pts:
                route_geometry[rid] = pts

        # Build per-vehicle state (pure in-memory)
        vehicle_states: Dict[str, Any] = {}
        for user_id, route_id in route_assignments.items():
            pts = route_geometry.get(route_id, [])
            if pts:
                vehicle_states[user_id] = {
                    "route_id": route_id,
                    "points":   pts,
                    "step":     0,
                    "total":    len(pts),
                    "done":     False,
                }
            else:
                logger.warning(f"Simulation: no geometry for route {route_id} user {user_id}")

        if not vehicle_states:
            logger.warning(f"Simulation: no vehicles with geometry for {disaster_id}")
            return

        logger.info(
            f"Simulation started: disaster={disaster_id} "
            f"vehicles={len(vehicle_states)} interval={interval}s"
        )

        # ── Phase 2: Animation loop — Redis only, zero DB writes ───────────
        loop = asyncio.get_event_loop()

        while True:
            all_done = True
            vehicle_updates = []
            redis_positions = {}

            for user_id, state in vehicle_states.items():
                if state["done"]:
                    continue

                all_done = False
                pts  = state["points"]
                step = state["step"]

                point = pts[step]
                lat = point[0] if isinstance(point, list) else point.get("lat")
                lng = point[1] if isinstance(point, list) else point.get("lng")

                # Write to Redis — not DB
                redis_positions[user_id] = {"lat": lat, "lng": lng}

                progress_pct = round((step / max(state["total"] - 1, 1)) * 100)
                vehicle_updates.append({
                    "user_id":      user_id,
                    "lat":          lat,
                    "lng":          lng,
                    "route_id":     state["route_id"],
                    "progress_pct": progress_pct,
                    "step":         step,
                    "total_steps":  state["total"],
                })

                next_step = step + 1
                if next_step >= state["total"]:
                    state["done"] = True
                    logger.info(f"Simulation: user {user_id} reached destination ({state['total']} steps)")
                else:
                    state["step"] = next_step

            # Atomic Redis pipeline write
            if redis_positions:
                def _write_redis():
                    r = _get_redis()
                    pipe = r.pipeline()
                    pipe.hset(
                        redis_key,
                        mapping={uid: json.dumps(pos) for uid, pos in redis_positions.items()},
                    )
                    pipe.expire(redis_key, 3600)
                    pipe.execute()
                await loop.run_in_executor(None, _write_redis)

            # Broadcast position update to all WebSocket clients
            if vehicle_updates:
                await _publish({
                    "service":    "simulation",
                    "event_type": "vehicle.location_updated",
                    "severity":   "INFO",
                    "colour":     "green",
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "title":      "Vehicle positions updated",
                    "message":    f"{len(vehicle_updates)} vehicle(s) moving",
                    "data": {
                        "disaster_id": disaster_id,
                        "vehicles":    vehicle_updates,
                    },
                    "target_user_ids": None,
                    "target_roles":    None,
                })

            if all_done:
                logger.info(f"Simulation complete: all vehicles reached destination disaster={disaster_id}")
                # ── Phase 3: Final flush to DB — one transaction ────────────
                await _flush_to_db(disaster_id, redis_key)
                await _publish({
                    "service":    "simulation",
                    "event_type": "simulation.complete",
                    "severity":   "INFO",
                    "colour":     "green",
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "title":      "Simulation complete",
                    "message":    "All vehicles have reached their destinations.",
                    "data":       {"disaster_id": disaster_id},
                    "target_user_ids": None,
                    "target_roles":    None,
                })
                break

            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        logger.info(f"Simulation cancelled: disaster={disaster_id}")
        await _flush_to_db(disaster_id, redis_key)
    except Exception as exc:
        logger.exception(f"Simulation error: disaster={disaster_id} — {exc}")
    finally:
        # Always clean up Redis key
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: _get_redis().delete(redis_key))
        except Exception:
            pass
        _running_simulations.pop(disaster_id, None)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def start_simulation(
    disaster_id: str,
    speed: str = "normal",
) -> Dict[str, Any]:
    """Start vehicle movement simulation. Returns immediately — runs in background."""
    if disaster_id in _running_simulations:
        task = _running_simulations[disaster_id]
        if not task.done():
            return {
                "status":      "already_running",
                "disaster_id": disaster_id,
                "message":     "Simulation already running for this disaster.",
            }
        del _running_simulations[disaster_id]

    interval = SPEED_INTERVALS.get(speed, SPEED_INTERVALS["normal"])

    task = asyncio.create_task(
        _run_simulation(disaster_id, interval),
        name=f"sim-{disaster_id}",
    )
    _running_simulations[disaster_id] = task

    return {
        "status":           "started",
        "disaster_id":      disaster_id,
        "speed":            speed,
        "interval_seconds": interval,
        "message":          f"Simulation started at {speed} speed ({interval}s per step).",
    }


async def stop_simulation(disaster_id: str) -> Dict[str, Any]:
    """Cancel a running simulation."""
    task = _running_simulations.pop(disaster_id, None)
    if task and not task.done():
        task.cancel()
        return {
            "status":      "stopped",
            "disaster_id": disaster_id,
            "message":     "Simulation stopped.",
        }
    return {
        "status":      "not_running",
        "disaster_id": disaster_id,
        "message":     "No simulation was running for this disaster.",
    }


def list_simulations() -> Dict[str, str]:
    """Return status of all simulations."""
    return {
        disaster_id: "running" if not task.done() else "finished"
        for disaster_id, task in _running_simulations.items()
    }