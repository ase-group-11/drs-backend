# File: app/tests/unit/test_deployment_service.py
"""
Unit tests for DeploymentService — dispatch, status updates, mission tracking.

Endpoints (wired via disaster.py):
  POST /api/v1/disasters/{id}/dispatch       → dispatch_units (team only)
  POST /api/v1/deployments/{id}/status       → update_status
  GET  /api/v1/deployments/{id}              → get_deployment
  GET  /api/v1/units/{id}/missions/active    → get_active_missions
  GET  /api/v1/units/{id}/missions/completed → get_completed_missions

Run:
  pytest app/tests/unit/test_deployment_service.py -v
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from fastapi import status

from app.main import app
from app.auth.dependencies import get_current_user, get_current_team_member
from app.db.session import get_db

# ══════════════════════════════════════════════════════════
# CONSTANTS & HELPERS
# ══════════════════════════════════════════════════════════

TEAM_ID        = str(uuid.uuid4())
DISASTER_ID    = str(uuid.uuid4())
UNIT_ID        = str(uuid.uuid4())
DEPLOYMENT_ID  = str(uuid.uuid4())

TEAM_MEMBER = {
    "id": TEAM_ID,
    "full_name": "Dispatcher",
    "email": "dispatch@drs.ie",
    "role": "ADMIN",
    "department": "FIRE",
}

CITIZEN_USER = {
    "id": str(uuid.uuid4()),
    "full_name": "Citizen",
    "phone_number": "+353871234567",
    "role": "RESIDENT",
}


def make_mock_result(rows=None, row=None):
    mock = MagicMock()
    if row is not None:
        rows = [row]
    rows = rows or []
    mock.mappings.return_value.all.return_value = rows
    mock.mappings.return_value.first.return_value = rows[0] if rows else None
    mock.scalar.return_value = rows[0] if rows else None
    mock.first.return_value = rows[0] if rows else None
    return mock


def sample_disaster_row(ds="ACTIVE"):
    return {
        "id": DISASTER_ID,
        "tracking_id": "DIS-2026-00001",
        "disaster_status": ds,
        "type": "FIRE",
        "severity": "HIGH",
        "lat": 53.3498,
        "lon": -6.2603,
        "location_address": "Dublin 2",
    }


def sample_unit_row(unit_status="AVAILABLE"):
    return {
        "id": UNIT_ID,
        "unit_code": "FIR-001",
        "unit_name": "Fire Engine 1",
        "unit_type": "FIRE_ENGINE",
        "department": "FIRE",
        "unit_status": unit_status,
        "station_name": "Dublin Fire HQ",
        "station_lat": 53.34,
        "station_lon": -6.27,
    }


def sample_deployment_row(dep_status="DISPATCHED"):
    now = datetime.utcnow()
    return {
        "id": DEPLOYMENT_ID,
        "disaster_id": DISASTER_ID,
        "unit_id": UNIT_ID,
        "deployment_status": dep_status,
        "priority_level": "STANDARD",
        "special_instructions": None,
        "dispatched_at": now,
        "assigned_at": now,
        "en_route_at": None,
        "on_scene_at": None,
        "in_progress_at": None,
        "completed_at": None,
        "situation_report": None,
        "assessment_notes": None,
        "status_tags": [],
        "minor_injuries": 0,
        "serious_injuries": 0,
        "additional_resources": [],
        "location_verified": False,
        "request_immediate_backup": False,
        "created_at": now,
        "updated_at": now,
        # joined fields from disasters
        "tracking_id": "DIS-2026-00001",
        "disaster_type": "FIRE",
        "severity": "HIGH",
        "disaster_status": "ACTIVE",
        "disaster_description": "Fire at warehouse",
        "description": "Fire at warehouse",
        "location_address": "Dublin 2",
        "people_affected": 10,
        "lat": 53.3498,
        "lon": -6.2603,
        "distance_km": 2.5,
        # joined fields from emergency_units
        "unit_code": "FIR-001",
        "unit_name": "Fire Engine 1",
        "eu_type": "FIRE_ENGINE",
        "eu_department": "FIRE",
        "crew_count": 2,
        # aliased id for missions query
        "deployment_id": DEPLOYMENT_ID,
    }


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
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
# TC-DEP-01: Dispatch Units
# ══════════════════════════════════════════════════════════
class TestDispatchUnits:

    @pytest.mark.asyncio
    async def test_dispatch_success(self, team_client):
        """TC-DEP-01-01: Dispatch a unit to an active disaster."""
        client, mock_db = team_client

        claim_result = MagicMock()
        claim_result.first.return_value = {"id": UNIT_ID}

        mock_db.execute.side_effect = [
            make_mock_result(row=sample_disaster_row()),   # disaster check
            make_mock_result(row=sample_unit_row()),       # unit check
            claim_result,                                   # atomic claim UPDATE
            MagicMock(),                                    # INSERT deployment
            make_mock_result(row={"distance_km": 5.0}),    # ETA calc
            MagicMock(),                                    # UPDATE disaster dept
        ]

        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/dispatch",
            json={"unit_ids": [UNIT_ID], "priority_level": "STANDARD"},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["units_dispatched"]) == 1
        assert data["units_dispatched"][0]["unit_code"] == "FIR-001"

    @pytest.mark.asyncio
    async def test_dispatch_disaster_not_found(self, team_client):
        """TC-DEP-01-02: 404 when disaster does not exist."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.post(
            f"/api/v1/disasters/{uuid.uuid4()}/dispatch",
            json={"unit_ids": [UNIT_ID]},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_dispatch_to_resolved_disaster_is_400(self, team_client):
        """TC-DEP-01-03: Cannot dispatch to a resolved disaster."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(
            row=sample_disaster_row(ds="RESOLVED")
        )
        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/dispatch",
            json={"unit_ids": [UNIT_ID]},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_dispatch_unit_not_available_is_409(self, team_client):
        """TC-DEP-01-04: 409 when unit is already deployed (atomic claim fails)."""
        client, mock_db = team_client

        claim_result = MagicMock()
        claim_result.first.return_value = None  # ← claim failed

        mock_db.execute.side_effect = [
            make_mock_result(row=sample_disaster_row()),
            make_mock_result(row=sample_unit_row(unit_status="DEPLOYED")),
            claim_result,
        ]

        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/dispatch",
            json={"unit_ids": [UNIT_ID]},
        )
        assert response.status_code == status.HTTP_409_CONFLICT

    @pytest.mark.asyncio
    async def test_dispatch_unit_not_found_is_404(self, team_client):
        """TC-DEP-01-05: 404 when unit ID doesn't exist."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(row=sample_disaster_row()),
            make_mock_result(rows=[]),  # unit not found
        ]
        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/dispatch",
            json={"unit_ids": [str(uuid.uuid4())]},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_dispatch_response_has_no_pending_event(self, team_client):
        """TC-DEP-01-06: _pending_event is stripped from API response."""
        client, mock_db = team_client

        claim_result = MagicMock()
        claim_result.first.return_value = {"id": UNIT_ID}

        mock_db.execute.side_effect = [
            make_mock_result(row=sample_disaster_row()),
            make_mock_result(row=sample_unit_row()),
            claim_result,
            MagicMock(),
            make_mock_result(row={"distance_km": 3.0}),
            MagicMock(),
        ]

        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/dispatch",
            json={"unit_ids": [UNIT_ID]},
        )
        assert response.status_code == status.HTTP_200_OK
        assert "_pending_event" not in response.json()

    @pytest.mark.asyncio
    async def test_dispatch_citizen_forbidden(self, citizen_client):
        """TC-DEP-01-07: Citizens cannot dispatch units."""
        client, _ = citizen_client
        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/dispatch",
            json={"unit_ids": [UNIT_ID]},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ══════════════════════════════════════════════════════════
