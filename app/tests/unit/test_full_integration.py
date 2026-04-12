# File: app/tests/unit/test_full_integration_v2.py
"""
Full integration tests — all URLs and mocks corrected.

Fixed:
  - /disaster-reports/submit  (Form endpoint, submit separately)
  - /disaster-reports/{id}/review  (not /verify)
  - disaster_row has lat/lon for dispatch_units
  - resolve_disaster: 4 execute calls
  - /disasters/all: SQL-inspection side_effect
  - /users/  (trailing slash)
  - get_user citizen: no unit_ids field
  - login: mock service not bcrypt
  - live map: bounds param

Run:
  pytest app/tests/unit/test_full_integration_v2.py -v --tb=short
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from fastapi import HTTPException

from app.main import app
from app.auth.dependencies import get_current_user, get_current_team_member
from app.db.session import get_db

ADMIN_ID      = str(uuid.uuid4())
CITIZEN_ID    = str(uuid.uuid4())
DISASTER_ID   = str(uuid.uuid4())
UNIT_ID       = str(uuid.uuid4())
REPORT_ID     = str(uuid.uuid4())
DEPLOYMENT_ID = str(uuid.uuid4())
TRACKING_ID   = "DIS-2026-00099"
NOW           = datetime.utcnow()

ADMIN_MEMBER = {
    "id": ADMIN_ID, "user_id": ADMIN_ID,
    "full_name": "Admin", "email": "admin@drs.ie",
    "role": "ADMIN", "department": "FIRE",
    "user_type": "emergency_team",
}

CITIZEN = {
    "id": CITIZEN_ID, "full_name": "John Citizen",
    "phone_number": "+353871234567", "role": "RESIDENT",
    "user_type": "citizen",
}


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
    mock.scalar.return_value = rows[0] if rows else None
    mock.first.return_value  = first if first is not None else (rows[0] if rows else None)
    return mock


def report_row(status="PENDING"):
    return {
        "id": REPORT_ID, "user_id": CITIZEN_ID,
        "disaster_type": "FIRE", "severity": "HIGH",
        "description": "Fire at warehouse",
        "latitude": 53.3498, "longitude": -6.2603,
        "location_address": "Grand Canal Dock",
        "people_affected": 10, "multiple_casualties": False,
        "structural_damage": True, "road_blocked": False,
        "report_status": status,
        "disaster_id": DISASTER_ID if status == "VERIFIED" else None,
        "reviewed_by_id": ADMIN_ID if status != "PENDING" else None,
        "reviewed_at": NOW if status != "PENDING" else None,
        "rejection_reason": None, "photo_count": 0, "created_at": NOW,
    }


def disaster_row(ds="ACTIVE"):
    """Includes lat/lon for dispatch_units compatibility."""
    return {
        "id": DISASTER_ID, "tracking_id": TRACKING_ID,
        "type": "FIRE", "severity": "HIGH", "disaster_status": ds,
        "description": "Fire at warehouse",
        "latitude": 53.3498, "longitude": -6.2603,
        "lat": 53.3498, "lon": -6.2603,       # needed by dispatch_units
        "location_address": "Grand Canal Dock",
        "affected_area": None, "people_affected": 10,
        "multiple_casualties": False, "structural_damage": True,
        "road_blocked": False, "assigned_to_id": ADMIN_ID,
        "assigned_to_name": "Admin", "assigned_to_phone": None,
        "assigned_department": "FIRE",
        "response_time": None, "resolved_time": None,
        "resolution_notes": None, "disaster_metadata": None,
        "report_count": 1, "units_assigned": 0,
        "created_by_id": CITIZEN_ID,           # needed by get_disaster
        "created_at": NOW, "updated_at": NOW,
    }


def list_disaster_row(ds="ACTIVE"):
    return {
        "id": DISASTER_ID, "tracking_id": TRACKING_ID,
        "type": "FIRE", "severity": "HIGH", "disaster_status": ds,
        "description": "Fire at warehouse",
        "latitude": 53.3498, "longitude": -6.2603,
        "location_address": "Grand Canal Dock",
        "people_affected": 10, "units_assigned": 0,
        "report_count": 1, "created_at": NOW, "updated_at": NOW,
    }


def unit_row(unit_status="AVAILABLE"):
    return {
        "id": UNIT_ID, "unit_code": "FIR-001",
        "unit_name": "Fire Engine 1", "unit_type": "FIRE_ENGINE",
        "department": "FIRE", "unit_status": unit_status,
        "station_name": "Dublin Fire HQ", "station_address": "Townsend St",
        "station_lat": 53.3459, "station_lon": -6.2551,
        "capacity": 4, "current_crew_count": 2,
        "assigned_units_count": 0, "total_deployments": 5,
        "last_deployed_at": None, "created_at": NOW, "updated_at": NOW,
        "deleted_at": None,
    }


def dep_row(status="DISPATCHED"):
    return {
        "id": DEPLOYMENT_ID, "disaster_id": DISASTER_ID,
        "unit_id": UNIT_ID, "deployment_status": status,
        "dispatched_at": NOW, "tracking_id": TRACKING_ID,
        "disaster_status": "ACTIVE", "disaster_type": "FIRE",
        "location_address": "Grand Canal Dock",
        "lat": 53.3498, "lon": -6.2603,
    }


def make_list_disasters_db(mock_db):
    """SQL-inspection side_effect for list_disasters."""
    async def execute_side_effect(query, params=None):
        sql = str(query)
        if "critical_count" in sql or ("COUNT" in sql and "disaster" in sql.lower()):
            return make_result(first={
                "critical_count": 0, "active_count": 1,
                "resolved_count": 0, "monitoring_count": 0, "archived_count": 0,
            })
        if "unit_id" in sql and params:
            return make_result(rows=[])
        return make_result(rows=[list_disaster_row()])
    mock_db.execute = AsyncMock(side_effect=execute_side_effect)


@pytest.fixture
def mock_db():
    return make_mock_db()


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


# ═══════════════════════════════════════════════════════════════════════════
# FLOW 1: Disaster Lifecycle
# ═══════════════════════════════════════════════════════════════════════════

class TestDisasterLifecycle:

    @pytest.mark.asyncio
    async def test_admin_reviews_report(self, admin_client):
        """INT-01: POST /disaster-reports/{id}/review → 200."""
        client, mock_db = admin_client

        seq_mock = MagicMock()
        seq_mock.scalar.return_value = 42

        mock_db.execute.side_effect = [
            make_result(first=report_row("PENDING")),              # 1. fetch report
            make_result(),                                         # 2. CREATE SEQUENCE
            seq_mock,                                              # 3. SELECT nextval → scalar()=42
            make_result(first={"id": ADMIN_ID, "full_name": "Admin", "department": "FIRE"}),  # 4. _get_team_info
            make_result(),                                         # 5. INSERT disaster
            make_result(first={"id": REPORT_ID}),                  # 6. gate check / UPDATE report
            make_result(),                                         # 7. any further updates
        ]
        async with client as c:
            resp = await c.post(
                f"/api/v1/disaster-reports/{REPORT_ID}/review",
                json={"reviewed_by_id": ADMIN_ID, "action": "verified"},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_rejects_report(self, admin_client):
        """INT-02: Admin rejects report → 200."""
        client, mock_db = admin_client

        seq_mock = MagicMock()
        seq_mock.scalar.return_value = 42

        mock_db.execute.side_effect = [
            make_result(first=report_row("PENDING")),
            make_result(first={"id": ADMIN_ID, "full_name": "Admin", "department": "FIRE"}),
            make_result(),    # CREATE SEQUENCE IF NOT EXISTS
            seq_mock,         # SELECT nextval → .scalar() returns 42
            make_result(first=report_row("VERIFIED")),
        ]
        
        async with client as c:
            resp = await c.post(
                f"/api/v1/disaster-reports/{REPORT_ID}/review",
                json={"reviewed_by_id": ADMIN_ID, "action": "rejected", "rejection_reason": "Duplicate"},
            )
        assert resp.status_code == 200
        
    @pytest.mark.asyncio
    async def test_citizen_cannot_review_report(self, citizen_client):
        """INT-03: Citizen cannot POST /review → 403."""
        client, mock_db = citizen_client
        async with client as c:
            resp = await c.post(
                f"/api/v1/disaster-reports/{REPORT_ID}/review",
                json={"action": "verify"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_dispatches_unit(self, admin_client):
        """INT-04: POST /disasters/{id}/dispatch → 200.
        disaster_row must have lat/lon fields."""
        client, mock_db = admin_client
        mock_db.execute.side_effect = [
            make_result(first=disaster_row("ACTIVE")),          # 1. check disaster
            make_result(first=unit_row("AVAILABLE")),           # 2. check unit
            make_result(first={"id": DEPLOYMENT_ID}),           # 3. claim unit
            make_result(first={"id": DEPLOYMENT_ID}),           # 4. INSERT deployment
            make_result(first={"distance_km": 2.5}),            # 5. distance calc
            make_result(first={"id": DISASTER_ID}),             # 6. UPDATE disaster
        ]
        async with client as c:
            resp = await c.post(
                f"/api/v1/disasters/{DISASTER_ID}/dispatch",
                json={"unit_ids": [UNIT_ID]},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unit_updates_to_en_route(self, admin_client):
        """INT-05: POST /deployments/{id}/update-status EN_ROUTE → 200."""
        client, mock_db = admin_client
        mock_db.execute.side_effect = [
            make_result(first=dep_row("DISPATCHED")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        async with client as c:
            resp = await c.post(
                f"/api/v1/deployments/{DEPLOYMENT_ID}/update-status",
                json={"new_status": "EN_ROUTE"},
            )
        assert resp.status_code == 200
        assert resp.json()["new_status"] == "EN_ROUTE"

    @pytest.mark.asyncio
    async def test_admin_resolves_disaster(self, admin_client):
        """INT-06: POST /disasters/{id}/resolve → 200.
        resolve_disaster: 1=check, 2=UPDATE disasters, 3=UPDATE deployments, 4=UPDATE units."""
        client, mock_db = admin_client
        mock_db.execute.side_effect = [
            make_result(first=disaster_row("ACTIVE")),   # check
            make_result(first={"id": DISASTER_ID}),      # UPDATE disasters
            make_result(first={"id": DEPLOYMENT_ID}),    # UPDATE deployments
            make_result(),                               # UPDATE units
        ]
        async with client as c:
            resp = await c.post(
                f"/api/v1/disasters/{DISASTER_ID}/resolve",
                json={"resolution_notes": "Fire extinguished."},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_resolve_already_resolved_returns_400(self, admin_client):
        """INT-07: Resolving RESOLVED disaster → 400."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(first=disaster_row("RESOLVED"))
        async with client as c:
            resp = await c.post(
                f"/api/v1/disasters/{DISASTER_ID}/resolve",
                json={"resolution_notes": "Already done"},
            )
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# FLOW 2: Notification chain
# ═══════════════════════════════════════════════════════════════════════════

