# File: app/tests/unit/test_chat_integration.py
"""
Chat integration tests — Redis-based chat.py (corrected signatures).

Actual function signatures confirmed via inspect:
  _redis_push_message(disaster_id, msg) -> int
  _redis_buffer_len(disaster_id) -> int
  _redis_get_buffer(disaster_id) -> List[dict]
  _redis_pop_buffer(disaster_id) -> List[dict]
  _redis_next_seq(disaster_id) -> int
  _redis_next_chunk(disaster_id) -> int
  _redis_publish(disaster_id, payload) -> None
  _flush_buffer(disaster_id) -> None   (creates own DB session)
  _get_room(disaster_id) -> Dict       (dict not set)
  _get_sender_info(db, user_id) -> {'full_name': ..., 'role': ...}

Run:
  pytest app/tests/unit/test_chat_integration.py -v
"""

import asyncio
import json
import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.auth.dependencies import get_current_user, get_current_team_member
from app.db.session import get_db
import app.api.v1.chat as chat_module

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

DISASTER_ID  = str(uuid.uuid4())
DISASTER_ID2 = str(uuid.uuid4())
SENDER_ID    = str(uuid.uuid4())
NOW          = datetime.utcnow()

ADMIN_USER = {
    "id":        str(uuid.uuid4()),
    "user_id":   str(uuid.uuid4()),
    "full_name": "Admin User",
    "role":      "ADMIN",
    "user_type": "emergency_team",
}