# TC-DEP-02: Get Deployment
# ══════════════════════════════════════════════════════════
class TestGetDeployment:

    @pytest.mark.asyncio
    async def test_get_deployment_success(self, team_client):
        """TC-DEP-02-01: GET /deployments/{id} returns deployment detail."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(
            row=sample_deployment_row()
        )
        response = await client.get(f"/api/v1/deployments/{DEPLOYMENT_ID}")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["deployment_id"] == DEPLOYMENT_ID
        assert data["deployment_status"] == "DISPATCHED"

    @pytest.mark.asyncio
    async def test_get_deployment_not_found(self, team_client):
        """TC-DEP-02-02: 404 for unknown deployment."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.get(f"/api/v1/deployments/{uuid.uuid4()}")
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ══════════════════════════════════════════════════════════
# TC-DEP-03: Update Deployment Status
# ══════════════════════════════════════════════════════════
class TestUpdateDeploymentStatus:

    @pytest.mark.asyncio
    async def test_update_status_en_route(self, team_client):
        """TC-DEP-03-01: Update deployment status to EN_ROUTE."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(row=sample_deployment_row("DISPATCHED")),  # fetch
            MagicMock(),  # UPDATE deployments
            MagicMock(),  # UPDATE emergency_units
        ]
        response = await client.post(
            f"/api/v1/deployments/{DEPLOYMENT_ID}/update-status",
            json={"new_status": "EN_ROUTE"},
        )
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_update_status_on_scene(self, team_client):
        """TC-DEP-03-02: Marking ON_SCENE sets response_time."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(row=sample_deployment_row("EN_ROUTE")),
            MagicMock(),
            MagicMock(),
        ]
        response = await client.post(
            f"/api/v1/deployments/{DEPLOYMENT_ID}/update-status",
            json={"new_status": "ON_SCENE"},
        )
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_update_status_deployment_not_found(self, team_client):
        """TC-DEP-03-03: 404 when deployment doesn't exist."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.post(
            f"/api/v1/deployments/{uuid.uuid4()}/status",
            json={"new_status": "EN_ROUTE"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ══════════════════════════════════════════════════════════
# TC-DEP-04: Active Missions
# ══════════════════════════════════════════════════════════
class TestActiveMissions:

    @pytest.mark.asyncio
    async def test_get_active_missions(self, team_client):
        """TC-DEP-04-01: GET /units/{id}/missions/active returns missions."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(
            rows=[sample_deployment_row("DISPATCHED")]
        )
        response = await client.get(f"/api/v1/deployments/unit/{UNIT_ID}/active")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "active_missions" in data or isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_active_missions_empty(self, team_client):
        """TC-DEP-04-02: Empty list when no active missions."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.get(f"/api/v1/deployments/unit/{UNIT_ID}/active")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Response shape: {"active_missions": [], "count": 0}
        missions = data.get("active_missions", data) if isinstance(data, dict) else data
        assert missions == []