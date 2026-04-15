# File: app/tests/integration/test_evacuation_api.py
"""
Integration tests — Evacuation API (UC8).

Overrides get_evacuation_service() dependency with an AsyncMock,
mirrors the pattern of other integration tests in this codebase.

Run:
  pytest app/tests/integration/test_evacuation_api.py -v
"""

import pytest
import pytest_asyncio
import httpx
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock

from app.main import app
from app.api.v1.evacuation import get_evacuation_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_evacuation_service():
    """AsyncMock of EvacuationService — injected via dependency override."""
    svc = AsyncMock()
    svc.plan_evacuation             = AsyncMock()
    svc.approve_evacuation          = AsyncMock()
    svc.activate_evacuation         = AsyncMock()
    svc.get_progress                = AsyncMock()
    svc.update_progress             = AsyncMock()
    svc.handle_route_blockage       = AsyncMock()
    svc.handle_disaster_escalation  = AsyncMock()
    svc.get_plan                    = AsyncMock()
    svc.list_plans                  = AsyncMock(return_value=[])
    return svc


@pytest_asyncio.fixture
async def client(mock_evacuation_service):
    """HTTP client with auth and service both overridden."""
    app.dependency_overrides[get_evacuation_service]   = lambda: mock_evacuation_service
    transport = httpx.ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Phase 1 — POST /evacuations/plan
# ---------------------------------------------------------------------------

class TestPlanEvacuation:

    @pytest.mark.asyncio
    async def test_returns_201_on_success(self, client, mock_evacuation_service):
        mock_evacuation_service.plan_evacuation.return_value = {
            "plan_id": "plan-1", "plan_ref": "EVA-0001",
            "disaster_id": "dis-1", "plan_status": "PENDING",
            "zones_count": 3, "shelters_count": 8,
            "total_population_affected": 60000, "total_vulnerable": 8000,
            "transport_plan_summary": {"total_buses": 1200, "total_ambulances": 4000},
            "auto_approved": False, "message": "Plan created.",
        }
        r = await client.post("/api/v1/evacuations/plan",
                              json={"disaster_id": "dis-1"})
        assert r.status_code == 201
        assert r.json()["plan_ref"] == "EVA-0001"

    @pytest.mark.asyncio
    async def test_auto_approve_flag_passed(self, client, mock_evacuation_service):
        mock_evacuation_service.plan_evacuation.return_value = {
            "plan_id": "plan-2", "plan_ref": "EVA-0002",
            "disaster_id": "dis-1", "plan_status": "APPROVED",
            "zones_count": 2, "shelters_count": 8,
            "total_population_affected": 40000, "total_vulnerable": 5000,
            "transport_plan_summary": {"total_buses": 800, "total_ambulances": 2500},
            "auto_approved": True, "message": "Plan auto-approved.",
        }
        r = await client.post("/api/v1/evacuations/plan",
                              json={"disaster_id": "dis-1", "auto_approve": True})
        assert r.status_code == 201
        assert r.json()["auto_approved"] is True
        mock_evacuation_service.plan_evacuation.assert_called_once_with(
            disaster_id="dis-1", auto_approve=True)

    @pytest.mark.asyncio
    async def test_422_missing_disaster_id(self, client, mock_evacuation_service):
        r = await client.post("/api/v1/evacuations/plan", json={})
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_forwards_http_exceptions(self, client, mock_evacuation_service):
        from fastapi import HTTPException
        mock_evacuation_service.plan_evacuation.side_effect = HTTPException(
            status_code=404, detail="Disaster not found.")
        r = await client.post("/api/v1/evacuations/plan",
                              json={"disaster_id": "no-such"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Phase 2 — POST /evacuations/{plan_id}/approve
# ---------------------------------------------------------------------------

class TestApproveEvacuation:

    @pytest.mark.asyncio
    async def test_approve_succeeds(self, client, mock_evacuation_service):
        mock_evacuation_service.approve_evacuation.return_value = {
            "plan_id": "p1", "plan_ref": "EVA-0001",
            "plan_status": "APPROVED", "approved_by": "Commander Murphy",
            "approved_at": "2026-03-22T10:05:00", "message": "Approved.",
        }
        r = await client.post("/api/v1/evacuations/p1/approve",
                              json={"approved_by": "Commander Murphy"})
        assert r.status_code == 200
        assert r.json()["plan_status"] == "APPROVED"
        mock_evacuation_service.approve_evacuation.assert_called_once_with(
            plan_id="p1", approved_by="Commander Murphy", notes=None)

    @pytest.mark.asyncio
    async def test_422_missing_approved_by(self, client, mock_evacuation_service):
        r = await client.post("/api/v1/evacuations/p1/approve", json={})
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_forwards_400_already_approved(self, client, mock_evacuation_service):
        from fastapi import HTTPException
        mock_evacuation_service.approve_evacuation.side_effect = HTTPException(
            status_code=400, detail="Plan is already approved.")
        r = await client.post("/api/v1/evacuations/p1/approve",
                              json={"approved_by": "Officer B"})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Phase 3 — POST /evacuations/{plan_id}/activate
# ---------------------------------------------------------------------------

class TestActivateEvacuation:

    @pytest.mark.asyncio
    async def test_activate_succeeds(self, client, mock_evacuation_service):
        mock_evacuation_service.activate_evacuation.return_value = {
            "plan_id": "p1", "plan_ref": "EVA-0001",
            "plan_status": "ACTIVE", "activated_at": "2026-03-22T10:10:00",
            "alerts_sent": 247, "map_updated": True,
            "units_en_route": 2000, "zones_active": 3,
            "message": "Evacuation is live.",
        }
        r = await client.post("/api/v1/evacuations/p1/activate")
        assert r.status_code == 200
        assert r.json()["plan_status"] == "ACTIVE"
        assert r.json()["alerts_sent"] == 247
        mock_evacuation_service.activate_evacuation.assert_called_once_with(plan_id="p1")

    @pytest.mark.asyncio
    async def test_forwards_400_not_approved(self, client, mock_evacuation_service):
        from fastapi import HTTPException
        mock_evacuation_service.activate_evacuation.side_effect = HTTPException(
            status_code=400, detail="Only APPROVED plans can be activated.")
        r = await client.post("/api/v1/evacuations/p1/activate")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Phase 4 — GET/POST /evacuations/{plan_id}/progress
# ---------------------------------------------------------------------------

class TestProgress:

    @pytest.mark.asyncio
    async def test_get_progress(self, client, mock_evacuation_service):
        mock_evacuation_service.get_progress.return_value = {
            "plan_id": "p1", "plan_ref": "EVA-0001", "plan_status": "ACTIVE",
            "completion_metrics": {"zone_city_centre": {"percentage": 30}},
            "overall_completion": 30.0, "traffic_update": None,
            "last_updated": "2026-03-22T10:15:00",
        }
        r = await client.get("/api/v1/evacuations/p1/progress")
        assert r.status_code == 200
        assert r.json()["overall_completion"] == 30.0

    @pytest.mark.asyncio
    async def test_push_progress(self, client, mock_evacuation_service):
        mock_evacuation_service.update_progress.return_value = {
            "plan_id": "p1", "plan_ref": "EVA-0001", "plan_status": "ACTIVE",
            "completion_metrics": {}, "overall_completion": 60.0,
            "message": "60.0% evacuated.",
        }
        r = await client.post(
            "/api/v1/evacuations/p1/progress",
            json={"completion_metrics": {
                "zone_city_centre": {"percentage": 60, "evacuated": 15000, "remaining": 10000}
            }},
        )
        assert r.status_code == 200
        assert r.json()["overall_completion"] == 60.0

    @pytest.mark.asyncio
    async def test_push_100_marks_completed(self, client, mock_evacuation_service):
        mock_evacuation_service.update_progress.return_value = {
            "plan_id": "p1", "plan_ref": "EVA-0001", "plan_status": "COMPLETED",
            "completion_metrics": {}, "overall_completion": 100.0,
            "message": "Evacuation complete!",
        }
        r = await client.post(
            "/api/v1/evacuations/p1/progress",
            json={"completion_metrics": {
                "zone_city_centre": {"percentage": 100, "evacuated": 25000, "remaining": 0}
            }},
        )
        assert r.json()["plan_status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_422_missing_metrics(self, client, mock_evacuation_service):
        r = await client.post("/api/v1/evacuations/p1/progress", json={})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Phase 4 alt — route-blockage
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Phase 4 alt — escalation
# ---------------------------------------------------------------------------

class TestEscalation:

    @pytest.mark.asyncio
    async def test_422_missing_reason(self, client, mock_evacuation_service):
        r = await client.post(
            "/api/v1/evacuations/p1/escalate",
            json={"new_zone_ids": ["zone_northside"]},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestCRUD:

    @pytest.mark.asyncio
    async def test_list_plans(self, client, mock_evacuation_service):
        mock_evacuation_service.list_plans.return_value = [
            {"plan_id": "p1", "plan_ref": "EVA-0001", "plan_status": "ACTIVE"},
            {"plan_id": "p2", "plan_ref": "EVA-0002", "plan_status": "COMPLETED"},
        ]
        r = await client.get("/api/v1/evacuations/")
        assert r.status_code == 200
        assert r.json()["count"] == 2

    @pytest.mark.asyncio
    async def test_list_plans_with_filter(self, client, mock_evacuation_service):
        mock_evacuation_service.list_plans.return_value = []
        r = await client.get("/api/v1/evacuations/?disaster_id=dis-1")
        assert r.status_code == 200
        mock_evacuation_service.list_plans.assert_called_once_with(disaster_id="dis-1")

    @pytest.mark.asyncio
    async def test_get_plan_detail(self, client, mock_evacuation_service):
        mock_evacuation_service.get_plan.return_value = {
            "id": "p1", "plan_ref": "EVA-0001", "plan_status": "ACTIVE",
            "impact_zones": [], "best_routes_per_zone": {},
        }
        r = await client.get("/api/v1/evacuations/p1")
        assert r.status_code == 200
        assert r.json()["plan_ref"] == "EVA-0001"

    @pytest.mark.asyncio
    async def test_get_plan_404_unknown(self, client, mock_evacuation_service):
        from fastapi import HTTPException
        mock_evacuation_service.get_plan.side_effect = HTTPException(
            status_code=404, detail="Evacuation plan not found.")
        r = await client.get("/api/v1/evacuations/no-plan")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Full lifecycle test
# ---------------------------------------------------------------------------

class TestFullLifecycle:

    @pytest.mark.asyncio
    async def test_pending_approved_active_completed(self, client, mock_evacuation_service):

        mock_evacuation_service.plan_evacuation.return_value = {
            "plan_id": "e2e", "plan_ref": "EVA-9999",
            "disaster_id": "dis-1", "plan_status": "PENDING",
            "zones_count": 3, "shelters_count": 8,
            "total_population_affected": 61000, "total_vulnerable": 8700,
            "transport_plan_summary": {"total_buses": 1220, "total_ambulances": 4350},
            "auto_approved": False, "message": "Plan created.",
        }
        r1 = await client.post("/api/v1/evacuations/plan",
                               json={"disaster_id": "dis-1"})
        assert r1.status_code == 201
        assert r1.json()["plan_status"] == "PENDING"

        mock_evacuation_service.approve_evacuation.return_value = {
            "plan_id": "e2e", "plan_ref": "EVA-9999", "plan_status": "APPROVED",
            "approved_by": "Chief Murphy", "approved_at": "2026-03-22T10:05:00",
            "message": "Approved.",
        }
        r2 = await client.post("/api/v1/evacuations/e2e/approve",
                               json={"approved_by": "Chief Murphy"})
        assert r2.json()["plan_status"] == "APPROVED"

        mock_evacuation_service.activate_evacuation.return_value = {
            "plan_id": "e2e", "plan_ref": "EVA-9999", "plan_status": "ACTIVE",
            "activated_at": "2026-03-22T10:10:00",
            "alerts_sent": 310, "map_updated": True,
            "units_en_route": 4350, "zones_active": 3,
            "message": "Evacuation is live.",
        }
        r3 = await client.post("/api/v1/evacuations/e2e/activate")
        assert r3.json()["plan_status"] == "ACTIVE"

        mock_evacuation_service.get_progress.return_value = {
            "plan_id": "e2e", "plan_ref": "EVA-9999", "plan_status": "ACTIVE",
            "completion_metrics": {}, "overall_completion": 45.0,
            "traffic_update": None, "last_updated": "2026-03-22T10:20:00",
        }
        r4 = await client.get("/api/v1/evacuations/e2e/progress")
        assert r4.json()["overall_completion"] == 45.0

        mock_evacuation_service.update_progress.return_value = {
            "plan_id": "e2e", "plan_ref": "EVA-9999", "plan_status": "COMPLETED",
            "completion_metrics": {}, "overall_completion": 100.0,
            "message": "Evacuation complete!",
        }
        r5 = await client.post(
            "/api/v1/evacuations/e2e/progress",
            json={"completion_metrics": {
                "zone_city_centre": {"percentage": 100, "evacuated": 25000, "remaining": 0}
            }},
        )
        assert r5.json()["plan_status"] == "COMPLETED"
        print("\n✅ Full lifecycle: PENDING → APPROVED → ACTIVE → COMPLETED")