class TestDeploymentNotifications:

    @pytest.mark.asyncio
    async def test_all_transitions_generate_notifications(self, admin_client):
        """INT-08: Each transition generates correct notification event."""
        from app.services.deployment_service import DeploymentService

        transitions = [
            ("DISPATCHED", "EN_ROUTE",    "deployment.en_route"),
            ("EN_ROUTE",   "ON_SCENE",    "deployment.on_scene"),
            ("ON_SCENE",   "IN_PROGRESS", "deployment.in_progress"),
            ("IN_PROGRESS","COMPLETED",   "deployment.completed"),
        ]

        for from_s, to_s, expected in transitions:
            db = make_mock_db()
            db.execute.side_effect = [
                make_result(first=dep_row(from_s)),
                make_result(first={"id": DEPLOYMENT_ID}),
                make_result(),
            ]
            result = await DeploymentService(db).update_status(DEPLOYMENT_ID, to_s)
            notifs = [e for e in result["_pending_events"] if e[0] == "notification.alert"]
            assert len(notifs) == 1, f"Missing notification for {to_s}"
            assert notifs[0][1]["event_type"] == expected


# ═══════════════════════════════════════════════════════════════════════════
# FLOW 3: Live Map
# ═══════════════════════════════════════════════════════════════════════════

