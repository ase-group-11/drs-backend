# File: app/api/v1/chat.py
"""
Disaster Group Chat — WebSocket + REST History

─────────────────────────────────────────────────────────────
ARCHITECTURE (Redis-based)
─────────────────────────────────────────────────────────────

Redis serves two purposes:

1. BUFFER (Redis List) — replaces in-memory _message_buffer
   Key: chat_buffer:{disaster_id}
   Each entry: JSON string of one message dict
   - All processes/pods share the same buffer
   - New joiners get buffered + DB messages = complete history
   - Survives server restart (Redis is persistent)

2. PUB/SUB (Redis Channel) — cross-device/cross-process broadcast
   Channel: chat:{disaster_id}
   - When a message is sent, it is published to Redis
   - A background listener subscribes and re-broadcasts
     to all locally connected WebSocket clients
   - Works across different devices, networks, pods

Flow:
  User sends message
        ↓
  1. RPUSH to Redis List (buffer)
  2. PUBLISH to Redis channel (triggers delivery to all devices)
        ↓
  Redis listener receives published message
        ↓
  Broadcasts to all WebSocket clients connected to this process
        ↓
  All devices receive the message instantly ✅

New user joins:
        ↓
  1. Fetch DB chunks (already flushed messages)
  2. Fetch Redis List (recent unflushed messages)
  3. Combine and send as history ✅

Flush (every 90s / 50 messages / last disconnect):
        ↓
  Read all messages from Redis List
  → ONE bulk INSERT to PostgreSQL
  → Delete Redis List
  → 50 messages = 1 DB write ✅

─────────────────────────────────────────────────────────────
ACCESS RULES
─────────────────────────────────────────────────────────────
  ADMIN / MANAGER  → any ACTIVE or MONITORING disaster
  STAFF            → only if deployed to that disaster
  RESOLVED         → WebSocket sends error, history via REST

─────────────────────────────────────────────────────────────
ENDPOINTS
─────────────────────────────────────────────────────────────
  WS  /api/v1/ws/chat/{disaster_id}?token=JWT
  GET /api/v1/chat/{disaster_id}/history
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_handler import decode_token
from app.auth.dependencies import get_current_team_member
from app.db.session import get_db, async_session_factory
from app.core.config import settings

logger = logging.getLogger("chat")

router = APIRouter(tags=["Disaster Chat"])

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

CHUNK_SIZE      = 50    # flush when Redis buffer reaches this many messages
FLUSH_INTERVAL  = 90    # flush every N seconds (1 min 30 sec)
MAX_RETRIES     = 3     # retry DB write this many times before giving up
MAX_BUFFER_SIZE = 500   # hard cap on Redis buffer
WS_TIMEOUT      = 180   # websocket receive timeout (must be > FLUSH_INTERVAL)

REDIS_URL = settings.REDIS_URL

# Redis key patterns
def _buffer_key(disaster_id: str) -> str:
    """Redis List key for buffered messages."""
    return f"chat_buffer:{disaster_id}"

def _pubsub_channel(disaster_id: str) -> str:
    """Redis Pub/Sub channel for real-time delivery."""
    return f"chat:{disaster_id}"

def _seq_key(disaster_id: str) -> str:
    """Redis key for sequence counter."""
    return f"chat_seq:{disaster_id}"

def _chunk_key(disaster_id: str) -> str:
    """Redis key for chunk counter."""
    return f"chat_chunk:{disaster_id}"

# ─────────────────────────────────────────────────────────────────────────────
# In-memory state (per process — WebSocket connections only)
# ─────────────────────────────────────────────────────────────────────────────

# Connected WebSocket clients on THIS process
# { disaster_id → { conn_id → { ws, user_id, sender_name, sender_type } } }
_chat_rooms:        Dict[str, Dict[str, Dict[str, Any]]] = {}

# Flush locks — one per disaster per process
_flush_locks:       Dict[str, asyncio.Lock]               = {}

# Periodic flush task guard — prevents duplicate tasks per process
_periodic_running:  Dict[str, bool]                       = {}

# Redis pub/sub listeners — one per disaster
_chat_listeners:    Dict[str, asyncio.Task]               = {}


def _get_room(disaster_id: str) -> Dict[str, Dict[str, Any]]:
    if disaster_id not in _chat_rooms:
        _chat_rooms[disaster_id] = {}
    return _chat_rooms[disaster_id]


def _get_lock(disaster_id: str) -> asyncio.Lock:
    if disaster_id not in _flush_locks:
        _flush_locks[disaster_id] = asyncio.Lock()
    return _flush_locks[disaster_id]


def get_room_members(disaster_id: str) -> int:
    return len(_chat_rooms.get(disaster_id, {}))


# ─────────────────────────────────────────────────────────────────────────────
# Redis helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_redis() -> aioredis.Redis:
    """Get a fresh Redis client. Each caller manages its own lifecycle."""
    return aioredis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_keepalive=True,
        socket_connect_timeout=5,
    )


async def _redis_push_message(disaster_id: str, msg: Dict[str, Any]) -> int:
    """
    Push one message to the Redis buffer List.
    Returns the new length of the list.
    Falls back to 0 if Redis is unavailable.
    """
    try:
        r = await _get_redis()
        try:
            length = await r.rpush(_buffer_key(disaster_id), json.dumps(msg, default=str))
            return length
        finally:
            await r.aclose()
    except Exception as exc:
        logger.error(f"[Chat] Redis push failed for {disaster_id}: {exc}")
        return 0


async def _redis_get_buffer(disaster_id: str) -> List[Dict[str, Any]]:
    """
    Read ALL messages from the Redis buffer List (without deleting).
    Returns [] if Redis unavailable.
    """
    try:
        r = await _get_redis()
        try:
            raw_list = await r.lrange(_buffer_key(disaster_id), 0, -1)
            return [json.loads(raw) for raw in raw_list]
        finally:
            await r.aclose()
    except Exception as exc:
        logger.error(f"[Chat] Redis get buffer failed for {disaster_id}: {exc}")
        return []


async def _redis_pop_buffer(disaster_id: str) -> List[Dict[str, Any]]:
    """
    Atomically read and delete the Redis buffer List (for flush).
    Uses GETDEL pattern: LRANGE then DELETE in a pipeline.
    """
    try:
        r = await _get_redis()
        try:
            pipe = r.pipeline()
            pipe.lrange(_buffer_key(disaster_id), 0, -1)
            pipe.delete(_buffer_key(disaster_id))
            results = await pipe.execute()
            raw_list = results[0]
            return [json.loads(raw) for raw in raw_list]
        finally:
            await r.aclose()
    except Exception as exc:
        logger.error(f"[Chat] Redis pop buffer failed for {disaster_id}: {exc}")
        return []


async def _redis_buffer_len(disaster_id: str) -> int:
    """Return current buffer length from Redis."""
    try:
        r = await _get_redis()
        try:
            return await r.llen(_buffer_key(disaster_id))
        finally:
            await r.aclose()
    except Exception:
        return 0


async def _redis_next_seq(disaster_id: str) -> int:
    """Atomically increment and return sequence counter from Redis."""
    try:
        r = await _get_redis()
        try:
            return await r.incr(_seq_key(disaster_id))
        finally:
            await r.aclose()
    except Exception as exc:
        logger.error(f"[Chat] Redis seq counter failed: {exc}")
        # Fallback to timestamp-based seq (not perfect but won't crash)
        import time
        return int(time.time() * 1000) % 1000000


async def _redis_next_chunk(disaster_id: str) -> int:
    """Atomically increment and return chunk counter from Redis."""
    try:
        r = await _get_redis()
        try:
            return await r.incr(_chunk_key(disaster_id))
        finally:
            await r.aclose()
    except Exception as exc:
        logger.error(f"[Chat] Redis chunk counter failed: {exc}")
        return 1


async def _redis_publish(disaster_id: str, payload: Dict[str, Any]) -> None:
    """Publish a message to the Redis Pub/Sub channel for this disaster."""
    try:
        r = await _get_redis()
        try:
            await r.publish(
                _pubsub_channel(disaster_id),
                json.dumps(payload, default=str)
            )
        finally:
            await r.aclose()
    except Exception as exc:
        logger.error(f"[Chat] Redis publish failed for {disaster_id}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Redis Pub/Sub listener — one per disaster room
# ─────────────────────────────────────────────────────────────────────────────

async def _chat_redis_listener(disaster_id: str) -> None:
    """
    Background task: subscribes to the Redis Pub/Sub channel for a disaster
    and broadcasts received messages to all locally connected WebSocket clients.

    This is what makes cross-device / cross-process messaging work:
      - Device A on Pod 1 publishes to Redis channel
      - This listener on Pod 2 receives it and sends to Device B
    """
    logger.info(f"[Chat] Redis listener starting for disaster {disaster_id}")
    channel = _pubsub_channel(disaster_id)

    while True:
        client = None
        try:
            client = aioredis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_keepalive=True,
                socket_connect_timeout=5,
                health_check_interval=30,
            )
            pubsub = client.pubsub()
            await pubsub.subscribe(channel)
            logger.info(f"[Chat] Subscribed to Redis channel: {channel}")

            async for raw in pubsub.listen():
                # Stop if room is empty
                if not _chat_rooms.get(disaster_id):
                    logger.info(f"[Chat] Room {disaster_id} empty — stopping Redis listener")
                    break

                if raw is None or raw.get("type") != "message":
                    continue

                try:
                    payload = json.loads(raw["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                # Broadcast to all locally connected WebSocket clients
                await _local_broadcast(disaster_id, payload)

        except asyncio.CancelledError:
            logger.info(f"[Chat] Redis listener cancelled for {disaster_id}")
            if client:
                try:
                    await asyncio.wait_for(client.aclose(), timeout=2.0)
                except Exception:
                    pass
            return
        except Exception as exc:
            logger.error(f"[Chat] Redis listener error for {disaster_id}: {exc} — retrying in 3s")
            await asyncio.sleep(3)
        finally:
            if client:
                try:
                    await asyncio.wait_for(client.aclose(), timeout=2.0)
                except Exception:
                    pass

        # Room is empty — clean up and stop
        if not _chat_rooms.get(disaster_id):
            break

    _chat_listeners.pop(disaster_id, None)
    logger.info(f"[Chat] Redis listener stopped for disaster {disaster_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Local broadcast (to WebSocket clients connected to THIS process)
# ─────────────────────────────────────────────────────────────────────────────

async def _local_broadcast(disaster_id: str, payload: Dict[str, Any]) -> None:
    """Send payload to every WebSocket client connected to this process."""
    room = _chat_rooms.get(disaster_id, {})
    dead: Set[str] = set()

    for conn_id, client in room.items():
        try:
            await client["ws"].send_text(json.dumps(payload, default=str))
        except Exception:
            dead.add(conn_id)

    for conn_id in dead:
        room.pop(conn_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_disaster_status(db: AsyncSession, disaster_id: str) -> Optional[str]:
    result = await db.execute(
        text("SELECT disaster_status FROM disasters WHERE id = :id AND deleted_at IS NULL"),
        {"id": disaster_id},
    )
    row = result.mappings().first()
    return str(row["disaster_status"]) if row else None


async def _get_sender_info(db: AsyncSession, user_id: str) -> Dict[str, str]:
    result = await db.execute(
        text("""
            SELECT full_name, role
            FROM emergency_teams
            WHERE id = :id AND deleted_at IS NULL
        """),
        {"id": user_id},
    )
    row = result.mappings().first()
    if not row:
        return {"full_name": "Unknown", "role": "STAFF"}
    return {"full_name": row["full_name"], "role": str(row["role"])}


async def _is_assigned_to_disaster(
    db: AsyncSession, user_id: str, disaster_id: str
) -> bool:
    result = await db.execute(
        text("""
            SELECT 1
            FROM deployments dep
            JOIN unit_crew uc ON uc.unit_id = dep.unit_id
            WHERE dep.disaster_id           = :disaster_id
              AND uc.team_member_id         = :user_id
              AND dep.deployment_status NOT IN ('COMPLETED', 'CANCELLED')
              AND dep.deleted_at IS NULL
            LIMIT 1
        """),
        {"disaster_id": disaster_id, "user_id": user_id},
    )
    return result.first() is not None


async def _fetch_history(
    db: AsyncSession, disaster_id: str, limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Fetch complete history:
    1. DB chunks (already flushed — persistent)
    2. Redis buffer (recent unflushed — shared across all processes)
    Combines both and returns last N messages.
    """
    # 1. From DB chunks
    result = await db.execute(
        text("""
            SELECT messages, chunk_number
            FROM disaster_chat_sessions
            WHERE disaster_id = :disaster_id
            ORDER BY chunk_number ASC
        """),
        {"disaster_id": disaster_id},
    )
    rows = result.mappings().all()

    all_messages = []
    for row in rows:
        chunk_messages = row["messages"]
        if isinstance(chunk_messages, str):
            chunk_messages = json.loads(chunk_messages)
        for msg in chunk_messages:
            msg["type"] = "message"
            msg["disaster_id"] = disaster_id
            all_messages.append(msg)

    # 2. From Redis buffer (unflushed messages — visible to ALL devices)
    buffered = await _redis_get_buffer(disaster_id)
    for msg in buffered:
        m = dict(msg)
        m["type"] = "message"
        m["disaster_id"] = disaster_id
        all_messages.append(m)

    return all_messages[-limit:]


