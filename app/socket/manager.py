"""
app/socket/manager.py

Socket.IO server setup and room management for the ReRoute Service.

Architecture:
  - One AsyncServer shared across the entire app
  - Mounted as ASGI middleware in main.py alongside FastAPI
  - JWT token validated on every connect (passed as query param or header)
  - Room structure:
      reroute:{region_id}   — all users in a geographic region
      user:{user_id}        — individual user for personalised alerts

Events emitted by MappingService:
  reroute_alert             → room: reroute:{region_id}
  updated_recommendation    → room: reroute:{region_id}
  all_clear                 → room: reroute:{region_id}

Events emitted by NotificationService (teammate):
  reroute_alert             → room: user:{user_id}
  updated_recommendation    → room: user:{user_id}
  all_clear                 → room: user:{user_id}

Client events handled here:
  join_region               → client joins reroute:{region_id}
  leave_region              → client leaves reroute:{region_id}
  connect / disconnect      → auth + cleanup
"""

import logging
from typing import Optional

import socketio
from jose import jwt, JWTError

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared AsyncServer instance
# ---------------------------------------------------------------------------

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",   # TODO: lock down in production
    logger=False,
    engineio_logger=False,
)

# ---------------------------------------------------------------------------
# Connection tracking (in-memory, per-process)
# ---------------------------------------------------------------------------
# sid → {"user_id": str, "region_id": str | None}
_connected_clients: dict[str, dict] = {}


def get_connected_count() -> int:
    return len(_connected_clients)


def get_clients_in_region(region_id: str) -> list[str]:
    """Return list of user_ids currently in a region room."""
    return [
        data["user_id"]
        for data in _connected_clients.values()
        if data.get("region_id") == region_id
    ]


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _extract_user_id(environ: dict) -> Optional[str]:
    """
    Extract and validate JWT from Socket.IO connection params.

    Clients pass token as:
      - Query param:  ?token=<jwt>
      - HTTP header:  Authorization: Bearer <jwt>

    Returns user_id string on success, None on failure.
    """
    # Try query string first
    query_string = environ.get("QUERY_STRING", "")
    token = None

    for part in query_string.split("&"):
        if part.startswith("token="):
            token = part[len("token="):]
            break

    # Fall back to Authorization header
    if not token:
        headers = dict(environ.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        if auth.startswith("Bearer "):
            token = auth[7:]

    if not token:
        return None

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=["HS256"],
        )
        return payload.get("sub")
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Socket.IO event handlers
# ---------------------------------------------------------------------------

@sio.event
async def connect(sid: str, environ: dict, auth: Optional[dict] = None):
    """
    Authenticate client on connect.

    Rejects connection if JWT is missing or invalid.
    Records sid → user_id mapping for later use.
    """
    user_id = _extract_user_id(environ)

    if not user_id:
        logger.warning(f"Socket.IO: rejected unauthenticated connection sid={sid}")
        return False  # Reject connection

    _connected_clients[sid] = {"user_id": user_id, "region_id": None}

    # Auto-join personal room so NotificationService can target this user
    await sio.enter_room(sid, f"user:{user_id}")

    logger.info(f"Socket.IO: connected sid={sid} user={user_id}")


@sio.event
async def disconnect(sid: str):
    """Clean up client tracking on disconnect."""
    client = _connected_clients.pop(sid, {})
    user_id = client.get("user_id", "unknown")
    region_id = client.get("region_id")

    if region_id:
        await sio.leave_room(sid, f"reroute:{region_id}")

    logger.info(f"Socket.IO: disconnected sid={sid} user={user_id}")


@sio.event
async def join_region(sid: str, data: dict):
    """
    Client joins a region room to receive reroute alerts.

    Payload: {"region_id": "region-dublin-m50"}
    """
    region_id = data.get("region_id")
    if not region_id:
        await sio.emit("error", {"message": "region_id required"}, to=sid)
        return

    client = _connected_clients.get(sid, {})

    # Leave previous region if switching
    old_region = client.get("region_id")
    if old_region and old_region != region_id:
        await sio.leave_room(sid, f"reroute:{old_region}")
        logger.debug(f"Socket.IO: sid={sid} left room reroute:{old_region}")

    await sio.enter_room(sid, f"reroute:{region_id}")
    _connected_clients[sid]["region_id"] = region_id

    await sio.emit(
        "joined_region",
        {"region_id": region_id, "status": "joined"},
        to=sid,
    )
    logger.info(
        f"Socket.IO: sid={sid} user={client.get('user_id')} "
        f"joined room reroute:{region_id}"
    )


@sio.event
async def leave_region(sid: str, data: dict):
    """
    Client explicitly leaves a region room.

    Payload: {"region_id": "region-dublin-m50"}
    """
    region_id = data.get("region_id")
    if not region_id:
        return

    await sio.leave_room(sid, f"reroute:{region_id}")

    if _connected_clients.get(sid, {}).get("region_id") == region_id:
        _connected_clients[sid]["region_id"] = None

    logger.info(f"Socket.IO: sid={sid} left room reroute:{region_id}")