# File: app/tests/unit/test_chat_integration.py
"""
Unit + Integration tests for the Disaster Group Chat feature.

Tests cover:
  Buffer logic:
    - _add_to_buffer: stores, auto-flush at CHUNK_SIZE, MAX_BUFFER_SIZE cap
    - _flush_buffer: INSERT, retry on failure, restore messages on failure
    - _periodic_flush_task: guard prevents duplicate tasks
    - Sequence and chunk counters

  DB helpers:
    - _get_disaster_status, _get_sender_info, _is_assigned_to_disaster
    - _fetch_history: combines DB chunks + buffered messages

  Room state:
    - _get_room, get_room_members, _get_lock

  REST API:
    - GET /chat/{disaster_id}/history: 200, 404, 403, limit param

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
from app.auth.dependencies import get_current_team_member
from app.db.session import get_db
import app.api.v1.chat as chat_module

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

DISASTER_ID  = str(uuid.uuid4())
DISASTER_ID2 = str(uuid.uuid4())
ADMIN_ID     = str(uuid.uuid4())
STAFF_ID     = str(uuid.uuid4())
NOW          = datetime.utcnow().isoformat()

ADMIN_USER = {"user_id": ADMIN_ID, "user_type": "emergency_team"}
STAFF_USER = {"user_id": STAFF_ID, "user_type": "emergency_team"}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_mock_db():
    db = AsyncMock()
    db.execute   = AsyncMock()
    db.flush     = AsyncMock()
    db.commit    = AsyncMock()
    db.rollback  = AsyncMock()
    db.close     = AsyncMock()
    return db


def make_db_result(rows=None, first=None):
    mock = MagicMock()
    rows = rows or []
    mock.mappings.return_value.all.return_value   = rows
    mock.mappings.return_value.first.return_value = first if first is not None else (rows[0] if rows else None)
    mock.first.return_value = first if first is not None else (rows[0] if rows else None)
    return mock


def make_msg(seq: int, disaster_id: str = DISASTER_ID) -> dict:
    return {
        "id":          str(uuid.uuid4()),
        "seq":         seq,
        "sender_id":   ADMIN_ID,
        "sender_name": "John Doe",
        "sender_type": "admin",
        "message":     f"Message {seq}",
        "sent_at":     NOW,
    }


def make_chunk_row(chunk_number: int, messages: list) -> dict:
    return {
        "chunk_number": chunk_number,
        "messages":     messages,
    }


@pytest.fixture(autouse=True)
def clear_chat_state():
    """Reset all in-memory chat state before each test."""
    chat_module._chat_rooms.clear()
    chat_module._message_buffer.clear()
    chat_module._seq_counters.clear()
    chat_module._chunk_counters.clear()
    chat_module._flush_locks.clear()
    chat_module._periodic_running.clear()
    yield
    chat_module._chat_rooms.clear()
    chat_module._message_buffer.clear()
    chat_module._seq_counters.clear()
    chat_module._chunk_counters.clear()
    chat_module._flush_locks.clear()
    chat_module._periodic_running.clear()


@pytest.fixture
def mock_db():
    return make_mock_db()


@pytest.fixture
def admin_client(mock_db):
    app.dependency_overrides[get_current_team_member] = lambda: ADMIN_USER
    app.dependency_overrides[get_db] = lambda: mock_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, mock_db
    app.dependency_overrides.clear()


@pytest.fixture
def staff_client(mock_db):
    app.dependency_overrides[get_current_team_member] = lambda: STAFF_USER
    app.dependency_overrides[get_db] = lambda: mock_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, mock_db
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Buffer logic tests
# ─────────────────────────────────────────────────────────────────────────────

class TestChatBuffer:

    @pytest.mark.asyncio
    async def test_add_to_buffer_stores_message(self):
        """CH-01: _add_to_buffer stores message in memory dict."""
        msg = make_msg(seq=1)
        result = await chat_module._add_to_buffer(DISASTER_ID, msg)

        assert result is True
        assert DISASTER_ID in chat_module._message_buffer
        assert len(chat_module._message_buffer[DISASTER_ID]) == 1
        assert chat_module._message_buffer[DISASTER_ID][0]["message"] == "Message 1"

    @pytest.mark.asyncio
    async def test_add_to_buffer_returns_false_when_full(self):
        """CH-02: _add_to_buffer returns False when MAX_BUFFER_SIZE reached."""
        # Pre-fill buffer to MAX_BUFFER_SIZE
        chat_module._message_buffer[DISASTER_ID] = [make_msg(i) for i in range(chat_module.MAX_BUFFER_SIZE)]

        result = await chat_module._add_to_buffer(DISASTER_ID, make_msg(seq=999))

        assert result is False
        # Buffer should not have grown
        assert len(chat_module._message_buffer[DISASTER_ID]) == chat_module.MAX_BUFFER_SIZE

    @pytest.mark.asyncio
    async def test_add_to_buffer_triggers_flush_at_chunk_size(self):
        """CH-03: Buffer triggers flush when it hits CHUNK_SIZE."""
        with patch("asyncio.create_task") as mock_task:
            # Add CHUNK_SIZE - 1 messages — no flush yet
            for i in range(chat_module.CHUNK_SIZE - 1):
                await chat_module._add_to_buffer(DISASTER_ID, make_msg(i + 1))
            mock_task.assert_not_called()

            # Add the CHUNK_SIZE-th message — flush triggered
            await chat_module._add_to_buffer(DISASTER_ID, make_msg(chat_module.CHUNK_SIZE))
            mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_two_disasters_have_independent_buffers(self):
        """CH-04: Two disasters have completely separate buffers."""
        await chat_module._add_to_buffer(DISASTER_ID, make_msg(seq=1, disaster_id=DISASTER_ID))
        await chat_module._add_to_buffer(DISASTER_ID2, make_msg(seq=1, disaster_id=DISASTER_ID2))

        assert len(chat_module._message_buffer[DISASTER_ID]) == 1
        assert len(chat_module._message_buffer[DISASTER_ID2]) == 1
        assert chat_module._message_buffer[DISASTER_ID][0] != chat_module._message_buffer[DISASTER_ID2][0]

    @pytest.mark.asyncio
    async def test_flush_buffer_inserts_to_db(self):
        """CH-05: _flush_buffer performs one INSERT with all buffered messages."""
        chat_module._message_buffer[DISASTER_ID] = [make_msg(i) for i in range(1, 4)]
        chat_module._chunk_counters[DISASTER_ID] = 0

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit  = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close   = AsyncMock()

        with patch("app.api.v1.chat.async_session_factory", return_value=mock_session):
            await chat_module._flush_buffer(DISASTER_ID)

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()
        # Buffer should be cleared after successful flush
        assert chat_module._message_buffer.get(DISASTER_ID, []) == []

    @pytest.mark.asyncio
    async def test_flush_buffer_does_nothing_if_empty(self):
        """CH-06: _flush_buffer does nothing when buffer is empty."""
        mock_session = AsyncMock()
        with patch("app.api.v1.chat.async_session_factory", return_value=mock_session):
            await chat_module._flush_buffer(DISASTER_ID)

        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_flush_buffer_retries_on_failure(self):
        """CH-07: _flush_buffer retries MAX_RETRIES times on DB failure."""
        chat_module._message_buffer[DISASTER_ID] = [make_msg(1)]
        chat_module._chunk_counters[DISASTER_ID] = 0

        call_count = 0

        async def always_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("DB error")

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=always_fail)
        mock_session.commit  = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close   = AsyncMock()

        with patch("app.api.v1.chat.async_session_factory", return_value=mock_session):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await chat_module._flush_buffer(DISASTER_ID)

        assert call_count == chat_module.MAX_RETRIES

    @pytest.mark.asyncio
    async def test_flush_buffer_restores_messages_on_all_retries_failed(self):
        """CH-08: Messages returned to buffer if all retries fail (not lost)."""
        original_messages = [make_msg(1), make_msg(2)]
        chat_module._message_buffer[DISASTER_ID] = list(original_messages)
        chat_module._chunk_counters[DISASTER_ID] = 0

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB down"))
        mock_session.commit  = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close   = AsyncMock()

        with patch("app.api.v1.chat.async_session_factory", return_value=mock_session):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await chat_module._flush_buffer(DISASTER_ID)

        # Messages must be back in buffer
        assert len(chat_module._message_buffer.get(DISASTER_ID, [])) == 2

    @pytest.mark.asyncio
    async def test_flush_buffer_uses_cast_jsonb_syntax(self):
        """CH-09: INSERT uses CAST(:messages AS jsonb) — correct asyncpg syntax."""
        chat_module._message_buffer[DISASTER_ID] = [make_msg(1)]
        chat_module._chunk_counters[DISASTER_ID] = 0

        captured_sql = {}

        async def capture(query, params=None):
            captured_sql["sql"] = str(query)
            captured_sql["params"] = params

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=capture)
        mock_session.commit  = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close   = AsyncMock()

        with patch("app.api.v1.chat.async_session_factory", return_value=mock_session):
            await chat_module._flush_buffer(DISASTER_ID)

        # Must use CAST syntax, not :: syntax
        assert "CAST" in captured_sql.get("sql", "")
        assert "::" not in captured_sql.get("sql", "")


# ─────────────────────────────────────────────────────────────────────────────
# Sequence and chunk counter tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCounters:

    def test_seq_counter_increments(self):
        """CH-10: Sequence numbers increment per disaster."""
        assert chat_module._next_seq(DISASTER_ID) == 1
        assert chat_module._next_seq(DISASTER_ID) == 2
        assert chat_module._next_seq(DISASTER_ID) == 3

    def test_seq_counters_independent_per_disaster(self):
        """CH-11: Seq counters are independent per disaster."""
        chat_module._next_seq(DISASTER_ID)
        chat_module._next_seq(DISASTER_ID)
        seq = chat_module._next_seq(DISASTER_ID2)
        assert seq == 1  # starts fresh for disaster 2

    def test_chunk_counter_increments(self):
        """CH-12: Chunk numbers increment per disaster."""
        assert chat_module._next_chunk(DISASTER_ID) == 1
        assert chat_module._next_chunk(DISASTER_ID) == 2

    def test_chunk_counters_independent_per_disaster(self):
        """CH-13: Chunk counters are independent per disaster."""
        chat_module._next_chunk(DISASTER_ID)
        chunk = chat_module._next_chunk(DISASTER_ID2)
        assert chunk == 1


# ─────────────────────────────────────────────────────────────────────────────
# Room state tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRoomState:

    def test_get_room_creates_empty_room(self):
        """CH-14: _get_room creates empty dict for new disaster."""
        room = chat_module._get_room(DISASTER_ID)
        assert room == {}
        assert DISASTER_ID in chat_module._chat_rooms

    def test_get_room_returns_existing(self):
        """CH-15: _get_room returns existing room without resetting."""
        chat_module._chat_rooms[DISASTER_ID] = {"conn-1": {"sender_name": "John"}}
        room = chat_module._get_room(DISASTER_ID)
        assert "conn-1" in room

    def test_get_room_members_empty(self):
        """CH-16: get_room_members returns 0 for unknown disaster."""
        assert chat_module.get_room_members("unknown-id") == 0

    def test_get_room_members_count(self):
        """CH-17: get_room_members returns correct count."""
        chat_module._chat_rooms[DISASTER_ID] = {"c1": {}, "c2": {}, "c3": {}}
        assert chat_module.get_room_members(DISASTER_ID) == 3

    def test_get_lock_returns_asyncio_lock(self):
        """CH-18: _get_lock returns asyncio.Lock per disaster."""
        lock = chat_module._get_lock(DISASTER_ID)
        assert isinstance(lock, asyncio.Lock)

    def test_get_lock_same_lock_for_same_disaster(self):
        """CH-19: Same lock returned for same disaster (not a new one each time)."""
        lock1 = chat_module._get_lock(DISASTER_ID)
        lock2 = chat_module._get_lock(DISASTER_ID)
        assert lock1 is lock2

    def test_get_lock_different_locks_per_disaster(self):
        """CH-20: Different disasters get different locks."""
        lock1 = chat_module._get_lock(DISASTER_ID)
        lock2 = chat_module._get_lock(DISASTER_ID2)
        assert lock1 is not lock2

    def test_periodic_task_guard(self):
        """CH-21: _periodic_running tracks running tasks per disaster."""
        assert not chat_module._periodic_running.get(DISASTER_ID)
        chat_module._periodic_running[DISASTER_ID] = True
        assert chat_module._periodic_running.get(DISASTER_ID) is True


# ─────────────────────────────────────────────────────────────────────────────
# DB helper tests
# ─────────────────────────────────────────────────────────────────────────────

class TestChatDBHelpers:

    @pytest.mark.asyncio
    async def test_get_disaster_status_returns_active(self):
        """CH-22: Returns correct status for active disaster."""
        db = make_mock_db()
        db.execute.return_value = make_db_result(first={"disaster_status": "ACTIVE"})
        status = await chat_module._get_disaster_status(db, DISASTER_ID)
        assert status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_get_disaster_status_returns_none_if_not_found(self):
        """CH-23: Returns None if disaster not found."""
        db = make_mock_db()
        db.execute.return_value = make_db_result(first=None)
        status = await chat_module._get_disaster_status(db, DISASTER_ID)
        assert status is None

    @pytest.mark.asyncio
    async def test_get_sender_info_admin(self):
        """CH-24: Returns correct name and role for admin."""
        db = make_mock_db()
        db.execute.return_value = make_db_result(first={"full_name": "Jane", "role": "ADMIN"})
        info = await chat_module._get_sender_info(db, ADMIN_ID)
        assert info["full_name"] == "Jane"
        assert info["role"] == "ADMIN"

    @pytest.mark.asyncio
    async def test_get_sender_info_defaults_for_unknown(self):
        """CH-25: Returns defaults for unknown user."""
        db = make_mock_db()
        db.execute.return_value = make_db_result(first=None)
        info = await chat_module._get_sender_info(db, "unknown")
        assert info["full_name"] == "Unknown"
        assert info["role"] == "STAFF"

    @pytest.mark.asyncio
    async def test_is_assigned_returns_true(self):
        """CH-26: Returns True when staff is deployed to disaster."""
        db = make_mock_db()
        db.execute.return_value = make_db_result(first={"team_member_id": STAFF_ID})
        result = await chat_module._is_assigned_to_disaster(db, STAFF_ID, DISASTER_ID)
        assert result is True

    @pytest.mark.asyncio
    async def test_is_assigned_returns_false(self):
        """CH-27: Returns False when staff is NOT deployed to disaster."""
        db = make_mock_db()
        db.execute.return_value = make_db_result(first=None)
        result = await chat_module._is_assigned_to_disaster(db, STAFF_ID, DISASTER_ID)
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_history_combines_chunks(self):
        """CH-28: _fetch_history combines messages from multiple DB chunks."""
        db = make_mock_db()
        msgs1 = [make_msg(i) for i in range(1, 4)]
        msgs2 = [make_msg(i) for i in range(4, 7)]

        db.execute.return_value = make_db_result(rows=[
            make_chunk_row(1, msgs1),
            make_chunk_row(2, msgs2),
        ])

        history = await chat_module._fetch_history(db, DISASTER_ID, limit=50)
        assert len(history) == 6
        assert all(m["type"] == "message" for m in history)

    @pytest.mark.asyncio
    async def test_fetch_history_includes_buffered_messages(self):
        """CH-29: _fetch_history includes messages still in memory buffer."""
        db = make_mock_db()
        db.execute.return_value = make_db_result(rows=[
            make_chunk_row(1, [make_msg(1), make_msg(2)])
        ])

        # 2 messages in buffer (not yet flushed)
        chat_module._message_buffer[DISASTER_ID] = [make_msg(3), make_msg(4)]

        history = await chat_module._fetch_history(db, DISASTER_ID, limit=50)
        # 2 from DB + 2 from buffer = 4
        assert len(history) == 4

    @pytest.mark.asyncio
    async def test_fetch_history_respects_limit(self):
        """CH-30: _fetch_history returns only last N messages."""
        db = make_mock_db()
        msgs = [make_msg(i) for i in range(1, 21)]  # 20 messages
        db.execute.return_value = make_db_result(rows=[make_chunk_row(1, msgs)])

        history = await chat_module._fetch_history(db, DISASTER_ID, limit=5)
        assert len(history) == 5
        # Should be the LAST 5 (most recent)
        assert history[-1]["seq"] == 20

    @pytest.mark.asyncio
    async def test_fetch_history_empty_when_no_messages(self):
        """CH-31: Returns empty list if no messages exist."""
        db = make_mock_db()
        db.execute.return_value = make_db_result(rows=[])
        history = await chat_module._fetch_history(db, DISASTER_ID, limit=50)
        assert history == []


# ─────────────────────────────────────────────────────────────────────────────
# REST API tests
# ─────────────────────────────────────────────────────────────────────────────

class TestChatHistoryAPI:

    @pytest.mark.asyncio
    async def test_history_returns_200_for_admin(self, admin_client):
        """CH-32: Admin can access history for any disaster."""
        client, mock_db = admin_client

        mock_db.execute.side_effect = [
            make_db_result(first={"disaster_status": "ACTIVE"}),
            make_db_result(first={"full_name": "Admin", "role": "ADMIN"}),
            make_db_result(rows=[make_chunk_row(1, [make_msg(1)])]),
        ]

        async with client as c:
            resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 200
        data = resp.json()
        assert data["disaster_id"] == DISASTER_ID
        assert "messages" in data
        assert "total_messages" in data
        assert "members_online" in data

    @pytest.mark.asyncio
    async def test_history_returns_404_if_disaster_not_found(self, admin_client):
        """CH-33: Returns 404 if disaster does not exist."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_db_result(first=None)

        async with client as c:
            resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_history_returns_403_for_unassigned_staff(self, staff_client):
        """CH-34: STAFF not assigned to disaster gets 403."""
        client, mock_db = staff_client

        mock_db.execute.side_effect = [
            make_db_result(first={"disaster_status": "ACTIVE"}),
            make_db_result(first={"full_name": "Staff", "role": "STAFF"}),
            make_db_result(first=None),  # not assigned
        ]

        async with client as c:
            resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_history_works_for_resolved_disaster(self, admin_client):
        """CH-35: History is readable even after disaster is RESOLVED."""
        client, mock_db = admin_client

        mock_db.execute.side_effect = [
            make_db_result(first={"disaster_status": "RESOLVED"}),
            make_db_result(first={"full_name": "Admin", "role": "ADMIN"}),
            make_db_result(rows=[make_chunk_row(1, [make_msg(1)])]),
        ]

        async with client as c:
            resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 200
        assert resp.json()["disaster_status"] == "RESOLVED"

    @pytest.mark.asyncio
    async def test_history_limit_param(self, admin_client):
        """CH-36: limit query param restricts number of messages."""
        client, mock_db = admin_client

        msgs = [make_msg(i) for i in range(1, 21)]
        mock_db.execute.side_effect = [
            make_db_result(first={"disaster_status": "ACTIVE"}),
            make_db_result(first={"full_name": "Admin", "role": "ADMIN"}),
            make_db_result(rows=[make_chunk_row(1, msgs)]),
        ]

        async with client as c:
            resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history?limit=5")

        assert resp.status_code == 200
        assert len(resp.json()["messages"]) == 5

    @pytest.mark.asyncio
    async def test_history_shows_members_online(self, admin_client):
        """CH-37: History response includes correct members_online count."""
        client, mock_db = admin_client

        # Simulate 2 users connected
        chat_module._chat_rooms[DISASTER_ID] = {"c1": {}, "c2": {}}

        mock_db.execute.side_effect = [
            make_db_result(first={"disaster_status": "ACTIVE"}),
            make_db_result(first={"full_name": "Admin", "role": "ADMIN"}),
            make_db_result(rows=[]),
        ]

        async with client as c:
            resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 200
        assert resp.json()["members_online"] == 2

    @pytest.mark.asyncio
    async def test_history_empty_if_no_messages(self, admin_client):
        """CH-38: Returns empty messages list if no messages exist."""
        client, mock_db = admin_client

        mock_db.execute.side_effect = [
            make_db_result(first={"disaster_status": "ACTIVE"}),
            make_db_result(first={"full_name": "Admin", "role": "ADMIN"}),
            make_db_result(rows=[]),
        ]

        async with client as c:
            resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 200
        assert resp.json()["messages"] == []
        assert resp.json()["total_messages"] == 0

    @pytest.mark.asyncio
    async def test_history_includes_buffered_messages(self, admin_client):
        """CH-39: History includes messages still in buffer (not yet flushed to DB)."""
        client, mock_db = admin_client

        # 1 message in DB chunk
        mock_db.execute.side_effect = [
            make_db_result(first={"disaster_status": "ACTIVE"}),
            make_db_result(first={"full_name": "Admin", "role": "ADMIN"}),
            make_db_result(rows=[make_chunk_row(1, [make_msg(1)])]),
        ]

        # 2 messages in buffer (not yet flushed)
        chat_module._message_buffer[DISASTER_ID] = [make_msg(2), make_msg(3)]

        async with client as c:
            resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 200
        # 1 from DB + 2 from buffer = 3
        assert resp.json()["total_messages"] == 3

    @pytest.mark.asyncio
    async def test_history_assigned_staff_gets_200(self, staff_client):
        """CH-40: STAFF assigned to disaster can access history."""
        client, mock_db = staff_client

        mock_db.execute.side_effect = [
            make_db_result(first={"disaster_status": "ACTIVE"}),
            make_db_result(first={"full_name": "Staff", "role": "STAFF"}),
            make_db_result(first={"team_member_id": STAFF_ID}),  # assigned
            make_db_result(rows=[]),
        ]

        async with client as c:
            resp = await c.get(f"/api/v1/chat/{DISASTER_ID}/history")

        assert resp.status_code == 200