# ─────────────────────────────────────────────────────────────────────────────
# Bulk flush — Redis buffer → PostgreSQL
# ─────────────────────────────────────────────────────────────────────────────

async def _flush_buffer(disaster_id: str) -> None:
    """
    Flush the Redis buffer to DB as one new chunk.
    Uses asyncio.Lock per disaster to prevent concurrent flushes.
    Retries up to MAX_RETRIES on DB failure.
    If all retries fail, pushes messages back to Redis (not lost).
    """
    async with _get_lock(disaster_id):
        messages = await _redis_pop_buffer(disaster_id)
        if not messages:
            return

        # Hard cap — only flush CHUNK_SIZE at a time
        if len(messages) > CHUNK_SIZE:
            overflow = messages[CHUNK_SIZE:]
            messages = messages[:CHUNK_SIZE]
            # Push overflow back to Redis
            r = await _get_redis()
            try:
                pipe = r.pipeline()
                for msg in overflow:
                    pipe.lpush(_buffer_key(disaster_id), json.dumps(msg, default=str))
                await pipe.execute()
            finally:
                await r.aclose()

        chunk_number = await _redis_next_chunk(disaster_id)
        from_seq     = messages[0]["seq"]
        to_seq       = messages[-1]["seq"]

        for attempt in range(MAX_RETRIES):
            session = async_session_factory()
            try:
                await session.execute(
                    text("""
                        INSERT INTO disaster_chat_sessions
                            (id, disaster_id, chunk_number, from_seq, to_seq, messages)
                        VALUES
                            (:id, :disaster_id, :chunk_number, :from_seq, :to_seq,
                             CAST(:messages AS jsonb))
                    """),
                    {
                        "id":           str(uuid.uuid4()),
                        "disaster_id":  disaster_id,
                        "chunk_number": chunk_number,
                        "from_seq":     from_seq,
                        "to_seq":       to_seq,
                        "messages":     json.dumps(messages, default=str),
                    },
                )
                await session.commit()
                # Clear Redis buffer key and counters after successful DB flush
                try:
                    r = await _get_redis()
                    await r.delete(
                        _buffer_key(disaster_id),
                        _seq_key(disaster_id),
                        _chunk_key(disaster_id),
                    )
                    await r.aclose()
                except Exception as redis_exc:
                    logger.warning(f"[Chat] Redis cleanup failed after flush: {redis_exc}")
                logger.info(
                    f"[Chat] Flushed chunk {chunk_number} for disaster {disaster_id} "
                    f"— {len(messages)} messages (seq {from_seq}-{to_seq})"
                )
                return  # success
            except Exception as exc:
                await session.rollback()
                logger.error(
                    f"[Chat] Flush attempt {attempt + 1}/{MAX_RETRIES} failed "
                    f"for disaster {disaster_id}: {exc}"
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    # All retries failed — push messages back to Redis
                    logger.error(
                        f"[Chat] All retries failed for {disaster_id}. "
                        f"Returning {len(messages)} messages to Redis buffer."
                    )
                    r = await _get_redis()
                    try:
                        pipe = r.pipeline()
                        for msg in reversed(messages):  # preserve order
                            pipe.lpush(_buffer_key(disaster_id), json.dumps(msg, default=str))
                        await pipe.execute()
                    except Exception as re:
                        logger.error(f"[Chat] Failed to restore messages to Redis: {re}")
                    finally:
                        await r.aclose()
                    # Rollback chunk counter
                    try:
                        r2 = await _get_redis()
                        await r2.decr(_chunk_key(disaster_id))
                        await r2.aclose()
                    except Exception:
                        pass
            finally:
                await session.close()


# ─────────────────────────────────────────────────────────────────────────────
# Periodic flush task
# ─────────────────────────────────────────────────────────────────────────────

async def _periodic_flush_task(disaster_id: str) -> None:
    """
    Flush buffer every FLUSH_INTERVAL seconds while room has users.
    Guard prevents duplicate tasks per disaster per process.
    """
    try:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)

            if not _chat_rooms.get(disaster_id):
                logger.info(f"[Chat] Periodic flush stopping — room {disaster_id} empty")
                break

            buffer_len = await _redis_buffer_len(disaster_id)
            if buffer_len > 0:
                logger.info(f"[Chat] Periodic flush for disaster {disaster_id} ({buffer_len} messages)")
                await _flush_buffer(disaster_id)
    finally:
        _periodic_running.pop(disaster_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/ws/chat/{disaster_id}")
async def chat_websocket(disaster_id: str, websocket: WebSocket) -> None:
    """
    Group chat WebSocket. Works across devices and networks via Redis.

    Connect:
      ws://host/api/v1/ws/chat/{disaster_id}?token=<JWT>

    On connect:
      { "type": "history", "messages": [...last 50...], "count": N }

    Send:
      { "message": "Fire contained on north side" }

    Receive:
      { "type": "message", "seq": 1, "sender_name": "...", ... }
      { "type": "system",  "message": "John joined the chat" }
      { "type": "error",   "message": "..." }
      { "type": "ping" }
    """

    # ── 1. Validate JWT ───────────────────────────────────────────────────────
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="token query param required")
        return

    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    user_id   = payload.get("sub")
    user_type = payload.get("user_type", "")

    if user_type != "emergency_team":
        await websocket.close(code=4003, reason="Emergency team access required")
        return

    # ── 2. Validate disaster + access ────────────────────────────────────────
    async with async_session_factory() as db:
        disaster_status = await _get_disaster_status(db, disaster_id)

        if not disaster_status:
            await websocket.close(code=4004, reason="Disaster not found")
            return

        if disaster_status not in ("ACTIVE", "MONITORING"):
            await websocket.accept()
            await websocket.send_text(json.dumps({
                "type":    "error",
                "code":    4009,
                "message": f"Chat is closed — disaster is {disaster_status}. Use history endpoint.",
            }))
            await websocket.close(code=4009)
            return

        sender_info = await _get_sender_info(db, user_id)
        role        = sender_info["role"]
        sender_name = sender_info["full_name"]
        is_admin    = role in ("ADMIN", "MANAGER")

        if not is_admin:
            assigned = await _is_assigned_to_disaster(db, user_id, disaster_id)
            if not assigned:
                await websocket.accept()
                await websocket.send_text(json.dumps({
                    "type":    "error",
                    "code":    4003,
                    "message": "Access denied — you are not assigned to this disaster. Only deployed units can access this chat.",
                }))
                await websocket.close(code=4003)
                return

        sender_type = "admin" if is_admin else "unit"
        history     = await _fetch_history(db, disaster_id, limit=50)

    # ── 3. Accept and register ────────────────────────────────────────────────
    await websocket.accept()

    conn_id = str(uuid.uuid4())
    room    = _get_room(disaster_id)
    room[conn_id] = {
        "ws":          websocket,
        "user_id":     user_id,
        "sender_name": sender_name,
        "sender_type": sender_type,
    }

    logger.info(
        f"[Chat] {sender_name} ({sender_type}) joined disaster {disaster_id}. "
        f"Room size: {len(room)}"
    )

    # Start Redis listener for this disaster (once per process)
    if disaster_id not in _chat_listeners or _chat_listeners[disaster_id].done():
        task = asyncio.create_task(_chat_redis_listener(disaster_id))
        _chat_listeners[disaster_id] = task
        logger.info(f"[Chat] Started Redis listener for disaster {disaster_id}")

    # Start periodic flush task (once per process per disaster)
    if not _periodic_running.get(disaster_id):
        _periodic_running[disaster_id] = True
        asyncio.create_task(_periodic_flush_task(disaster_id))

    try:
        # ── 4. Send history on connect ────────────────────────────────────────
        await websocket.send_text(json.dumps({
            "type":        "history",
            "disaster_id": disaster_id,
            "count":       len(history),
            "messages":    history,
        }, default=str))

        # Notify others via Redis Pub/Sub (reaches all devices)
        join_msg = {
            "type":        "system",
            "disaster_id": disaster_id,
            "message":     f"{sender_name} joined the chat",
            "sent_at":     datetime.utcnow().isoformat(),
        }
        await _redis_publish(disaster_id, join_msg)

        # ── 5. Message loop ───────────────────────────────────────────────────
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=WS_TIMEOUT
                )
            except asyncio.TimeoutError:
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            message_text = (data.get("message") or "").strip()
            if not message_text:
                continue

            # Re-check disaster status
            async with async_session_factory() as db:
                current_status = await _get_disaster_status(db, disaster_id)

            if current_status not in ("ACTIVE", "MONITORING"):
                await websocket.send_text(json.dumps({
                    "type":    "error",
                    "message": f"Chat is now closed — disaster has been {current_status}.",
                }))
                continue

            # Check buffer size
            buf_len = await _redis_buffer_len(disaster_id)
            if buf_len >= MAX_BUFFER_SIZE:
                await websocket.send_text(json.dumps({
                    "type":    "error",
                    "message": "Server buffer is full. Please wait.",
                }))
                continue

            # Build message with Redis sequence number
            msg_id  = str(uuid.uuid4())
            seq     = await _redis_next_seq(disaster_id)
            sent_at = datetime.utcnow().isoformat()

            msg = {
                "id":          msg_id,
                "seq":         seq,
                "sender_id":   user_id,
                "sender_name": sender_name,
                "sender_type": sender_type,
                "message":     message_text,
                "sent_at":     sent_at,
            }

            # Push to Redis buffer (shared across all processes)
            await _redis_push_message(disaster_id, msg)

            # Check if buffer should be flushed
            new_buf_len = await _redis_buffer_len(disaster_id)
            if new_buf_len >= CHUNK_SIZE:
                logger.info(f"[Chat] Buffer full ({new_buf_len}) for disaster {disaster_id} — flushing")
                asyncio.create_task(_flush_buffer(disaster_id))

            # Publish to Redis Pub/Sub — delivers to ALL devices
            await _redis_publish(disaster_id, {
                **msg,
                "type":        "message",
                "disaster_id": disaster_id,
            })

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug(f"[Chat] WS error for {user_id}: {exc}")
    finally:
        room.pop(conn_id, None)

        # Last user left → flush remaining buffer
        if not _chat_rooms.get(disaster_id):
            _chat_rooms.pop(disaster_id, None)
            buf_len = await _redis_buffer_len(disaster_id)
            if buf_len > 0:
                logger.info(
                    f"[Chat] Last user left disaster {disaster_id} — "
                    f"flushing {buf_len} buffered messages"
                )
                await _flush_buffer(disaster_id)

        # Publish leave notification
        await _redis_publish(disaster_id, {
            "type":        "system",
            "disaster_id": disaster_id,
            "message":     f"{sender_name} left the chat",
            "sent_at":     datetime.utcnow().isoformat(),
        })

        logger.info(
            f"[Chat] {sender_name} disconnected from disaster {disaster_id}. "
            f"Room size: {len(_chat_rooms.get(disaster_id, {}))}"
        )