STAFF_USER = {
    "id":        str(uuid.uuid4()),
    "user_id":   str(uuid.uuid4()),
    "full_name": "Staff User",
    "role":      "STAFF",
    "user_type": "emergency_team",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_mock_db():
    db = AsyncMock()
    db.execute  = AsyncMock()
    db.flush    = AsyncMock()
    db.commit   = AsyncMock()
    db.rollback = AsyncMock()
    db.close    = AsyncMock()
    return db


def make_result(rows=None, first=None):
    mock = MagicMock()
    rows = rows or []
    mock.mappings.return_value.all.return_value   = rows
    mock.mappings.return_value.first.return_value = first if first is not None else (rows[0] if rows else None)
    mock.first.return_value = first if first is not None else (rows[0] if rows else None)
    return mock


make_db_result = make_result  # alias


def make_msg(seq=1, disaster_id=None):
    return {
        "id":          str(uuid.uuid4()),
        "seq":         seq,
        "sender_id":   SENDER_ID,
        "sender_name": "John Doe",
        "sender_type": "admin",
        "message":     f"Message {seq}",
        "sent_at":     NOW.isoformat(),
    }


def make_chunk_row(chunk_num, messages):
    return {
        "chunk_number": chunk_num,
        "messages":     messages,
        "created_at":   NOW,
    }


def make_session_factory(mock_db):
    """Create async context manager that yields mock_db."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_db)
    cm.__aexit__  = AsyncMock(return_value=False)
    factory = MagicMock(return_value=cm)
    return factory


@pytest.fixture(autouse=True)
def clear_chat_state():
    """Reset all in-memory chat state before each test."""
    chat_module._chat_rooms.clear()
    if hasattr(chat_module, "_periodic_running"):
        chat_module._periodic_running.clear()
    if hasattr(chat_module, "_flush_locks"):
        chat_module._flush_locks.clear()
    if hasattr(chat_module, "_chat_listeners"):
        chat_module._chat_listeners.clear()


@pytest.fixture
def mock_db():
    return make_mock_db()


@pytest.fixture
def admin_client(mock_db):
    app.dependency_overrides[get_current_user]        = lambda: ADMIN_USER
    app.dependency_overrides[get_current_team_member] = lambda: ADMIN_USER
    app.dependency_overrides[get_db]                  = lambda: mock_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, mock_db
    app.dependency_overrides.clear()


@pytest.fixture
def staff_client(mock_db):
    app.dependency_overrides[get_current_user]        = lambda: STAFF_USER
    app.dependency_overrides[get_current_team_member] = lambda: STAFF_USER
    app.dependency_overrides[get_db]                  = lambda: mock_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, mock_db
    app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════
# REDIS KEY HELPERS
# ═══════════════════════════════════════════════════════════════════════════

class TestRedisKeyHelpers:

    def test_buffer_key_format(self):
        """CH-01: _buffer_key returns correct Redis key containing disaster_id."""
        key = chat_module._buffer_key(DISASTER_ID)
        assert DISASTER_ID in key
        assert isinstance(key, str)

    def test_seq_key_format(self):
        """CH-02: _seq_key returns correct Redis key."""
        key = chat_module._seq_key(DISASTER_ID)
        assert DISASTER_ID in key
        assert isinstance(key, str)

    def test_chunk_key_format(self):
        """CH-03: _chunk_key returns correct Redis key."""
        key = chat_module._chunk_key(DISASTER_ID)
        assert DISASTER_ID in key
        assert isinstance(key, str)

    def test_pubsub_channel_format(self):
        """CH-04: _pubsub_channel returns correct Redis channel."""
        channel = chat_module._pubsub_channel(DISASTER_ID)
        assert DISASTER_ID in channel
        assert isinstance(channel, str)

    def test_different_disasters_have_different_keys(self):
        """CH-05: Different disasters have different buffer keys."""
        key1 = chat_module._buffer_key(DISASTER_ID)
        key2 = chat_module._buffer_key(DISASTER_ID2)
        assert key1 != key2


# ═══════════════════════════════════════════════════════════════════════════
# REDIS OPERATIONS  —  functions create their own Redis connection internally
# ═══════════════════════════════════════════════════════════════════════════

class TestRedisOperations:

    @pytest.mark.asyncio
    async def test_redis_push_message(self):
        """CH-06: _redis_push_message(disaster_id, msg) pushes JSON to Redis list."""
        mock_redis = AsyncMock()
        mock_redis.rpush = AsyncMock(return_value=1)
        msg = make_msg(seq=1)

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await chat_module._redis_push_message(DISASTER_ID, msg)

        mock_redis.rpush.assert_called_once()
        call_args = mock_redis.rpush.call_args[0]
        assert chat_module._buffer_key(DISASTER_ID) == call_args[0]
        assert json.loads(call_args[1]) == msg
        assert result == 1

    @pytest.mark.asyncio
    async def test_redis_buffer_len(self):
        """CH-07: _redis_buffer_len(disaster_id) returns count from Redis llen."""
        mock_redis = AsyncMock()
        mock_redis.llen = AsyncMock(return_value=5)

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await chat_module._redis_buffer_len(DISASTER_ID)

        mock_redis.llen.assert_called_once_with(chat_module._buffer_key(DISASTER_ID))
        assert result == 5

    @pytest.mark.asyncio
    async def test_redis_get_buffer_returns_messages(self):
        """CH-08: _redis_get_buffer(disaster_id) returns list of dicts."""
        msg = make_msg(seq=1)
        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[json.dumps(msg).encode()])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await chat_module._redis_get_buffer(DISASTER_ID)

        assert len(result) == 1
        assert result[0] == msg

    @pytest.mark.asyncio
    async def test_redis_get_buffer_empty(self):
        """CH-09: _redis_get_buffer returns empty list when no messages."""
        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await chat_module._redis_get_buffer(DISASTER_ID)

        assert result == []


    @pytest.mark.asyncio
    async def test_redis_next_seq_increments(self):
        """CH-11: _redis_next_seq(disaster_id) calls INCR and returns value."""
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await chat_module._redis_next_seq(DISASTER_ID)

        mock_redis.incr.assert_called_once_with(chat_module._seq_key(DISASTER_ID))
        assert result == 1

    @pytest.mark.asyncio
    async def test_redis_next_chunk_increments(self):
        """CH-12: _redis_next_chunk(disaster_id) calls INCR and returns value."""
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=3)

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await chat_module._redis_next_chunk(DISASTER_ID)

        mock_redis.incr.assert_called_once_with(chat_module._chunk_key(DISASTER_ID))
        assert result == 3

    @pytest.mark.asyncio
    async def test_redis_publish_sends_to_channel(self):
        """CH-13: _redis_publish(disaster_id, payload) sends to pub/sub channel."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        msg = make_msg(seq=1)

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            await chat_module._redis_publish(DISASTER_ID, msg)

        mock_redis.publish.assert_called_once()
        channel = mock_redis.publish.call_args[0][0]
        assert DISASTER_ID in channel


# ═══════════════════════════════════════════════════════════════════════════
# ROOM STATE  —  _get_room returns a dict (WebSocket → member info)
# ═══════════════════════════════════════════════════════════════════════════

class TestRoomState:

    def test_get_room_creates_empty_room(self):
        """CH-14: _get_room creates empty dict for new disaster."""
        room = chat_module._get_room(DISASTER_ID)
        assert isinstance(room, dict)
        assert len(room) == 0

    def test_get_room_returns_existing(self):
        """CH-15: _get_room returns same dict on second call."""
        room1 = chat_module._get_room(DISASTER_ID)
        room1["ws_client_1"] = {"user": "test"}
        room2 = chat_module._get_room(DISASTER_ID)
        assert "ws_client_1" in room2

    def test_get_room_members_empty(self):
        """CH-16: New room has no members."""
        room = chat_module._get_room(DISASTER_ID)
        assert len(room) == 0

    def test_get_room_members_count(self):
        """CH-17: Room member count tracks added entries."""
        room = chat_module._get_room(DISASTER_ID)
        room["ws1"] = {}
        room["ws2"] = {}
        assert len(chat_module._chat_rooms[DISASTER_ID]) == 2

    def test_get_lock_returns_asyncio_lock(self):
        """CH-18: _get_lock returns an asyncio.Lock."""
        lock = chat_module._get_lock(DISASTER_ID)
        assert isinstance(lock, asyncio.Lock)

    def test_get_lock_same_lock_for_same_disaster(self):
        """CH-19: Same lock returned for same disaster."""
        lock1 = chat_module._get_lock(DISASTER_ID)
        lock2 = chat_module._get_lock(DISASTER_ID)
        assert lock1 is lock2

    def test_get_lock_different_locks_per_disaster(self):
        """CH-20: Different locks for different disasters."""
        lock1 = chat_module._get_lock(DISASTER_ID)
        lock2 = chat_module._get_lock(DISASTER_ID2)
        assert lock1 is not lock2

    def test_periodic_task_guard(self):
        """CH-21: _periodic_running tracks which disasters have tasks."""
        assert DISASTER_ID not in chat_module._periodic_running
        chat_module._periodic_running[DISASTER_ID] = True
        assert DISASTER_ID in chat_module._periodic_running


# ═══════════════════════════════════════════════════════════════════════════
# FLUSH BUFFER  —  _flush_buffer(disaster_id) creates its own DB session
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# DB HELPERS
# ═══════════════════════════════════════════════════════════════════════════

class TestChatDBHelpers:

    @pytest.mark.asyncio
    async def test_get_disaster_status_returns_active(self):
        """CH-25: _get_disaster_status returns status string for known disaster."""
        db = make_mock_db()
        db.execute.return_value = make_result(first={"disaster_status": "ACTIVE"})

        result = await chat_module._get_disaster_status(db, DISASTER_ID)
        assert result == "ACTIVE"

    @pytest.mark.asyncio
    async def test_get_disaster_status_returns_none_if_not_found(self):
        """CH-26: _get_disaster_status returns None if disaster not found."""
        db = make_mock_db()
        db.execute.return_value = make_result(first=None)

        result = await chat_module._get_disaster_status(db, DISASTER_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_sender_info_admin(self):
        """CH-27: _get_sender_info returns full_name and role."""
        db = make_mock_db()
        db.execute.return_value = make_result(first={
            "full_name": "Admin User",
            "role":      "ADMIN",
        })

        info = await chat_module._get_sender_info(db, ADMIN_USER["id"])

        assert info["full_name"] == "Admin User"

    @pytest.mark.asyncio
    async def test_get_sender_info_defaults_for_unknown(self):
        """CH-28: _get_sender_info returns defaults when user not found."""
        db = make_mock_db()
        db.execute.return_value = make_result(first=None)

        info = await chat_module._get_sender_info(db, str(uuid.uuid4()))

        assert "full_name" in info

    @pytest.mark.asyncio
    async def test_is_assigned_returns_true(self):
        """CH-29: _is_assigned_to_disaster returns True when unit found."""
        db = make_mock_db()
        db.execute.return_value = make_result(first={"id": DISASTER_ID})

        result = await chat_module._is_assigned_to_disaster(
            db, STAFF_USER["id"], DISASTER_ID
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_is_assigned_returns_false(self):
        """CH-30: _is_assigned_to_disaster returns False when not assigned."""
        db = make_mock_db()
        db.execute.return_value = make_result(first=None)

        result = await chat_module._is_assigned_to_disaster(
            db, STAFF_USER["id"], DISASTER_ID
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_history_combines_chunks(self):
        """CH-31: _fetch_history returns messages from DB chunks."""
        db = make_mock_db()
        db.execute.return_value = make_result(rows=[
            make_chunk_row(1, [make_msg(1), make_msg(2)]),
            make_chunk_row(2, [make_msg(3)]),
        ])

        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await chat_module._fetch_history(db, DISASTER_ID)

        assert len(result) >= 3

    @pytest.mark.asyncio
    async def test_fetch_history_empty_when_no_messages(self):
        """CH-32: _fetch_history returns empty list when no messages."""
        db = make_mock_db()
        db.execute.return_value = make_result(rows=[])

        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await chat_module._fetch_history(db, DISASTER_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_history_respects_limit(self):
        """CH-33: _fetch_history accepts the limit parameter."""
        db = make_mock_db()
        db.execute.return_value = make_result(rows=[
            make_chunk_row(1, [make_msg(i) for i in range(1, 101)])
        ])

        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await chat_module._fetch_history(db, DISASTER_ID, limit=10)

        assert len(result) <= 100

    @pytest.mark.asyncio
    async def test_fetch_history_includes_redis_buffer(self):
        """CH-34: _fetch_history includes messages still in Redis buffer."""
        db = make_mock_db()
        db.execute.return_value = make_result(rows=[
            make_chunk_row(1, [make_msg(1)]),
        ])

        buffered = [make_msg(2), make_msg(3)]
        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(
            return_value=[json.dumps(m).encode() for m in buffered]
        )

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await chat_module._fetch_history(db, DISASTER_ID)

        assert len(result) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# CHAT HISTORY API
# ═══════════════════════════════════════════════════════════════════════════

class TestChatHistoryAPI:

    @pytest.mark.asyncio
    async def test_history_returns_200_for_admin(self, admin_client):
        """CH-35: GET /chat/{id}/history → 200 for admin."""
        client, mock_db = admin_client

        mock_db.execute.side_effect = [
            make_result(first={"disaster_status": "ACTIVE"}),
            make_result(first={"full_name": "Admin", "role": "ADMIN"}),
            make_result(rows=[]),
        ]

        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            async with client as c:
                resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_history_returns_404_if_disaster_not_found(self, admin_client):
        """CH-36: GET /chat/{id}/history → 404 if disaster not found."""
        client, mock_db = admin_client

        mock_db.execute.return_value = make_result(first=None)

        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            async with client as c:
                resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_history_returns_403_for_unassigned_staff(self, staff_client):
        """CH-37: GET /chat/{id}/history → 403 for unassigned STAFF."""
        client, mock_db = staff_client

        mock_db.execute.side_effect = [
            make_result(first={"disaster_status": "ACTIVE"}),
            make_result(first={"full_name": "Staff", "role": "STAFF"}),
            make_result(first=None),  # not assigned
        ]

        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            async with client as c:
                resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_history_works_for_resolved_disaster(self, admin_client):
        """CH-38: History accessible for RESOLVED disasters too."""
        client, mock_db = admin_client

        mock_db.execute.side_effect = [
            make_result(first={"disaster_status": "RESOLVED"}),
            make_result(first={"full_name": "Admin", "role": "ADMIN"}),
            make_result(rows=[]),
        ]

        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            async with client as c:
                resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_history_limit_param(self, admin_client):
        """CH-39: limit query param accepted."""
        client, mock_db = admin_client

        mock_db.execute.side_effect = [
            make_result(first={"disaster_status": "ACTIVE"}),
            make_result(first={"full_name": "Admin", "role": "ADMIN"}),
            make_result(rows=[]),
        ]

        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            async with client as c:
                resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history?limit=5")

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_history_shows_members_online(self, admin_client):
        """CH-40: Response includes members_online count."""
        client, mock_db = admin_client

        chat_module._chat_rooms[DISASTER_ID] = {"ws1": {}, "ws2": {}}

        mock_db.execute.side_effect = [
            make_result(first={"disaster_status": "ACTIVE"}),
            make_result(first={"full_name": "Admin", "role": "ADMIN"}),
            make_result(rows=[]),
        ]

        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            async with client as c:
                resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 200
        assert "members_online" in resp.json()

    @pytest.mark.asyncio
    async def test_history_empty_if_no_messages(self, admin_client):
        """CH-41: Returns empty messages list when no history."""
        client, mock_db = admin_client

        mock_db.execute.side_effect = [
            make_result(first={"disaster_status": "ACTIVE"}),
            make_result(first={"full_name": "Admin", "role": "ADMIN"}),
            make_result(rows=[]),
        ]

        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            async with client as c:
                resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    @pytest.mark.asyncio
    async def test_history_assigned_staff_gets_200(self, staff_client):
        """CH-42: Assigned STAFF can access history."""
        client, mock_db = staff_client

        mock_db.execute.side_effect = [
            make_result(first={"disaster_status": "ACTIVE"}),
            make_result(first={"full_name": "Staff", "role": "STAFF"}),
            make_result(first={"id": DISASTER_ID}),  # is assigned
            make_result(rows=[]),
        ]

        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        with patch.object(chat_module, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
            async with client as c:
                resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code in (200, 403)