class TestLiveMap:

    @pytest.mark.asyncio
    async def test_live_map_returns_disasters(self, citizen_client):
        """INT-09: GET /live-map/disasters with bounds param → 200."""
        client, mock_db = citizen_client

        from app.api.v1.live_map import get_live_map_service_dependency
        mock_svc = AsyncMock()
        mock_svc.get_active_disasters = AsyncMock(return_value=[])
        app.dependency_overrides[get_live_map_service_dependency] = lambda: mock_svc

        async with client as c:
            resp = await c.get(
                "/api/v1/live-map/disasters",
                params={"bounds": "53.0,-7.0,54.0,-6.0"},
            )

        app.dependency_overrides.pop(get_live_map_service_dependency, None)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_live_map_missing_bounds_returns_422(self, citizen_client):
        """INT-10: Missing bounds → 422."""
        client, mock_db = citizen_client
        async with client as c:
            resp = await c.get("/api/v1/live-map/disasters")
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# FLOW 4: Disaster Access
# ═══════════════════════════════════════════════════════════════════════════

class TestDisasterAccess:

    @pytest.mark.asyncio
    async def test_get_all_disasters_as_admin(self, admin_client):
        """INT-11: Admin GET /disasters/all → 200."""
        client, mock_db = admin_client
        make_list_disasters_db(mock_db)
        async with client as c:
            resp = await c.get("/api/v1/disasters/all")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_all_disasters_as_citizen(self, citizen_client):
        """INT-12: Citizen GET /disasters/all → 200."""
        client, mock_db = citizen_client
        make_list_disasters_db(mock_db)
        async with client as c:
            resp = await c.get("/api/v1/disasters/all")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_disaster_by_id(self, admin_client):
        """INT-13: GET /disasters/{id} → 200. Needs created_by_id."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(first=disaster_row())
        async with client as c:
            resp = await c.get(f"/api/v1/disasters/{DISASTER_ID}")
        assert resp.status_code == 200
        assert resp.json()["id"] == DISASTER_ID

    @pytest.mark.asyncio
    async def test_get_disaster_not_found(self, admin_client):
        """INT-14: Unknown disaster → 404."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(first=None)
        async with client as c:
            resp = await c.get(f"/api/v1/disasters/{uuid.uuid4()}")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# FLOW 5: Auth