async def _clear_redis_chat_data(disaster_id: str) -> None:
    """Clear all Redis data for a disaster chat."""
    try:
        r = await _get_redis()
        try:
            await r.delete(
                _buffer_key(disaster_id),   # chat_buffer:{id}
                _seq_key(disaster_id),       # chat_seq:{id}
                _chunk_key(disaster_id),     # chat_chunk:{id}
            )
            logger.info(f"[Chat] Redis data cleared for disaster {disaster_id}")
        finally:
            await r.aclose()
    except Exception as exc:
        logger.error(f"[Chat] Failed to clear Redis for {disaster_id}: {exc}")

# ─────────────────────────────────────────────────────────────────────────────
# REST — Chat history
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/chat/{disaster_id}/history",
    summary="Get chat history for a disaster",
)
async def get_chat_history(
    disaster_id: str,
    limit: int = Query(50, ge=1, le=500, description="Number of messages to return (max 500)"),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Fetch chat history (DB chunks + Redis buffer).
    Works for ALL disaster statuses including RESOLVED.
    ADMIN/MANAGER → any disaster. STAFF → only assigned.
    """
    user_id = current_user["user_id"]

    result = await db.execute(
        text("SELECT disaster_status FROM disasters WHERE id = :id AND deleted_at IS NULL"),
        {"id": disaster_id},
    )
    disaster = result.mappings().first()
    if not disaster:
        raise HTTPException(status_code=404, detail="Disaster not found.")

    sender_info = await _get_sender_info(db, user_id)
    is_admin    = sender_info["role"] in ("ADMIN", "MANAGER")

    if not is_admin:
        assigned = await _is_assigned_to_disaster(db, user_id, disaster_id)
        if not assigned:
            raise HTTPException(
                status_code=403,
                detail="Access denied — not assigned to this disaster.",
            )

    messages = await _fetch_history(db, disaster_id, limit=limit)

    return {
        "disaster_id":     disaster_id,
        "disaster_status": str(disaster["disaster_status"]),
        "members_online":  get_room_members(disaster_id),
        "total_messages":  len(messages),
        "messages":        messages,
    }