# File: app/tests/unit/test_emergency_unit.py
"""
Unit tests for EmergencyUnitService.

Run:
  pytest app/tests/unit/test_emergency_unit.py -v
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport
from fastapi import status, HTTPException

from app.main import app
from app.auth.dependencies import get_current_user, get_current_team_member
from app.db.session import get_db

# ══════════════════════════════════════════════════════════
# CONSTANTS & HELPERS
# ══════════════════════════════════════════════════════════

TEAM_ID   = str(uuid.uuid4())
UNIT_ID   = str(uuid.uuid4())
MEMBER_ID = str(uuid.uuid4())

TEAM_MEMBER = {
    "id": TEAM_ID,
    "full_name": "Unit Manager",
    "email": "units@drs.ie",
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


def sample_unit_row(unit_id=None, unit_status="AVAILABLE"):
    """Full unit row matching the get_unit SQL JOIN query."""
    now = datetime.utcnow()
    return {
        "id": unit_id or UNIT_ID,
        "unit_code": "FIR-001",
        "unit_name": "Fire Engine 1",
        "description": "Primary fire response unit",
        "unit_type": "FIRE_ENGINE",
        "department": "FIRE",
        "unit_status": unit_status,
        "station_name": "Dublin Fire HQ",
        "station_address": "Townsend St, Dublin 2",
        "station_lat": 53.3459,
        "station_lon": -6.2551,
        "capacity": 4,
        "total_deployments": 10,
        "avg_response_time_seconds": 300,
        "commander_id": None,
        "commander_name": None,
        "commander_phone": None,
        "crew_count": 2,
        "commander_name": None,
        "distance_km": None,
        "eta_minutes": None,
        "last_deployed_at": None,
        "success_rate": None,
        "vehicle_model": None,
        "vehicle_license_plate": None,
        "vehicle_year": None,
        "equipment_checklist": [],
        "commander_email": None,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }


def sample_count_row():
    return {"total": 1, "active_count": 1, "deployed_count": 0}


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.flush = AsyncMock()
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
    app.dependency_overrides[get_current_user] = lambda: CITIZEN_USER
    app.dependency_overrides[get_current_team_member] = lambda: (_ for _ in ()).throw(
        HTTPException(status_code=403, detail="Forbidden")
    )
    app.dependency_overrides[get_db] = lambda: mock_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, mock_db
    app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════
# TC-EU-01: List Units
# list_units makes 3 execute calls:
#   1. main SQL
#   2. count SQL (total/active/deployed)
#   3. dept SQL (by_department breakdown)
# ══════════════════════════════════════════════════════════
class TestListUnits:

    @pytest.mark.asyncio
    async def test_list_units_success(self, team_client):
        """TC-EU-01-01: GET /emergency-units/ returns list."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(rows=[sample_unit_row()]),     # 1. main query
            make_mock_result(row=sample_count_row()),        # 2. count query
            make_mock_result(rows=[{"department": "FIRE", "cnt": 1}]),  # 3. dept query
        ]
        response = await client.get("/api/v1/emergency-units/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "units" in data

    @pytest.mark.asyncio
    async def test_list_units_empty(self, team_client):
        """TC-EU-01-02: Returns empty list when no units exist."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(rows=[]),
            make_mock_result(row={"total": 0, "active_count": 0, "deployed_count": 0}),
            make_mock_result(rows=[]),
        ]
        response = await client.get("/api/v1/emergency-units/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["units"] == []

    @pytest.mark.asyncio
    async def test_list_units_citizen_forbidden(self, citizen_client):
        """TC-EU-01-03: Citizens cannot list units → 403."""
        client, _ = citizen_client
        response = await client.get("/api/v1/emergency-units/")
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ══════════════════════════════════════════════════════════
# TC-EU-02: List Available Units
# list_available_units makes 1 execute call
# Row fields: id, unit_code, unit_name, unit_type, department,
#             station_name, crew_count, capacity, commander_name,
#             distance_km, eta_minutes
# ══════════════════════════════════════════════════════════
class TestListAvailableUnits:

    @pytest.mark.asyncio
    async def test_available_units_success(self, team_client):
        """TC-EU-02-01: GET /emergency-units/available returns AVAILABLE units."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[sample_unit_row()])
        response = await client.get("/api/v1/emergency-units/available")
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_available_units_empty(self, team_client):
        """TC-EU-02-02: Empty when all units are deployed."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.get("/api/v1/emergency-units/available")
        assert response.status_code == status.HTTP_200_OK


# ══════════════════════════════════════════════════════════
# TC-EU-03: Get Unit Detail
# get_unit makes 3 execute calls:
#   1. main unit SQL (with JOINs)
#   2. crew roster SQL
#   3. active deployment SQL
# ══════════════════════════════════════════════════════════
class TestGetUnit:

    @pytest.mark.asyncio
    async def test_get_unit_success(self, team_client):
        """TC-EU-03-01: GET /emergency-units/{id} returns unit detail."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(row=sample_unit_row()),   # 1. unit main query
            make_mock_result(rows=[]),                  # 2. crew roster
            make_mock_result(rows=[]),                  # 3. active deployment
        ]
        response = await client.get(f"/api/v1/emergency-units/{UNIT_ID}")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == UNIT_ID
        assert data["unit_code"] == "FIR-001"

    @pytest.mark.asyncio
    async def test_get_unit_not_found(self, team_client):
        """TC-EU-03-02: 404 for unknown unit."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.get(f"/api/v1/emergency-units/{uuid.uuid4()}")
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ══════════════════════════════════════════════════════════
# TC-EU-04: Create Unit
# create_unit makes:
#   1. check duplicate unit_code
#   2. INSERT unit (+ optional crew inserts)
#   then calls get_unit (3 more calls)
# ══════════════════════════════════════════════════════════
class TestCreateUnit:

    @pytest.mark.asyncio
    async def test_create_unit_success(self, team_client):
        """TC-EU-04-01: POST /emergency-units/ creates new unit."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(rows=[]),                  # 1. check duplicate code
            MagicMock(),                                 # 2. INSERT unit
            make_mock_result(row=sample_unit_row()),    # 3. get_unit main query
            make_mock_result(rows=[]),                  # 4. get_unit crew
            make_mock_result(rows=[]),                  # 5. get_unit deployment
        ]
        response = await client.post(
            "/api/v1/emergency-units/",
            json={
                "unit_code": "FIR-001",
                "unit_name": "Fire Engine 1",
                "unit_type": "FIRE_ENGINE",
                "department": "FIRE",
                "station_name": "Dublin Fire HQ",
                "station_address": "Townsend St, Dublin 2",
                "station_latitude": 53.3459,
                "station_longitude": -6.2551,
                "capacity": 4,
            },
        )
        assert response.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)

    @pytest.mark.asyncio
    async def test_create_unit_citizen_forbidden(self, citizen_client):
        """TC-EU-04-02: Citizens cannot create units → 403."""
        client, _ = citizen_client
        response = await client.post(
            "/api/v1/emergency-units/",
            json={
                "unit_code": "FIR-002",
                "unit_name": "Fire Engine 2",
                "unit_type": "FIRE_ENGINE",
                "department": "FIRE",
                "station_name": "HQ",
                "station_address": "Dublin",
                "station_latitude": 53.34,
                "station_longitude": -6.26,
                "capacity": 4,
            },
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ══════════════════════════════════════════════════════════
# TC-EU-05: Decommission Unit
# decommission_unit makes 3 execute calls:
#   1. check_sql (SELECT id, unit_code, unit_status)
#   2. UPDATE deployments → CANCELLED
#   3. UPDATE emergency_units RETURNING id (atomic — returns None if DEPLOYED)
# The 400 is raised when RETURNING returns no rows (unit is DEPLOYED/ON_SCENE)
# ══════════════════════════════════════════════════════════
class TestDecommissionUnit:

    @pytest.mark.asyncio
    async def test_decommission_success(self, team_client):
        """TC-EU-05-01: DELETE /emergency-units/{id} decommissions AVAILABLE unit."""
        client, mock_db = team_client

        # The RETURNING UPDATE returns a row (success)
        returning_result = MagicMock()
        returning_result.first.return_value = {"id": UNIT_ID}

        mock_db.execute.side_effect = [
            make_mock_result(row=sample_unit_row(unit_status="AVAILABLE")),  # 1. check
            MagicMock(),        # 2. UPDATE deployments CANCELLED
            MagicMock(),        # 3. DELETE FROM unit_crew (crew fix)
            returning_result,   # 4. UPDATE units RETURNING (success)
        ]
        response = await client.delete(f"/api/v1/emergency-units/{UNIT_ID}")
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_decommission_deployed_unit_is_400(self, team_client):
        """TC-EU-05-02: Cannot decommission a DEPLOYED unit → 400."""
        client, mock_db = team_client

        # The RETURNING UPDATE returns None (unit is DEPLOYED — blocked by WHERE clause)
        returning_result = MagicMock()
        returning_result.first.return_value = None  # ← atomic check fails

        mock_db.execute.side_effect = [
            make_mock_result(row=sample_unit_row(unit_status="DEPLOYED")),  # 1. check
            MagicMock(),        # 2. UPDATE deployments
            MagicMock(),        # 3. DELETE FROM unit_crew (crew fix)
            returning_result,   # 4. UPDATE units RETURNING (blocked — DEPLOYED)
        ]
        response = await client.delete(f"/api/v1/emergency-units/{UNIT_ID}")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_decommission_not_found(self, team_client):
        """TC-EU-05-03: 404 for unknown unit."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.delete(f"/api/v1/emergency-units/{uuid.uuid4()}")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_decommission_makes_3_db_calls(self, team_client):
        """TC-EU-05-04: Decommission makes 4 DB calls: check, cancel deployments, delete crew, soft-delete."""
        client, mock_db = team_client

        returning_result = MagicMock()
        returning_result.first.return_value = {"id": UNIT_ID}

        mock_db.execute.side_effect = [
            make_mock_result(row=sample_unit_row(unit_status="AVAILABLE")),
            MagicMock(),        # UPDATE deployments
            MagicMock(),        # DELETE crew (bug fix)
            returning_result,   # UPDATE units RETURNING
        ]

        response = await client.delete(f"/api/v1/emergency-units/{UNIT_ID}")
        assert response.status_code == status.HTTP_200_OK
        assert mock_db.execute.call_count == 4


# ══════════════════════════════════════════════════════════
# TC-EU-06: Crew Management
# add_crew_member makes:
#   1. unit check
#   2. member check
#   3. already-assigned check
#   4. INSERT unit_crew + count query
# ══════════════════════════════════════════════════════════
class TestCrewManagement:

    @pytest.mark.asyncio
    async def test_add_crew_member(self, team_client):
        """TC-EU-06-01: POST /emergency-units/{id}/crew adds a member."""
        client, mock_db = team_client
        mock_db.execute.side_effect = [
            make_mock_result(row={"id": UNIT_ID, "unit_code": "FIR-001", "capacity": 4}),
            make_mock_result(row={"id": MEMBER_ID, "full_name": "John"}),
            make_mock_result(rows=[]),   # not already assigned
            MagicMock(),                  # INSERT unit_crew
            make_mock_result(row={"count": 1}),  # crew count
        ]
        response = await client.post(
            f"/api/v1/emergency-units/{UNIT_ID}/crew",
            json={"team_member_id": MEMBER_ID},
        )
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_add_crew_unit_not_found(self, team_client):
        """TC-EU-06-02: 404 when unit doesn't exist."""
        client, mock_db = team_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.post(
            f"/api/v1/emergency-units/{uuid.uuid4()}/crew",
            json={"team_member_id": MEMBER_ID},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND