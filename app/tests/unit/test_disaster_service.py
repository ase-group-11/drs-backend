# File: app/tests/unit/test_disaster_service.py
"""
Unit tests for DisasterService and Disaster API endpoints.

Covers:
  GET  /api/v1/disasters/active     → list_active
  GET  /api/v1/disasters/all        → list_all
  GET  /api/v1/disasters/{id}       → get_disaster
  POST /api/v1/disasters/{id}/resolve   → resolve_disaster (team only)
  POST /api/v1/disasters/{id}/escalate  → escalate_disaster (team only)

Run:
  pytest app/tests/unit/test_disaster_service.py -v
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport
from fastapi import status

from app.main import app
from app.auth.dependencies import get_current_user, get_current_team_member
from app.db.session import get_db

# ══════════════════════════════════════════════════════════
# SHARED FIXTURES & HELPERS
# ══════════════════════════════════════════════════════════

TEAM_ID      = str(uuid.uuid4())
CITIZEN_ID   = str(uuid.uuid4())
DISASTER_ID  = str(uuid.uuid4())
TRACKING_ID  = "DIS-2026-00001"

TEAM_MEMBER = {
    "id": TEAM_ID,
    "full_name": "Team Member",
    "email": "team@drs.ie",
    "role": "ADMIN",
    "department": "FIRE",
}

CITIZEN_USER = {
    "id": CITIZEN_ID,
    "full_name": "Citizen",
    "phone_number": "+353871234567",
    "role": "RESIDENT",
}

def make_mock_result(rows=None, row=None):
    """Create a mock DB result that supports .mappings().all() and .mappings().first()."""
    mock = MagicMock()
    if row is not None:
        rows = [row]
    rows = rows or []
    mock.mappings.return_value.all.return_value = rows
    mock.mappings.return_value.first.return_value = rows[0] if rows else None
    mock.scalar.return_value = rows[0] if rows else None
    return mock

def sample_disaster_row(
    disaster_id=None,
    status="ACTIVE",
    severity="HIGH",
    disaster_type="FIRE",
):
    return {
        "id": disaster_id or DISASTER_ID,
        "tracking_id": TRACKING_ID,
        "type": disaster_type,
        "severity": severity,
        "disaster_status": status,
        "description": "Test disaster",
        "latitude": 53.3498,
        "longitude": -6.2603,
        "location_address": "Dublin 2",
        "affected_area": None,
        "people_affected": 10,
        "multiple_casualties": False,
        "structural_damage": True,
        "road_blocked": False,
        "assigned_to_id": None,
        "assigned_to_name": None,
        "assigned_to_phone": None,
        "assigned_department": "FIRE",
        "response_time": None,
        "resolved_time": None,
        "resolution_notes": None,
        "created_by_id": CITIZEN_ID,
        "disaster_metadata": None,
        "report_count": 2,
        "units_assigned": 1,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

def sample_count_row():
    return {
        "critical_count": 0,
        "active_count": 1,
        "resolved_count": 0,
        "monitoring_count": 0,
        "archived_count": 0,
    }


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@pytest.fixture
def team_client(mock_db):
    app.dependency_overrides[get_current_team_member] = lambda: TEAM_MEMBER
    app.dependency_overrides[get_current_user]        = lambda: TEAM_MEMBER
    app.dependency_overrides[get_db]                  = lambda: mock_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, mock_db
    app.dependency_overrides.clear()


@pytest.fixture
def citizen_client(mock_db):
    from fastapi import HTTPException
    app.dependency_overrides[get_current_user]        = lambda: CITIZEN_USER
    app.dependency_overrides[get_current_team_member] = lambda: (_ for _ in ()).throw(HTTPException(status_code=403, detail="Forbidden"))
    app.dependency_overrides[get_db]                  = lambda: mock_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, mock_db
    app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════
# TC-DS-01: List Active Disasters
# ══════════════════════════════════════════════════════════
class TestListActiveDisasters:

    @pytest.mark.asyncio
    async def test_list_active_returns_200(self, team_client):
        """TC-DS-01-01: GET /disasters/active returns 200 with disaster list."""
        client, mock_db = team_client
        disaster_row  = sample_disaster_row()
        unit_ids_row  = []  # no deployed units

        mock_db.execute.side_effect = [
            make_mock_result(rows=[disaster_row]),   # main query
            make_mock_result(row=sample_count_row()),  # count query
            make_mock_result(rows=unit_ids_row),     # deployed unit IDs
        ]

        response = await client.get("/api/v1/disasters/active")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "disasters" in data
        assert data["count"] == 1
        assert data["disasters"][0]["disaster_status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_list_active_empty(self, team_client):
        """TC-DS-01-02: Returns empty list when no active disasters."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(rows=[]),
            make_mock_result(row=sample_count_row()),
        ]
        response = await client.get("/api/v1/disasters/active")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 0
        assert response.json()["disasters"] == []

    @pytest.mark.asyncio
    async def test_list_active_requires_team_token(self, citizen_client):
        """TC-DS-01-03: Citizens cannot access /disasters/active."""
        client, _ = citizen_client
        response = await client.get("/api/v1/disasters/active")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_list_active_summary_counts(self, team_client):
        """TC-DS-01-04: Summary counts are returned correctly."""
        client, mock_db = team_client
        count_row = {
            "critical_count": 2,
            "active_count": 5,
            "resolved_count": 3,
            "monitoring_count": 1,
            "archived_count": 0,
        }
        mock_db.execute.side_effect = [
            make_mock_result(rows=[]),
            make_mock_result(row=count_row),
        ]
        response = await client.get("/api/v1/disasters/active")
        assert response.status_code == status.HTTP_200_OK
        summary = response.json()["summary"]
        assert summary["critical"] == 2
        assert summary["active"] == 5
        assert summary["resolved"] == 3


