# File: app/tests/unit/test_integration.py
"""
Integration tests for Dublin City Disaster Response System.

Tests full end-to-end flows across multiple services.

Run:
  pytest app/tests/unit/test_integration.py -v
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from fastapi import status, HTTPException

from app.main import app
from app.auth.dependencies import get_current_user, get_current_team_member
from app.db.session import get_db

# ══════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════

ADMIN_ID     = str(uuid.uuid4())
CITIZEN_ID   = str(uuid.uuid4())
DISASTER_ID  = str(uuid.uuid4())
UNIT_ID      = str(uuid.uuid4())
REPORT_ID    = str(uuid.uuid4())
TRACKING_ID  = "DIS-2026-00042"

ADMIN_MEMBER = {
    "id": ADMIN_ID,
    "full_name": "Admin",
    "email": "admin@drs.ie",
    "role": "ADMIN",
    "department": "FIRE",
}

CITIZEN = {
    "id": CITIZEN_ID,
    "full_name": "John Citizen",
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


def report_row(report_status="PENDING"):
    now = datetime.utcnow()
    return {
        "id": REPORT_ID,
        "user_id": CITIZEN_ID,
        "disaster_type": "FIRE",
        "severity": "HIGH",
        "description": "Fire at warehouse",
        "latitude": 53.3498,
        "longitude": -6.2603,
        "location_address": "Grand Canal Dock, Dublin 2",
        "people_affected": 10,
        "multiple_casualties": False,
        "structural_damage": True,
        "road_blocked": False,
        "report_status": report_status,
        "disaster_id": DISASTER_ID if report_status == "VERIFIED" else None,
        "reviewed_by_id": ADMIN_ID if report_status != "PENDING" else None,
        "reviewed_at": now if report_status != "PENDING" else None,
        "rejection_reason": None,
        "photo_count": 0,
        "created_at": now,
    }


def disaster_row(ds="ACTIVE"):
    now = datetime.utcnow()
    return {
        "id": DISASTER_ID,
        "tracking_id": TRACKING_ID,
        "type": "FIRE",
        "severity": "HIGH",
        "disaster_status": ds,
        "description": "Fire at warehouse",
        "latitude": 53.3498,
        "longitude": -6.2603,
        "location_address": "Grand Canal Dock, Dublin 2",
        "affected_area": None,
        "people_affected": 10,
        "multiple_casualties": False,
        "structural_damage": True,
        "road_blocked": False,
        "assigned_to_id": ADMIN_ID,
        "assigned_to_name": "Admin",
        "assigned_to_phone": None,
        "assigned_department": "FIRE",
        "response_time": None,
        "resolved_time": None,
        "resolution_notes": None,
        "created_by_id": CITIZEN_ID,
        "disaster_metadata": None,
        "report_count": 1,
        "units_assigned": 0,
        "created_at": now,
        "updated_at": now,
    }


def unit_row(unit_status="AVAILABLE"):
    now = datetime.utcnow()
    return {
        "id": UNIT_ID,
        "unit_code": "FIR-001",
        "unit_name": "Fire Engine 1",
        "unit_type": "FIRE_ENGINE",
        "department": "FIRE",
        "unit_status": unit_status,
        "station_name": "Dublin Fire HQ",
        "station_address": "Townsend St",
        "station_lat": 53.3459,
        "station_lon": -6.2551,
        "capacity": 4,
        "current_crew_count": 2,
        "assigned_units_count": 2,
        "total_deployments": 5,
        "last_deployed_at": None,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }


def user_row():
    now = datetime.utcnow()
    return {
        "id": CITIZEN_ID,
        "full_name": "Test User",
        "email": "user@example.com",
        "phone_number": "+353871234567",
        "role": "RESIDENT",
        "status": "ACTIVE",
        "user_type": "citizen",
        "department": None,
        "employee_id": None,
        "reports_count": 0,
        "verified_reports": 0,
        "rejected_reports": 0,
        "is_assigned": False,
        "assigned_units_count": 0,
        "commanding_units_count": 0,
        "current_unit_codes": [],
        "reviews_count": 0,
        "created_at": now,
        "updated_at": now,
    }


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@pytest.fixture
def admin_client(mock_db):
    app.dependency_overrides[get_current_team_member] = lambda: ADMIN_MEMBER
    app.dependency_overrides[get_current_user]        = lambda: ADMIN_MEMBER
    app.dependency_overrides[get_db]                  = lambda: mock_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, mock_db
    app.dependency_overrides.clear()


@pytest.fixture
def citizen_client(mock_db):
    app.dependency_overrides[get_current_user] = lambda: CITIZEN
    app.dependency_overrides[get_current_team_member] = lambda: (_ for _ in ()).throw(
        HTTPException(status_code=403, detail="Forbidden")
    )
    app.dependency_overrides[get_db] = lambda: mock_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, mock_db
    app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════
# FLOW 1: Disaster Reporting
# ══════════════════════════════════════════════════════════
class TestDisasterReportingFlow:

    @pytest.mark.asyncio
    async def test_citizen_submits_report(self, citizen_client):
        """FLOW-01-01: Citizen submits a report → 201, report_status=PENDING."""
        client, mock_db = citizen_client

        created_report = report_row("PENDING")
        mock_db.execute.side_effect = [
            make_mock_result(row={"id": REPORT_ID}),  # INSERT RETURNING
            make_mock_result(row=created_report),       # fetch created report
        ]

        response = await client.post(
            "/api/v1/disaster-reports/",
            json={
                "user_id": CITIZEN_ID,
                "location_address": "Grand Canal Dock, Dublin 2",
                "disaster_type": "FIRE",
                "severity": "HIGH",
                "description": "Fire at warehouse",
                "latitude": 53.3498,
                "longitude": -6.2603,
                "people_affected": 10,
                "multiple_casualties": False,
                "structural_damage": True,
                "road_blocked": False,
                "photos": [],
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["report_status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_admin_views_pending_reports(self, admin_client):
        """FLOW-01-02: Admin fetches pending reports → key is 'pending_reports'."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(rows=[report_row("PENDING")])

        response = await client.get("/api/v1/disaster-reports/pending/all")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Response shape: {"pending_reports": [...], "count": N}
        assert "pending_reports" in data

    @pytest.mark.asyncio
    async def test_admin_verifies_report(self, admin_client):
        """FLOW-01-03: Admin verifies report → AdminReviewResponse with report_status=VERIFIED."""
        client, mock_db = admin_client

        seq_result = MagicMock()
        seq_result.scalar.return_value = 42

        gate_result = MagicMock()
        gate_result.first.return_value = {"id": REPORT_ID}

        mock_db.execute.side_effect = [
            make_mock_result(row=report_row("PENDING")),
            MagicMock(),   # CREATE SEQUENCE
            seq_result,    # nextval
            make_mock_result(row={"id": ADMIN_ID, "full_name": "Admin", "department": "FIRE"}),
            make_mock_result(row={"max_people": 10, "any_casualties": False, "any_damage": True, "any_blocked": False}),
            MagicMock(),   # INSERT disaster
            gate_result,   # gate UPDATE RETURNING
        ]

        response = await client.post(
            f"/api/v1/disaster-reports/{REPORT_ID}/review",
            json={
                "action": "verified",
                "reviewed_by_id": ADMIN_ID,
                "rejection_reason": "",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # AdminReviewResponse has report_status not action
        assert data["report_status"] == "VERIFIED"
        assert data["report_id"] == REPORT_ID

    @pytest.mark.asyncio
    async def test_admin_rejects_report(self, admin_client):
        """FLOW-01-04: Admin rejects a false alarm → report_status=REJECTED."""
        client, mock_db = admin_client

        gate_result = MagicMock()
        gate_result.first.return_value = {"id": REPORT_ID}

        mock_db.execute.side_effect = [
            make_mock_result(row=report_row("PENDING")),
            gate_result,
        ]

        response = await client.post(
            f"/api/v1/disaster-reports/{REPORT_ID}/review",
            json={
                "action": "rejected",
                "reviewed_by_id": ADMIN_ID,
                "rejection_reason": "False alarm confirmed",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["report_status"] == "REJECTED"

    @pytest.mark.asyncio
    async def test_citizen_cannot_review_report(self, citizen_client):
        """FLOW-01-05: Citizens cannot review reports → 403."""
        client, _ = citizen_client
        response = await client.post(
            f"/api/v1/disaster-reports/{REPORT_ID}/review",
            json={"action": "verified", "reviewed_by_id": CITIZEN_ID, "rejection_reason": ""},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ══════════════════════════════════════════════════════════
# FLOW 2: Disaster Lifecycle
# ══════════════════════════════════════════════════════════
class TestDisasterLifecycleFlow:

    @pytest.mark.asyncio
    async def test_disaster_visible_in_active_list(self, admin_client):
        """FLOW-02-01: New disaster appears in /disasters/active."""
        client, mock_db = admin_client
        mock_db.execute.side_effect = [
            make_mock_result(rows=[disaster_row("ACTIVE")]),
            make_mock_result(row={
                "critical_count": 0, "active_count": 1,
                "resolved_count": 0, "monitoring_count": 0, "archived_count": 0,
            }),
            make_mock_result(rows=[]),
        ]
        response = await client.get("/api/v1/disasters/active")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_dispatch_unit_to_disaster(self, admin_client):
        """FLOW-02-02: Admin dispatches unit → deployment created."""
        client, mock_db = admin_client

        claim_result = MagicMock()
        claim_result.first.return_value = {"id": UNIT_ID}

        # disaster SQL uses lat/lon aliases
        disaster = disaster_row("ACTIVE")
        disaster["lat"] = 53.3498
        disaster["lon"] = -6.2603

        mock_db.execute.side_effect = [
            make_mock_result(row=disaster),
            make_mock_result(row=unit_row("AVAILABLE")),
            claim_result,
            MagicMock(),
            make_mock_result(row={"distance_km": 2.5}),
            MagicMock(),
        ]

        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/dispatch",
            json={"unit_ids": [UNIT_ID], "priority_level": "URGENT"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["units_dispatched"][0]["unit_id"] == UNIT_ID

    @pytest.mark.asyncio
    async def test_resolve_disaster(self, admin_client):
        """FLOW-02-03: Resolving disaster completes deployments and frees units."""
        client, mock_db = admin_client
        mock_db.execute.side_effect = [
            make_mock_result(row=disaster_row("ACTIVE")),
            MagicMock(), MagicMock(), MagicMock(),
        ]
        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/resolve",
            json={"resolution_notes": "Fire extinguished."},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["disaster_status"] == "RESOLVED"

    @pytest.mark.asyncio
    async def test_cannot_resolve_already_resolved(self, admin_client):
        """FLOW-02-04: Re-resolving a RESOLVED disaster returns 400."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(row=disaster_row("RESOLVED"))
        response = await client.post(
            f"/api/v1/disasters/{DISASTER_ID}/resolve",
            json={"resolution_notes": "Again."},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ══════════════════════════════════════════════════════════
# FLOW 3: Live Map
# ══════════════════════════════════════════════════════════
class TestLiveMapFlow:

    @pytest.mark.asyncio
    async def test_live_map_returns_disasters(self, citizen_client):
        """FLOW-03-01: GET /live-map/disasters returns disaster list."""
        client, _ = citizen_client

        from app.api.v1.live_map import get_live_map_service_dependency
        mock_service = AsyncMock()
        mock_service.get_active_disasters.return_value = [{
            "id": DISASTER_ID,
            "tracking_id": TRACKING_ID,
            "type": "FIRE",
            "severity": "HIGH",
            "status": "ACTIVE",
            "report_status": "VERIFIED",
            "location": {"lat": 53.3498, "lon": -6.2603},
            "description": "Fire at warehouse",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "is_user_reported": True,
            "photo_count": 0,
        }]
        app.dependency_overrides[get_live_map_service_dependency] = lambda: mock_service

        response = await client.get("/api/v1/live-map/disasters?bounds=53.30,-6.35,53.40,-6.20")
        app.dependency_overrides.pop(get_live_map_service_dependency, None)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_live_map_missing_bounds_is_422(self, citizen_client):
        """FLOW-03-02: Missing bounds param returns 422."""
        client, _ = citizen_client
        response = await client.get("/api/v1/live-map/disasters")
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ══════════════════════════════════════════════════════════
# FLOW 4: User Management
# ══════════════════════════════════════════════════════════
class TestUserManagementFlow:

    @pytest.mark.asyncio
    async def test_list_users_admin_only(self, admin_client):
        """FLOW-04-01: GET /users/ requires admin token."""
        client, mock_db = admin_client
        mock_db.execute.side_effect = [
            make_mock_result(rows=[user_row()]),   # citizen query
            make_mock_result(rows=[]),              # team member query (empty)
        ]
        response = await client.get("/api/v1/users/")
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_list_users_citizen_forbidden(self, citizen_client):
        """FLOW-04-02: Citizens cannot list all users → 403."""
        client, _ = citizen_client
        response = await client.get("/api/v1/users/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_get_user_by_id(self, admin_client):
        """FLOW-04-03: Admin fetches user by ID."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(row=user_row())
        response = await client.get(f"/api/v1/users/{CITIZEN_ID}")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["id"] == CITIZEN_ID

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, admin_client):
        """FLOW-04-04: 404 for unknown user."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(rows=[])
        response = await client.get(f"/api/v1/users/{uuid.uuid4()}")
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ══════════════════════════════════════════════════════════
# FLOW 5: Auth
# ══════════════════════════════════════════════════════════
class TestAuthFlow:

    @pytest.mark.asyncio
    async def test_team_login_wrong_password_is_401(self):
        """FLOW-05-01: Wrong password returns 401."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("app.services.emergency_team_service.EmergencyTeamService.login_team_member") as mock_login:
                mock_login.side_effect = ValueError("Invalid credentials")
                response = await client.post(
                    "/api/v1/emergency-team/login",
                    json={"email": "wrong@drs.ie", "password": "wrongpassword"},
                )
                assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio
    async def test_protected_endpoint_no_token_is_401_or_403(self):
        """FLOW-05-02: No token on protected endpoint returns 401 or 403."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/disasters/active")
            assert response.status_code in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            )

    @pytest.mark.asyncio
    async def test_citizen_otp_request(self):
        """FLOW-05-03: POST /auth/login sends OTP or 404 for unregistered number."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with patch("app.services.user_service.UserService.login_user") as mock_login:
                mock_login.return_value = {"message": "OTP sent", "otp_expires_in": 300}
                response = await client.post(
                    "/api/v1/auth/login",
                    json={"phone_number": "+353871234567"},
                )
                assert response.status_code in (status.HTTP_200_OK, status.HTTP_404_NOT_FOUND)