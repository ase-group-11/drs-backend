# File: app/tests/unit/test_deployment_status_notification.py
"""
Deployment status + notification tests — updated for new _publish_pending_events.

Run:
  pytest app/tests/unit/test_deployment_status_notification.py -v
"""

import json
import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.auth.dependencies import get_current_user, get_current_team_member
from app.db.session import get_db

DEPLOYMENT_ID = str(uuid.uuid4())
DISASTER_ID   = str(uuid.uuid4())
UNIT_ID       = str(uuid.uuid4())
TRACKING_ID   = "DIS-2026-00042"
NOW           = datetime.utcnow()

ADMIN_USER = {
    "id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),
    "full_name": "Admin", "role": "ADMIN",
    "user_type": "emergency_team",
}

STAFF_USER = {
    "id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),
    "full_name": "Staff", "role": "STAFF",
    "user_type": "emergency_team",
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


def dep_row(deployment_status="DISPATCHED", disaster_status="ACTIVE"):
    return {
        "id": DEPLOYMENT_ID, "disaster_id": DISASTER_ID,
        "unit_id": UNIT_ID, "deployment_status": deployment_status,
        "dispatched_at": NOW, "tracking_id": TRACKING_ID,
        "disaster_status": disaster_status, "disaster_type": "FIRE",
        "location_address": "Grand Canal Dock, Dublin 2",
        "lat": 53.3498, "lon": -6.2603,
    }


def mission_row():
    return {
        "deployment_id": DEPLOYMENT_ID, "deployment_status": "EN_ROUTE",
        "dispatched_at": NOW, "assigned_at": NOW, "en_route_at": NOW,
        "on_scene_at": None, "in_progress_at": None, "completed_at": None,
        "priority_level": "HIGH", "special_instructions": None,
        "situation_report": None, "minor_injuries": None, "serious_injuries": None,
        "disaster_id": DISASTER_ID, "tracking_id": TRACKING_ID,
        "disaster_type": "FIRE", "severity": "HIGH",
        "disaster_status": "ACTIVE", "description": "Fire at warehouse",
        "location_address": "Grand Canal Dock, Dublin 2",
        "people_affected": 10, "lat": 53.3498, "lon": -6.2603,
        "distance_km": 2.5,
    }


def completed_mission_row():
    return {
        "deployment_id": DEPLOYMENT_ID, "deployment_status": "COMPLETED",
        "dispatched_at": NOW, "completed_at": NOW,
        "priority_level": "HIGH", "situation_report": "Resolved",
        "minor_injuries": 0, "serious_injuries": 0,
        "disaster_id": DISASTER_ID, "tracking_id": TRACKING_ID,
        "disaster_type": "FIRE", "severity": "HIGH",
        "disaster_status": "RESOLVED",
        "location_address": "Grand Canal Dock, Dublin 2",
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
# SERVICE: valid transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestDeploymentStatusTransitions:

    @pytest.mark.asyncio
    async def test_dispatched_to_en_route_succeeds(self):
        """DS-01: DISPATCHED → EN_ROUTE valid."""
        from app.services.deployment_service import DeploymentService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first=dep_row("DISPATCHED")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        result = await DeploymentService(db).update_status(DEPLOYMENT_ID, "EN_ROUTE")
        assert result["new_status"] == "EN_ROUTE"
        assert result["previous_status"] == "DISPATCHED"

    @pytest.mark.asyncio
    async def test_en_route_to_on_scene_succeeds(self):
        """DS-02: EN_ROUTE → ON_SCENE valid."""
        from app.services.deployment_service import DeploymentService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first=dep_row("EN_ROUTE")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        result = await DeploymentService(db).update_status(DEPLOYMENT_ID, "ON_SCENE")
        assert result["new_status"] == "ON_SCENE"

    @pytest.mark.asyncio
    async def test_on_scene_to_in_progress_succeeds(self):
        """DS-03: ON_SCENE → IN_PROGRESS valid."""
        from app.services.deployment_service import DeploymentService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first=dep_row("ON_SCENE")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        result = await DeploymentService(db).update_status(DEPLOYMENT_ID, "IN_PROGRESS")
        assert result["new_status"] == "IN_PROGRESS"

    @pytest.mark.asyncio
    async def test_in_progress_to_completed_succeeds(self):
        """DS-04: IN_PROGRESS → COMPLETED valid."""
        from app.services.deployment_service import DeploymentService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first=dep_row("IN_PROGRESS")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        result = await DeploymentService(db).update_status(DEPLOYMENT_ID, "COMPLETED")
        assert result["new_status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_invalid_transition_raises_400(self):
        """DS-05: DISPATCHED → ON_SCENE invalid."""
        from app.services.deployment_service import DeploymentService
        from fastapi import HTTPException
        db = make_mock_db()
        db.execute.return_value = make_result(first=dep_row("DISPATCHED"))
        with pytest.raises(HTTPException) as exc:
            await DeploymentService(db).update_status(DEPLOYMENT_ID, "ON_SCENE")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_deployment_not_found_raises_404(self):
        """DS-06: Missing deployment → 404."""
        from app.services.deployment_service import DeploymentService
        from fastapi import HTTPException
        db = make_mock_db()
        db.execute.return_value = make_result(first=None)
        with pytest.raises(HTTPException) as exc:
            await DeploymentService(db).update_status(DEPLOYMENT_ID, "EN_ROUTE")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cancellation_from_dispatched(self):
        """DS-07: DISPATCHED → CANCELLED valid."""
        from app.services.deployment_service import DeploymentService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first=dep_row("DISPATCHED")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        result = await DeploymentService(db).update_status(DEPLOYMENT_ID, "CANCELLED")
        assert result["new_status"] == "CANCELLED"


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE: notifications generated
# ─────────────────────────────────────────────────────────────────────────────

class TestDeploymentStatusNotifications:

    @pytest.mark.asyncio
    async def test_en_route_generates_notification(self):
        """DS-08: EN_ROUTE → notification.alert with HIGH severity."""
        from app.services.deployment_service import DeploymentService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first=dep_row("DISPATCHED")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        result = await DeploymentService(db).update_status(DEPLOYMENT_ID, "EN_ROUTE")
        notifs = [e for e in result["_pending_events"] if e[0] == "notification.alert"]
        assert len(notifs) == 1
        assert notifs[0][1]["event_type"] == "deployment.en_route"
        assert notifs[0][1]["severity"] == "HIGH"

    @pytest.mark.asyncio
    async def test_on_scene_generates_notification(self):
        """DS-09: ON_SCENE → notification.alert."""
        from app.services.deployment_service import DeploymentService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first=dep_row("EN_ROUTE")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        result = await DeploymentService(db).update_status(DEPLOYMENT_ID, "ON_SCENE")
        notifs = [e for e in result["_pending_events"] if e[0] == "notification.alert"]
        assert len(notifs) == 1
        assert notifs[0][1]["event_type"] == "deployment.on_scene"

    @pytest.mark.asyncio
    async def test_completed_generates_notification_and_unit_completed(self):
        """DS-10: COMPLETED → notification.alert + disaster.unit_completed."""
        from app.services.deployment_service import DeploymentService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first=dep_row("IN_PROGRESS")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        result = await DeploymentService(db).update_status(DEPLOYMENT_ID, "COMPLETED")
        events     = result["_pending_events"]
        notifs     = [e for e in events if e[0] == "notification.alert"]
        unit_compl = [e for e in events if e[0] == "disaster.unit_completed"]
        assert len(notifs) == 1
        assert notifs[0][1]["event_type"] == "deployment.completed"
        assert notifs[0][1]["severity"]   == "MEDIUM"
        assert len(unit_compl) == 1

    @pytest.mark.asyncio
    async def test_cancelled_generates_low_severity_notification(self):
        """DS-11: CANCELLED → LOW severity notification."""
        from app.services.deployment_service import DeploymentService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first=dep_row("DISPATCHED")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        result = await DeploymentService(db).update_status(DEPLOYMENT_ID, "CANCELLED")
        notifs = [e for e in result["_pending_events"] if e[0] == "notification.alert"]
        assert notifs[0][1]["severity"] == "LOW"

    @pytest.mark.asyncio
    async def test_notification_contains_correct_data_fields(self):
        """DS-12: Notification payload has deployment_id, disaster_id, tracking_id."""
        from app.services.deployment_service import DeploymentService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first=dep_row("DISPATCHED")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        result = await DeploymentService(db).update_status(DEPLOYMENT_ID, "EN_ROUTE")
        # Payload fields are flat (no nested "data" key in deployment_service output)
        payload = [e for e in result["_pending_events"] if e[0] == "notification.alert"][0][1]
        assert payload["deployment_id"] == DEPLOYMENT_ID
        assert payload["disaster_id"]   == DISASTER_ID
        assert payload["tracking_id"]   == TRACKING_ID
        assert payload["unit_id"]       == UNIT_ID

    @pytest.mark.asyncio
    async def test_backup_requested_generates_backup_event(self):
        """DS-13: request_immediate_backup=True → disaster.backup_requested."""
        from app.services.deployment_service import DeploymentService
        db = make_mock_db()
        db.execute.side_effect = [
            make_result(first=dep_row("ON_SCENE")),
            make_result(first={"id": DEPLOYMENT_ID}),
            make_result(),
        ]
        result = await DeploymentService(db).update_status(
            DEPLOYMENT_ID, "IN_PROGRESS",
            request_immediate_backup=True,
            additional_resources=["AMBULANCE"],
        )
        backup = [e for e in result["_pending_events"] if e[0] == "disaster.backup_requested"]
        assert len(backup) == 1
        assert backup[0][1]["requesting_unit"] == UNIT_ID


# ─────────────────────────────────────────────────────────────────────────────
# API: update-status endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestDeploymentStatusAPI:

    @pytest.mark.asyncio
    async def test_update_status_en_route_returns_200(self, admin_client):
        """DS-14: POST /update-status EN_ROUTE → 200."""
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
    async def test_update_status_invalid_transition_returns_400(self, admin_client):
        """DS-15: Invalid transition → 400."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(first=dep_row("DISPATCHED"))
        async with client as c:
            resp = await c.post(
                f"/api/v1/deployments/{DEPLOYMENT_ID}/update-status",
                json={"new_status": "COMPLETED"},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_status_not_found_returns_404(self, admin_client):
        """DS-16: Unknown deployment → 404."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(first=None)
        async with client as c:
            resp = await c.post(
                f"/api/v1/deployments/{DEPLOYMENT_ID}/update-status",
                json={"new_status": "EN_ROUTE"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_status_missing_body_returns_422(self, admin_client):
        """DS-17: Missing new_status → 422."""
        client, mock_db = admin_client
        async with client as c:
            resp = await c.post(
                f"/api/v1/deployments/{DEPLOYMENT_ID}/update-status",
                json={},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_active_missions_returns_200(self, admin_client):
        """DS-18: GET /active → 200 with active_missions list."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(rows=[mission_row()])
        async with client as c:
            resp = await c.get(f"/api/v1/deployments/unit/{UNIT_ID}/active")
        assert resp.status_code == 200
        assert "active_missions" in resp.json()

    @pytest.mark.asyncio
    async def test_get_active_missions_empty_list(self, admin_client):
        """DS-19: Returns empty active_missions list when none."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(rows=[])
        async with client as c:
            resp = await c.get(f"/api/v1/deployments/unit/{UNIT_ID}/active")
        assert resp.status_code == 200
        assert resp.json()["active_missions"] == []

    @pytest.mark.asyncio
    async def test_get_completed_missions_returns_200(self, admin_client):
        """DS-20: GET /completed → 200 with completed_missions list."""
        client, mock_db = admin_client
        mock_db.execute.return_value = make_result(rows=[completed_mission_row()])
        async with client as c:
            resp = await c.get(f"/api/v1/deployments/unit/{UNIT_ID}/completed")
        assert resp.status_code == 200
        assert "completed_missions" in resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION PUBLISHER
# ─────────────────────────────────────────────────────────────────────────────

class TestNotificationPublisher:

    def test_build_envelope_structure(self):
        """DS-21: _build_envelope returns correct JSON."""
        from app.services.notification_publisher import _build_envelope
        result = json.loads(_build_envelope(
            "deployment", "deployment.en_route", "HIGH",
            "Unit En Route", "Unit is en route", {"unit_id": UNIT_ID},
        ))
        assert result["service"]    == "deployment"
        assert result["event_type"] == "deployment.en_route"
        assert result["severity"]   == "HIGH"
        assert result["colour"]     == "orange"
        assert result["data"]["unit_id"] == UNIT_ID
        assert "timestamp" in result

    def test_colour_map_critical_red(self):
        """DS-22: CRITICAL → red."""
        from app.services.notification_publisher import _build_envelope
        assert json.loads(_build_envelope("s", "e", "CRITICAL", "T", "M", None))["colour"] == "red"

    def test_colour_map_low_blue(self):
        """DS-23: LOW → blue."""
        from app.services.notification_publisher import _build_envelope
        assert json.loads(_build_envelope("s", "e", "LOW", "T", "M", None))["colour"] == "blue"

    def test_colour_map_medium_yellow(self):
        """DS-24: MEDIUM → yellow."""
        from app.services.notification_publisher import _build_envelope
        assert json.loads(_build_envelope("s", "e", "MEDIUM", "T", "M", None))["colour"] == "yellow"

    def test_publish_alert_calls_redis(self):
        """DS-25: publish_alert publishes to Redis app_alerts channel."""
        from app.services.notification_publisher import publish_alert
        with patch("app.services.notification_publisher._get_sync") as mock_get:
            mock_redis = MagicMock()
            mock_get.return_value = mock_redis
            result = publish_alert(
                service="deployment", event_type="deployment.en_route",
                title="Unit En Route", message="On way", severity="HIGH",
                data={"unit_id": UNIT_ID},
            )
        assert result is True
        mock_redis.publish.assert_called_once()
        assert mock_redis.publish.call_args[0][0] == "app_alerts"

    def test_publish_alert_returns_false_on_failure(self):
        """DS-26: publish_alert returns False on Redis error."""
        from app.services.notification_publisher import publish_alert
        with patch("app.services.notification_publisher._get_sync") as mock_get:
            mock_redis = MagicMock()
            mock_redis.publish.side_effect = Exception("Redis down")
            mock_get.return_value = mock_redis
            result = publish_alert("deployment", "test", "T", "M")
        assert result is False

    def test_publish_pending_events_uses_rabbitmq_service(self):
        """DS-27: notification.alert events go to publish_alert (Redis directly)."""
        from app.api.v1.deployment import _publish_pending_events

        events = [("notification.alert", {
            "service":    "deployment",
            "event_type": "deployment.en_route",
            "severity":   "HIGH",
            "title":      "Unit En Route",
            "message":    "On the way",
            "deployment_id":   DEPLOYMENT_ID,
            "disaster_id":     DISASTER_ID,
            "tracking_id":     TRACKING_ID,
            "unit_id":         UNIT_ID,
            "previous_status": "DISPATCHED",
            "new_status":      "EN_ROUTE",
        })]

        with patch("app.services.notification_publisher.publish_alert",
                   return_value=True) as mock_pub:
            _publish_pending_events(events)

        mock_pub.assert_called_once_with(
            service="deployment",
            event_type="deployment.en_route",
            severity="HIGH",
            title="Unit En Route",
            message="On the way",
            data={
                "deployment_id":   DEPLOYMENT_ID,
                "disaster_id":     DISASTER_ID,
                "tracking_id":     TRACKING_ID,
                "unit_id":         UNIT_ID,
                "previous_status": "DISPATCHED",
                "new_status":      "EN_ROUTE",
            },
        )

    def test_publish_pending_events_publishes_multiple_events(self):
        """DS-28: Non-notification events go to get_rabbitmq_service."""
        from app.api.v1.deployment import _publish_pending_events

        events = [
            ("disaster.unit_completed", {"disaster_id": DISASTER_ID}),
            ("disaster.backup_requested", {"unit_id": UNIT_ID}),
        ]

        with patch("app.services.rabbitmq_service.get_rabbitmq_service") as mock_get:
            mock_svc = MagicMock()
            mock_get.return_value = mock_svc
            _publish_pending_events(events)

        assert mock_svc.publish.call_count == 2
        mock_svc.publish.assert_any_call("disaster.unit_completed", events[0][1])
        mock_svc.publish.assert_any_call("disaster.backup_requested", events[1][1])