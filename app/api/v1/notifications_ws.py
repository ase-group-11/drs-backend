# File: app/api/v1/notifications_ws.py
"""
Authenticated WebSocket Notification Endpoint + Redis pub/sub listener.

Authentication Flow:
  Client sends JWT token as a query parameter:
    ws://host/api/v1/ws/notifications?token=<access_token>

  On connect the server:
    1. Validates the JWT token (closes with 4001 if invalid/expired)
    2. Fetches user's real phone + email from DB (users or emergency_teams table)
    3. Stores user profile in connected_clients registry
    4. Flushes any queued offline alerts
    5. Stores user location in Redis (geo targeting)

Heartbeat (client → server every 3 min):
    {"type": "ping", "lat": 53.34, "lon": -6.26}
    Note: user_id is NO LONGER needed in the frame — it comes from the token.

Alert envelope delivered to client:
{
  "service":    "disaster" | "reroute" | "coordination",
  "event_type": "disaster.dispatched" | ...,
  "severity":   "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO",
  "colour":     "red" | "orange" | "yellow" | "blue" | "green",
  "title":      "...",
  "message":    "...",
  "data":       { ... },
  "timestamp":  "ISO-8601"
}

The consumer now also receives real phone/email per connected user so it can
route SMS/Email directly from the notification_consumer without the deployment
service having to pass those fields through RabbitMQ.

Python 3.9 compatible.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Set

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_handler import decode_token
from app.core.config import settings
from app.db.session import get_db                   # FastAPI DB dependency
from app.services.location_registry import (
    delete_user_location,
    flush_offline_alerts,
    set_user_location,
)

logger = logging.getLogger("notifications_ws")
router  = APIRouter(tags=["Notifications"])

REDIS_URL     = settings.REDIS_URL
REDIS_CHANNEL = "app_alerts"

# ── Connected client registry ─────────────────────────────────
# user_id → { "ws": WebSocket, "phone": str, "email": str,
#              "full_name": str, "user_type": str }
connected_clients: Dict[str, Dict[str, Any]] = {}


# ── DB helpers (run inside a fresh async session) ─────────────

async def _fetch_user_profile(user_id: str, user_type: str) -> Dict[str, Any]:
    """
    Fetch phone_number and email from DB based on user_type.
    Returns dict with phone, email, full_name. Empty strings if not found.
    """
    profile = {"phone": "", "email": "", "full_name": ""}
    try:
        # Pull one session from the get_db generator (Python 3.9 compatible)
        gen = get_db()
        db = await gen.__anext__()
        try:
            if user_type == "emergency_team":
                row = await db.execute(
                    text("""
                        SELECT phone_number, email, full_name
                        FROM emergency_teams
                        WHERE id = :uid AND deleted_at IS NULL
                    """),
                    {"uid": user_id},
                )
            else:
                row = await db.execute(
                    text("""
                        SELECT phone_number, email, full_name
                        FROM users
                        WHERE id = :uid AND deleted_at IS NULL
                    """),
                    {"uid": user_id},
                )
            r = row.mappings().first()
            if r:
                profile["phone"]     = r["phone_number"] or ""
                profile["email"]     = r["email"] or ""
                profile["full_name"] = r["full_name"] or ""
        finally:
            await db.close()
            try:
                await gen.aclose()
            except Exception:
                pass
    except Exception as exc:
        logger.error(f"_fetch_user_profile({user_id}): {exc}")
    return profile


# ── Targeted broadcaster ──────────────────────────────────────

async def broadcast_to_users(
    message: str,
    target_user_ids: Optional[Set[str]],
    target_roles: Optional[Set[str]] = None,
) -> None:
    """
    Send message to targeted users with optional role filtering.

    Targeting rules:
    - If target_roles is set, all connected users whose user_type is in
      target_roles receive the message regardless of geo-targeting.
    - Geo-targeted users (target_user_ids) always receive the message.
    - If target_user_ids is None → broadcast to everyone.

    Note: "admin" is not a JWT user_type. EmergencyTeamRole.ADMIN members
    connect with user_type="emergency_team" — pass target_roles={"emergency_team"}
    to reach them.
    """
    dead: Set[str] = set()
    pairs: list = []

    for uid, info in connected_clients.items():
        user_type = info.get("user_type", "user")

        # Role-targeted — include regardless of geo
        if target_roles and user_type in target_roles:
            pairs.append((uid, info))
            continue

        # Geo-targeted or broadcast
        if target_user_ids is None or uid in target_user_ids:
            pairs.append((uid, info))

    for uid, info in pairs:
        try:
            await info["ws"].send_text(message)
        except Exception:
            dead.add(uid)

    for uid in dead:
        connected_clients.pop(uid, None)

    if pairs:
        delivered = len(pairs) - len(dead)
        logger.info(
            f"Delivered to {delivered}/{len(pairs)} "
            f"({'broadcast' if target_user_ids is None else 'targeted'})"
        )


# ── Redis listener (started once in main.py lifespan) ─────────

async def redis_listener() -> None:
    """
    Background task: subscribes to Redis app_alerts channel and
    routes each alert to the correct connected clients.

    Uses a health_check_interval to keep the connection alive during
    idle periods — prevents the remote Redis server from dropping the
    TCP connection after 60s of inactivity (which causes missed messages).
    """
    logger.info(f"Redis listener starting — channel: {REDIS_CHANNEL}")
    while True:
        client = None
        try:
            client = aioredis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_keepalive=True,
                socket_connect_timeout=5,
                socket_timeout=10,
                health_check_interval=30,  # ping Redis every 30s to keep alive
            )
            pubsub = client.pubsub()
            await pubsub.subscribe(REDIS_CHANNEL)
            logger.info(f"Subscribed to Redis channel: {REDIS_CHANNEL}")

            while True:
                # Use get_message with timeout instead of async for
                # so we can send keepalive pings and detect disconnects
                try:
                    raw = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    # No message in 5s — send a keepalive ping
                    try:
                        await client.ping()
                    except Exception:
                        logger.warning("Redis keepalive ping failed — reconnecting")
                        break  # exit inner loop → reconnect
                    continue

                if raw is None:
                    continue

                try:
                    payload = json.loads(raw["data"])
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue

                # Strip targeting fields before forwarding to clients
                target_list  = payload.pop("target_user_ids", None)
                target_roles = payload.pop("target_roles", None)
                target_ids   = set(target_list) if target_list is not None else None
                role_set     = set(target_roles) if target_roles is not None else None

                await broadcast_to_users(
                    json.dumps(payload, default=str), target_ids, role_set
                )

        except asyncio.CancelledError:
            logger.info("Redis listener cancelled — shutting down")
            return
        except Exception as exc:
            logger.error(f"Redis listener error: {exc} — retrying in 3s")
            await asyncio.sleep(3)
        finally:
            # Always clean up the client on exit from the outer try
            if client:
                try:
                    await client.aclose()
                except Exception:
                    pass


# ── WebSocket endpoint ────────────────────────────────────────

@router.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket) -> None:
    """
    Authenticated real-time notification WebSocket.

    Connect:
        ws://localhost:8000/api/v1/ws/notifications?token=<JWT_access_token>

    After connecting, optionally send your GPS location (for geo targeting):
        {"lat": 53.3498, "lon": -6.2603}

    Heartbeat every 3 minutes to keep location fresh:
        {"type": "ping", "lat": 53.3498, "lon": -6.2603}

    The server will:
      - Reject connections without a valid token (close code 4001)
      - Reject expired tokens (close code 4001)
      - Fetch your real phone/email from the DB for SMS/Email delivery
      - Flush any alerts you missed while offline
    """
    # ── Step 1: Extract and validate JWT from query param ─────
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="token query param required")
        return

    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    user_id   = payload.get("sub")
    user_type = payload.get("user_type", "user")   # "user" or "emergency_team"

    if not user_id:
        await websocket.close(code=4001, reason="Token missing user identity")
        return

    await websocket.accept()
    logger.info(f"[{user_id}] ({user_type}) WS accepted. Fetching profile...")

    # ── Step 2: Fetch real phone + email from DB ──────────────
    profile = await _fetch_user_profile(user_id, user_type)
    logger.info(
        f"[{user_id}] Profile: name={profile['full_name']} "
        f"phone={profile['phone']} email={profile['email']}"
    )

    # ── Step 3: Register in connected_clients ─────────────────
    connected_clients[user_id] = {
        "ws":        websocket,
        "phone":     profile["phone"],
        "email":     profile["email"],
        "full_name": profile["full_name"],
        "user_type": user_type,
    }
    logger.info(f"[{user_id}] connected. Active: {len(connected_clients)}")

    try:
        # ── Step 4: Read initial location frame (optional, 15s timeout) ──
        try:
            raw  = await asyncio.wait_for(websocket.receive_text(), timeout=15)
            init = json.loads(raw)
            # Accept both {"lat":..,"lon":..} and {"type":"ping","lat":..,"lon":..}
            lat = init.get("lat")
            lon = init.get("lon")
            if lat is not None and lon is not None:
                set_user_location(user_id, float(lat), float(lon))
                logger.info(f"[{user_id}] Location set: lat={lat} lon={lon}")
        except asyncio.TimeoutError:
            # Location frame is optional — no location means no geo targeting
            # but direct subscriptions still work
            logger.info(f"[{user_id}] No location frame within 15s (geo targeting disabled)")
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning(f"[{user_id}] Location frame parse error: {exc}")

        # ── Step 5: Flush offline alerts ──────────────────────
        pending = flush_offline_alerts(user_id)
        stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        fresh = []
        for alert in pending:
            ts = alert.get("timestamp")
            if ts:
                try:
                    alert_time = datetime.fromisoformat(ts)
                    # Make offset-aware if needed
                    if alert_time.tzinfo is None:
                        alert_time = alert_time.replace(tzinfo=timezone.utc)
                    if alert_time < stale_cutoff:
                        logger.info(
                            f"[{user_id}] Discarding stale offline alert "
                            f"event={alert.get('event_type')} age={(datetime.now(timezone.utc) - alert_time).seconds}s"
                        )
                        continue
                except Exception:
                    pass  # if timestamp unparseable, deliver anyway
            fresh.append(alert)

        for alert in fresh:
            try:
                await websocket.send_text(json.dumps(alert, default=str))
            except Exception:
                break
        if pending:
            logger.info(
                f"[{user_id}] Flushed {len(fresh)}/{len(pending)} offline alert(s) "
                f"({len(pending) - len(fresh)} stale discarded)"
            )

        # ── Step 6: Keep-alive / heartbeat loop ───────────────
        while True:
            try:
                raw   = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                frame = json.loads(raw)
                if frame.get("type") == "ping":
                    plat = frame.get("lat")
                    plon = frame.get("lon")
                    if plat is not None and plon is not None:
                        set_user_location(user_id, float(plat), float(plon))
                    # Send pong back to confirm receipt
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # No message from client in 30s — server sends ping to keep alive
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break  # client gone
            except (json.JSONDecodeError, Exception):
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug(f"[{user_id}] WS error: {exc}")
    finally:
        connected_clients.pop(user_id, None)
        delete_user_location(user_id)
        logger.info(f"[{user_id}] disconnected. Active: {len(connected_clients)}")


# ── Public helper for notification_consumer ───────────────────

def get_user_contact(user_id: str) -> Dict[str, str]:
    """
    Return the phone and email for a currently-connected user.
    Used by notification_consumer to get real contact details
    without querying the DB again.

    Returns {"phone": "", "email": ""} if user is offline.
    """
    info = connected_clients.get(user_id)
    if not info:
        return {"phone": "", "email": ""}
    return {"phone": info.get("phone", ""), "email": info.get("email", "")}