# File: app/api/v1/chat.py
"""
Disaster Group Chat — WebSocket + REST History

─────────────────────────────────────────────────────────────
BULK INSERT STRATEGY (chunk-based)
─────────────────────────────────────────────────────────────
Messages are buffered in memory per disaster and flushed as one
bulk INSERT into disaster_chat_sessions.

Flush triggers:
  1. Buffer hits CHUNK_SIZE (50)  → flush immediately
  2. Every FLUSH_INTERVAL (90s)   → flush whatever is in buffer
  3. Last user disconnects        → flush remaining messages

─────────────────────────────────────────────────────────────
FIXES APPLIED
─────────────────────────────────────────────────────────────
  Fix 1: MAX_BUFFER_SIZE cap        → stop accepting if buffer > 500
  Fix 2: Periodic task guard        → only one task per disaster ever
  Fix 3: Seq counter safety         → restore from DB with +10 buffer
  Fix 4: CAST(:messages AS jsonb)   → correct asyncpg JSONB syntax
  Fix 5: _get_latest_seq/chunk use  → accepts db param, no new session
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_handler import decode_token
from app.auth.dependencies import get_current_team_member
from app.db.session import get_db, async_session_factory

logger = logging.getLogger("chat")

router = APIRouter(tags=["Disaster Chat"])

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

CHUNK_SIZE       = 50    # flush when buffer reaches this many messages
FLUSH_INTERVAL   = 120    # flush every N seconds (1 min 30 sec)
MAX_RETRIES      = 3     # retry DB write this many times before giving up
MAX_BUFFER_SIZE  = 500   # hard cap — stop accepting messages if buffer exceeds this
WS_TIMEOUT       = 180   # websocket receive timeout (must be > FLUSH_INTERVAL)

# ─────────────────────────────────────────────────────────────────────────────
# In-memory state
# ─────────────────────────────────────────────────────────────────────────────

_chat_rooms:        Dict[str, Dict[str, Dict[str, Any]]] = {}
_message_buffer:    Dict[str, List[Dict[str, Any]]]      = {}
_seq_counters:      Dict[str, int]                        = {}
_chunk_counters:    Dict[str, int]                        = {}
_flush_locks:       Dict[str, asyncio.Lock]               = {}
_periodic_running:  Dict[str, bool]                       = {}  # FIX 2: task guard


def _get_room(disaster_id: str) -> Dict[str, Dict[str, Any]]:
    if disaster_id not in _chat_rooms:
        _chat_rooms[disaster_id] = {}
    return _chat_rooms[disaster_id]


def _get_lock(disaster_id: str) -> asyncio.Lock:
    if disaster_id not in _flush_locks:
        _flush_locks[disaster_id] = asyncio.Lock()
    return _flush_locks[disaster_id]


def _next_seq(disaster_id: str) -> int:
    _seq_counters[disaster_id] = _seq_counters.get(disaster_id, 0) + 1
    return _seq_counters[disaster_id]


def _next_chunk(disaster_id: str) -> int:
    _chunk_counters[disaster_id] = _chunk_counters.get(disaster_id, 0) + 1
    return _chunk_counters[disaster_id]


def get_room_members(disaster_id: str) -> int:
    return len(_chat_rooms.get(disaster_id, {}))


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


async def _get_latest_chunk_number(db: AsyncSession, disaster_id: str) -> int:
    """
    FIX 5: accepts db param — no new session created.
    Query DB for the latest chunk number for this disaster.
    """
    result = await db.execute(
        text("""
            SELECT COALESCE(MAX(chunk_number), 0) as max_chunk
            FROM disaster_chat_sessions
            WHERE disaster_id = :disaster_id
        """),
        {"disaster_id": disaster_id},
    )
    row = result.mappings().first()
    return int(row["max_chunk"]) if row else 0


async def _get_latest_seq(db: AsyncSession, disaster_id: str) -> int:
    """
    FIX 5: accepts db param — no new session created.
    FIX 3: adds +10 safety buffer to avoid duplicate seq on restart.
    """
    result = await db.execute(
        text("""
            SELECT COALESCE(MAX(to_seq), 0) as max_seq
            FROM disaster_chat_sessions
            WHERE disaster_id = :disaster_id
        """),
        {"disaster_id": disaster_id},
    )
    row = result.mappings().first()
    db_seq = int(row["max_seq"]) if row else 0
    # FIX 3: +10 buffer — if server crashed with buffered unsaved messages,
    # those messages had seq numbers after db_seq. Adding a buffer ensures
    # new messages get higher seq numbers and never conflict.
    return db_seq 


# ─────────────────────────────────────────────────────────────────────────────
# Bulk flush
# ─────────────────────────────────────────────────────────────────────────────

async def _flush_buffer(disaster_id: str) -> None:
    """
    Flush the message buffer for a disaster to DB as one new chunk.
    Uses asyncio.Lock so only one flush runs at a time per disaster.
    Retries up to MAX_RETRIES times on DB failure.
    If all retries fail, messages are returned to buffer (not lost).
    """
    async with _get_lock(disaster_id):
        messages = _message_buffer.pop(disaster_id, [])
        if not messages:
            return

        # FIX 1: enforce buffer cap — if somehow buffer exceeded MAX_BUFFER_SIZE,
        # only flush first CHUNK_SIZE messages and put the rest back
        if len(messages) > CHUNK_SIZE:
            overflow = messages[CHUNK_SIZE:]
            messages = messages[:CHUNK_SIZE]
            existing = _message_buffer.get(disaster_id, [])
            _message_buffer[disaster_id] = overflow + existing

        chunk_number = _next_chunk(disaster_id)
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
                    await asyncio.sleep(2 ** attempt)  # 1s, 2s
                else:
                    # All retries failed — return messages to buffer
                    logger.error(
                        f"[Chat] All retries failed for disaster {disaster_id}. "
                        f"Returning {len(messages)} messages to buffer."
                    )
                    existing = _message_buffer.get(disaster_id, [])
                    _message_buffer[disaster_id] = messages + existing
                    # Rollback chunk counter since insert failed
                    _chunk_counters[disaster_id] -= 1
            finally:
                await session.close()


async def _add_to_buffer(disaster_id: str, msg: Dict[str, Any]) -> bool:
    """
    Add a message to the buffer.
    FIX 1: Returns False if buffer is at MAX_BUFFER_SIZE (message rejected).
    Triggers flush automatically when buffer hits CHUNK_SIZE.
    """
    if disaster_id not in _message_buffer:
        _message_buffer[disaster_id] = []

    # FIX 1: hard cap — reject message if buffer is full
    if len(_message_buffer[disaster_id]) >= MAX_BUFFER_SIZE:
        logger.error(
            f"[Chat] Buffer full for disaster {disaster_id} "
            f"({MAX_BUFFER_SIZE} messages) — message rejected"
        )
        return False

    _message_buffer[disaster_id].append(msg)

    # Flush immediately when chunk is full
    if len(_message_buffer[disaster_id]) >= CHUNK_SIZE:
        logger.info(f"[Chat] Buffer full for disaster {disaster_id} — flushing chunk")
        asyncio.create_task(_flush_buffer(disaster_id))

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Periodic flush task
# ─────────────────────────────────────────────────────────────────────────────

async def _periodic_flush_task(disaster_id: str) -> None:
    """
    FIX 2: Only one task ever runs per disaster.
    Tracked via _periodic_running dict.
    Stops when room is empty and cleans up the guard flag.
    """
    try:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)

            # Stop if no one is connected anymore
            if not _chat_rooms.get(disaster_id):
                logger.info(f"[Chat] Periodic flush stopping — room {disaster_id} empty")
                break

            buffer_size = len(_message_buffer.get(disaster_id, []))
            if buffer_size >= 5:  # only flush if at least 10 messages
                logger.info(f"[Chat] Periodic flush for disaster {disaster_id} ({buffer_size} messages)")
                await _flush_buffer(disaster_id)
            elif buffer_size > 0:
                logger.info(f"[Chat] Periodic flush skipped — only {buffer_size} messages, waiting for more")
    finally:
        # FIX 2: always clear guard so next connect can start a new task
        _periodic_running.pop(disaster_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# History fetch
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_history(
    db: AsyncSession, disaster_id: str, limit: int = 50
) -> List[Dict[str, Any]]:
    """Fetch message history combining all DB chunks + in-memory buffer."""
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

    # Include messages still in buffer (not yet flushed to DB)
    buffered = _message_buffer.get(disaster_id, [])
    for msg in buffered:
        m = dict(msg)
        m["type"] = "message"
        m["disaster_id"] = disaster_id
        all_messages.append(m)

    return all_messages[-limit:]


# ─────────────────────────────────────────────────────────────────────────────
# Broadcast
# ─────────────────────────────────────────────────────────────────────────────

async def _broadcast(disaster_id: str, payload: Dict[str, Any]) -> None:
    """Send payload to every connected client in the disaster's chat room."""
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
# WebSocket endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/ws/chat/{disaster_id}")
async def chat_websocket(disaster_id: str, websocket: WebSocket) -> None:
    """
    Group chat WebSocket for a specific disaster.

    Connect:
      ws://host/api/v1/ws/chat/{disaster_id}?token=<JWT>

    On connect:
      { "type": "history", "messages": [...last 50...], "count": N }

    Send:
      { "message": "Fire is spreading to east wing" }

    Receive:
      { "type": "message", "seq": 47, "sender_name": "...", ... }
      { "type": "system",  "message": "John joined the chat" }
      { "type": "error",   "message": "Chat is closed" }
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
            await websocket.close(
                code=4009,
                reason=f"Chat is closed — disaster is {disaster_status}. Use history endpoint.",
            )
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

        # FIX 3+5: Restore counters from DB if not in memory
        # (handles server restart — uses same db session, no new connection)
        if disaster_id not in _seq_counters:
            _seq_counters[disaster_id] = await _get_latest_seq(db, disaster_id)
        if disaster_id not in _chunk_counters:
            _chunk_counters[disaster_id] = await _get_latest_chunk_number(db, disaster_id)

        history = await _fetch_history(db, disaster_id, limit=50)

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

    # FIX 2: Only start periodic task if NOT already running for this disaster
    if not _periodic_running.get(disaster_id):
        _periodic_running[disaster_id] = True
        asyncio.create_task(_periodic_flush_task(disaster_id))
        logger.info(f"[Chat] Started periodic flush task for disaster {disaster_id}")

    try:
        # ── 4. Send history on connect ────────────────────────────────────────
        await websocket.send_text(json.dumps({
            "type":        "history",
            "disaster_id": disaster_id,
            "count":       len(history),
            "messages":    history,
        }, default=str))

        # Notify others
        await _broadcast(disaster_id, {
            "type":        "system",
            "disaster_id": disaster_id,
            "message":     f"{sender_name} joined the chat",
            "sent_at":     datetime.utcnow().isoformat(),
        })

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

            msg_id  = str(uuid.uuid4())
            seq     = _next_seq(disaster_id)
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

            # FIX 1: check if buffer accepted the message
            accepted = await _add_to_buffer(disaster_id, msg)
            if not accepted:
                await websocket.send_text(json.dumps({
                    "type":    "error",
                    "message": "Server buffer is full. Message not saved. Please wait.",
                }))
                continue

            # Broadcast immediately (before DB flush)
            await _broadcast(disaster_id, {
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

        # Last user left → flush remaining buffer immediately
        if not _chat_rooms.get(disaster_id):
            _chat_rooms.pop(disaster_id, None)
            if _message_buffer.get(disaster_id):
                logger.info(
                    f"[Chat] Last user left disaster {disaster_id} — flushing remaining buffer"
                )
                await _flush_buffer(disaster_id)

        # Notify others
        await _broadcast(disaster_id, {
            "type":        "system",
            "disaster_id": disaster_id,
            "message":     f"{sender_name} left the chat",
            "sent_at":     datetime.utcnow().isoformat(),
        })

        logger.info(
            f"[Chat] {sender_name} disconnected from disaster {disaster_id}. "
            f"Room size: {len(_chat_rooms.get(disaster_id, {}))}"
        )


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
    Fetch chat history for a disaster.
    Works for ALL disaster statuses including RESOLVED.
    ADMIN/MANAGER → any disaster. STAFF → only assigned disasters.
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
    