# ══════════════════════════════════════════════════════════
# TC-DS-02: List All Disasters
# ══════════════════════════════════════════════════════════
class TestListAllDisasters:

    @pytest.mark.asyncio
    async def test_list_all_returns_200(self, team_client):
        """TC-DS-02-01: GET /disasters/all returns 200."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(rows=[sample_disaster_row()]),
            make_mock_result(row=sample_count_row()),
            make_mock_result(rows=[]),
        ]
        response = await client.get("/api/v1/disasters/all")
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_list_all_citizen_forbidden(self, citizen_client):
        """TC-DS-02-02: Citizens cannot access /disasters/all."""
        client, _ = citizen_client
        response = await client.get("/api/v1/disasters/all")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_list_all_includes_resolved(self, team_client):
        """TC-DS-02-03: All endpoint includes resolved disasters."""
        client, mock_db = team_client
        resolved_row = sample_disaster_row(status="RESOLVED")
        mock_db.execute.side_effect = [
            make_mock_result(rows=[resolved_row]),
            make_mock_result(row=sample_count_row()),
            make_mock_result(rows=[]),
        ]
        response = await client.get("/api/v1/disasters/all")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["disasters"][0]["disaster_status"] == "RESOLVED"


# ══════════════════════════════════════════════════════════
# TC-DS-03: Get Disaster Detail
# ══════════════════════════════════════════════════════════
class TestGetDisaster:

    @pytest.mark.asyncio
    async def test_get_disaster_success(self, team_client):
        """TC-DS-03-01: GET /disasters/{id} returns full disaster detail."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(row=sample_disaster_row()),  # main query
            make_mock_result(rows=[]),                    # deployed unit IDs
        ]
        response = await client.get(f"/api/v1/disasters/{DISASTER_ID}")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == DISASTER_ID
        assert data["tracking_id"] == TRACKING_ID
        assert "location" in data

    @pytest.mark.asyncio
    async def test_get_disaster_not_found(self, team_client):
        """TC-DS-03-02: 404 for unknown disaster ID."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.get(f"/api/v1/disasters/{uuid.uuid4()}")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_disaster_citizen_can_access(self, citizen_client):
        """TC-DS-03-03: Citizens can view disaster detail."""
        client, mock_db = citizen_client
        mock_db.execute.side_effect = [
            make_mock_result(row=sample_disaster_row()),
            make_mock_result(rows=[]),
        ]
        response = await client.get(f"/api/v1/disasters/{DISASTER_ID}")
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_get_disaster_response_shape(self, team_client):
        """TC-DS-03-04: Response contains all required fields."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(row=sample_disaster_row()),
            make_mock_result(rows=[]),
        ]
        response = await client.get(f"/api/v1/disasters/{DISASTER_ID}")
        data = response.json()
        required_fields = ["id", "tracking_id", "type", "severity",
                           "disaster_status", "location", "description"]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"


# ══════════════════════════════════════════════════════════
# TC-DS-04: Resolve Disaster
# ══════════════════════════════════════════════════════════
class TestResolveDisaster:

    @pytest.mark.asyncio
    async def test_resolve_success(self, team_client):
        """TC-DS-04-01: POST /disasters/{id}/resolve returns 200."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(row=sample_disaster_row()),  # check_sql
            MagicMock(),   # UPDATE disasters
            MagicMock(),   # UPDATE deployments
            MagicMock(),   # UPDATE emergency_units
        ]
        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/resolve",
            json={"resolution_notes": "Situation resolved."},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["disaster_status"] == "RESOLVED"
        assert data["disaster_id"] == DISASTER_ID

    @pytest.mark.asyncio
    async def test_resolve_not_found_is_404(self, team_client):
        """TC-DS-04-02: Resolving unknown disaster returns 404."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.post(
            f"/api/v1/disasters/{uuid.uuid4()}/resolve",
            json={"resolution_notes": "Done."},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_resolve_already_resolved_is_400(self, team_client):
        """TC-DS-04-03: Resolving already-resolved disaster returns 400."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(
            row=sample_disaster_row(status="RESOLVED")
        )
        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/resolve",
            json={"resolution_notes": "Already done."},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_resolve_citizen_forbidden(self, citizen_client):
        """TC-DS-04-04: Citizens cannot resolve disasters."""
        client, _ = citizen_client
        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/resolve",
            json={"resolution_notes": "Test."},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_resolve_response_has_no_pending_event(self, team_client):
        """TC-DS-04-05: _pending_event is stripped from response body."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(row=sample_disaster_row()),
            MagicMock(), MagicMock(), MagicMock(),
        ]
        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/resolve",
            json={"resolution_notes": "Done."},
        )
        assert "_pending_event" not in response.json()


# ══════════════════════════════════════════════════════════
# TC-DS-05: Escalate Disaster
# ══════════════════════════════════════════════════════════
class TestEscalateDisaster:

    @pytest.mark.asyncio
    async def test_escalate_success(self, team_client):
        """TC-DS-05-01: Escalating severity returns updated disaster."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(row=sample_disaster_row(severity="MEDIUM")),
            MagicMock(),  # UPDATE disasters
        ]
        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/escalate",
            json={"new_severity": "CRITICAL", "reason": "More casualties reported."},
        )
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_escalate_resolved_disaster_is_400(self, team_client):
        """TC-DS-05-02: Cannot escalate a resolved disaster."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(
            row=sample_disaster_row(status="RESOLVED")
        )
        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/escalate",
            json={"new_severity": "CRITICAL"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_escalate_not_found(self, team_client):
        """TC-DS-05-03: 404 for unknown disaster."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.post(
            f"/api/v1/disasters/{uuid.uuid4()}/escalate",
            json={"new_severity": "HIGH"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND