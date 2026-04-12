# File: app/tests/unit/test_incident_log_fixed.py
"""
Fixed incident log / timeline tests.
Actual URL: /api/v1/disasters/{id}/timeline  (NOT /incident-log)

Run:
  pytest app/tests/unit/test_incident_log_fixed.py -v --tb=short
"""

import pytest
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.auth.dependencies import get_current_user, get_current_team_member
from app.db.session import get_db

DISASTER_ID = str(uuid.uuid4())
TRACKING_ID = "DIS-2026-00042"
NOW         = datetime.utcnow()

ADMIN_USER = {
    "id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),
    "full_name": "Admin", "role": "ADMIN",
    "user_type": "emergency_team",
}

CITIZEN = {
    "id": str(uuid.uuid4()), "full_name": "John",
    "phone_number": "+353871234567", "role": "RESIDENT",
}


def make_mock_db():
    db = AsyncMock()
    db.execute  = AsyncMock()
    db.flush    = AsyncMock()
    db.commit   = AsyncMock()
    db.rollback = AsyncMock()
    return db


def make_result(rows=None, first=None):
    mock = MagicMock()
    rows = rows or []
    mock.mappings.return_value.all.return_value   = rows
    mock.mappings.return_value.first.return_value = first if first is not None else (rows[0] if rows else None)
    mock.first.return_value = first if first is not None else (rows[0] if rows else None)
    return mock


def timeline_entry(event_type, minutes_ago=0, actor="System", badge="System"):
    ts = NOW - timedelta(minutes=minutes_ago)
    return {
        "event_type": event_type,
        "title":      event_type.replace("_", " ").title(),
        "actor":      actor,
        "badge":      badge,
        "event_time": ts,
    }


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


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestIncidentLogService:

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_disaster(self):
        """IL-01: 404 if disaster not found."""
        from app.services.incident_log_service import IncidentLogService
        from fastapi import HTTPException
        db = make_mock_db()
        db.execute.return_value = make_result(first=None)
        with pytest.raises(HTTPException) as exc:
            await IncidentLogService(db).get_timeline(DISASTER_ID)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_timeline_structure(self):
        """IL-02: Returns disaster_id, tracking_id, entries, total_entries."""
        from app.services.incident_log_service import IncidentLogService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first={"id": DISASTER_ID, "tracking_id": TRACKING_ID}),
            make_result(rows=[
                timeline_entry("DISASTER_REPORTED", 60, "Citizen", "Citizen"),
                timeline_entry("UNITS_DEPLOYED",    50, "Admin User", "Admin"),
            ]),
        ]
        result = await IncidentLogService(db).get_timeline(DISASTER_ID)
        assert result["disaster_id"]   == DISASTER_ID
        assert result["tracking_id"]   == TRACKING_ID
        assert result["total_entries"] == 2
        assert len(result["entries"])  == 2

    @pytest.mark.asyncio
    async def test_entries_sorted_newest_first(self):
        """IL-03: Entries sorted newest first."""
        from app.services.incident_log_service import IncidentLogService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first={"id": DISASTER_ID, "tracking_id": TRACKING_ID}),
            make_result(rows=[
                timeline_entry("DISASTER_REPORTED", 60),
                timeline_entry("RESPONSE_STARTED",  50),
                timeline_entry("UNITS_DEPLOYED",    40),
                timeline_entry("DISASTER_RESOLVED",  5),
            ]),
        ]
        result = await IncidentLogService(db).get_timeline(DISASTER_ID)
        timestamps = [e["timestamp"] for e in result["entries"]]
        assert timestamps == sorted(timestamps, reverse=True)

    @pytest.mark.asyncio
    async def test_empty_timeline_returns_empty_entries(self):
        """IL-04: No events → empty list."""
        from app.services.incident_log_service import IncidentLogService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first={"id": DISASTER_ID, "tracking_id": TRACKING_ID}),
            make_result(rows=[]),
        ]
        result = await IncidentLogService(db).get_timeline(DISASTER_ID)
        assert result["entries"]       == []
        assert result["total_entries"] == 0

    @pytest.mark.asyncio
    async def test_entry_has_required_fields(self):
        """IL-05: Each entry has all required fields."""
        from app.services.incident_log_service import IncidentLogService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first={"id": DISASTER_ID, "tracking_id": TRACKING_ID}),
            make_result(rows=[timeline_entry("UNITS_DEPLOYED", 30, "Admin User", "Admin")]),
        ]
        result = await IncidentLogService(db).get_timeline(DISASTER_ID)
        entry = result["entries"][0]
        for field in ("event_type", "title", "actor", "badge", "time", "timestamp"):
            assert field in entry

    @pytest.mark.asyncio
    async def test_citizen_actor_preserved(self):
        """IL-06: Citizen actor/badge preserved correctly."""
        from app.services.incident_log_service import IncidentLogService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first={"id": DISASTER_ID, "tracking_id": TRACKING_ID}),
            make_result(rows=[timeline_entry("DISASTER_REPORTED", 60, "Citizen", "Citizen")]),
        ]
        result = await IncidentLogService(db).get_timeline(DISASTER_ID)
        assert result["entries"][0]["actor"] == "Citizen"
        assert result["entries"][0]["badge"] == "Citizen"

    @pytest.mark.asyncio
    async def test_all_13_event_types_processed(self):
        """IL-07: All 13 event types returned."""
        from app.services.incident_log_service import IncidentLogService
        all_types = [
            "DISASTER_REPORTED", "RESPONSE_STARTED", "UNITS_DEPLOYED",
            "UNITS_ARRIVED", "BACKUP_REQUESTED", "MISSION_COMPLETED",
            "REROUTE_TRIGGERED", "OPERATOR_OVERRIDE", "TRAFFIC_RESTORED",
            "EVACUATION_CREATED", "EVACUATION_APPROVED", "EVACUATION_ACTIVATED",
            "DISASTER_RESOLVED",
        ]
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first={"id": DISASTER_ID, "tracking_id": TRACKING_ID}),
            make_result(rows=[timeline_entry(et, i*5) for i, et in enumerate(all_types)]),
        ]
        result = await IncidentLogService(db).get_timeline(DISASTER_ID)
        assert result["total_entries"] == 13
        assert {e["event_type"] for e in result["entries"]} == set(all_types)


# ─────────────────────────────────────────────────────────────────────────────
# API TESTS — actual URL: /api/v1/disasters/{id}/timeline
# ─────────────────────────────────────────────────────────────────────────────

class TestIncidentLogAPI:

    @pytest.mark.asyncio
    async def test_get_timeline_returns_200(self, admin_client):
        """IL-08: GET /disasters/{id}/timeline → 200."""
        client, mock_db = admin_client
        mock_db.execute.side_effect = [
            make_result(first={"id": DISASTER_ID, "tracking_id": TRACKING_ID}),
            make_result(rows=[
                timeline_entry("DISASTER_REPORTED", 60, "Citizen", "Citizen"),
                timeline_entry("UNITS_DEPLOYED",    50, "Admin User", "Admin"),
            ]),
        ]
        async with client as c:
            resp = await c.get(f"/api/v1/disasters/{DISASTER_ID}/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["disaster_id"] == DISASTER_ID
        assert len(data["entries"]) == 2

    @pytest.mark.asyncio
    async def test_get_timeline_returns_404_unknown_disaster(self, admin_client):
        """IL-09: Unknown disaster → 404."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(first=None)
        async with client as c:
            resp = await c.get(f"/api/v1/disasters/{DISASTER_ID}/timeline")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_timeline_requires_auth(self):
        """IL-10: No auth → 401/403."""
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        async with client as c:
            resp = await c.get(f"/api/v1/disasters/{DISASTER_ID}/timeline")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_get_timeline_empty_returns_200(self, admin_client):
        """IL-11: Empty timeline → 200 with empty entries."""
        client, mock_db = admin_client
        mock_db.execute.side_effect = [
            make_result(first={"id": DISASTER_ID, "tracking_id": TRACKING_ID}),
            make_result(rows=[]),
        ]
        async with client as c:
            resp = await c.get(f"/api/v1/disasters/{DISASTER_ID}/timeline")
        assert resp.status_code == 200
        assert resp.json()["entries"]       == []
        assert resp.json()["total_entries"] == 0