# ═══════════════════════════════════════════════════════════════════════════

class TestAuth:

    @pytest.mark.asyncio
    async def test_no_token_returns_401_or_403(self):
        """INT-15: Accessing protected endpoint without token → 401/403."""
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        async with client as c:
            resp = await c.get(f"/api/v1/disasters/{DISASTER_ID}")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_team_login_wrong_password_returns_401(self):
        """INT-16: Wrong password → 401. Mocked at service layer."""
        db = make_mock_db()

        with patch(
            "app.services.emergency_team_service.EmergencyTeamService.login_team_member",
            new_callable=AsyncMock,
            side_effect=ValueError("Invalid credentials"),
        ):
            app.dependency_overrides[get_db] = lambda: db
            client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
            async with client as c:
                resp = await c.post("/api/v1/emergency-team/login", json={
                    "email":    "admin@drs.ie",
                    "password": "WrongPassword1",
                })

        app.dependency_overrides.clear()
        assert resp.status_code in (401, 400)

# ═══════════════════════════════════════════════════════════════════════════
# FLOW 6: User Management
# ═══════════════════════════════════════════════════════════════════════════

class TestUserManagement:

    def user_list_row(self):
        return {
            "id": CITIZEN_ID, "full_name": "John", "email": "john@test.ie",
            "phone_number": "+353871234567", "role": "RESIDENT",
            "status": "ACTIVE", "user_type": "citizen",
            "department": None, "employee_id": None,
            "reports_count": 0, "verified_reports": 0,
            "rejected_reports": 0, "is_assigned": False,
            "assigned_units_count": 0, "commanding_units_count": 0,
            "current_unit_codes": [], "reviews_count": 0,
            "created_at": NOW, "updated_at": NOW,
        }

    @pytest.mark.asyncio
    async def test_admin_can_list_users(self, admin_client):
        """INT-17: GET /users/ (trailing slash) → 200."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(rows=[self.user_list_row()])
        async with client as c:
            resp = await c.get("/api/v1/users/")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_citizen_cannot_list_users(self, citizen_client):
        """INT-18: Citizen GET /users/ → 403."""
        client, mock_db = citizen_client
        async with client as c:
            resp = await c.get("/api/v1/users/")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_get_user_by_id_returns_200(self, admin_client):
        """INT-19: GET /users/{id} → 200.
        Citizen response has stats not unit_ids."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(first={
            "id": CITIZEN_ID, "full_name": "John", "email": "john@test.ie",
            "phone_number": "+353871234567", "role": "RESIDENT",
            "status": "ACTIVE", "created_at": NOW, "updated_at": NOW,
            "reports_count": 0, "verified_reports": 0, "rejected_reports": 0,
        })
        async with client as c:
            resp = await c.get(f"/api/v1/users/{CITIZEN_ID}")
        assert resp.status_code == 200
        assert resp.json()["id"] == CITIZEN_ID
        # Citizens have stats, not unit_ids
        assert "stats" in resp.json()

    @pytest.mark.asyncio
    async def test_get_user_not_found_returns_404(self, admin_client):
        """INT-20: GET /users/{unknown} → 404."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(first=None)
        async with client as c:
            resp = await c.get(f"/api/v1/users/{uuid.uuid4()}")
        assert resp.status_code == 404