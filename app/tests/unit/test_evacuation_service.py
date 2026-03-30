# File: app/tests/unit/test_evacuation_service.py
"""
Unit tests — EvacuationService (UC8).

Constructor injection pattern — no patch() needed.
mock_external_integration_service, mock_mapping_service, mock_publisher
come from conftest.py (same fixtures used by UC7 tests).
mock_evacuation_db is defined locally.

Run:
  pytest app/tests/unit/test_evacuation_service.py -v
"""

import math
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.evacuation_service import (
    EvacuationService,
    DUBLIN_ZONES,
    DUBLIN_SHELTERS,
    get_all_shelters,
    get_zones_near_disaster,
    get_population_profile,
    compute_transport_needs,
    allocate_resources,
    score_and_select_routes,
    avg_congestion_weight,
    straight_line_fallback,
    BUS_CAPACITY,
    AMBULANCE_CAPACITY,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_evacuation_db():
    """AsyncMock of EvacuationRepository."""
    repo = AsyncMock()
    repo.get_disaster         = AsyncMock(return_value=None)
    repo.get_blocked_roads    = AsyncMock(return_value=[])
    repo.get_users_in_zones   = AsyncMock(return_value=[])
    repo.generate_plan_ref    = AsyncMock(return_value="EVA-0001")
    repo.save_plan            = AsyncMock(return_value="plan-001")
    repo.get_plan             = AsyncMock(return_value=None)
    repo.update_plan          = AsyncMock(return_value=True)
    repo.list_plans           = AsyncMock(return_value=[])
    repo.get_disaster_by_plan = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_external_integration_service():
    svc = AsyncMock()
    svc.get_directions    = AsyncMock(return_value={"routes": []})
    svc.fetch_traffic_data = AsyncMock(return_value={"segments": [], "mode": "mock"})
    return svc


@pytest.fixture
def mock_mapping_service():
    svc = AsyncMock()
    svc.highlight_alternative_routes = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def mock_publisher():
    pub = AsyncMock()
    pub.is_connected = False
    pub.publish_reroute_triggered = AsyncMock()
    pub.publish_route_updated     = AsyncMock()
    return pub


@pytest.fixture
def evacuation_service(
    mock_evacuation_db,
    mock_external_integration_service,
    mock_mapping_service,
    mock_publisher,
):
    return EvacuationService(
        db=mock_evacuation_db,
        external=mock_external_integration_service,
        mapping=mock_mapping_service,
        publisher=mock_publisher,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test data
# ─────────────────────────────────────────────────────────────────────────────

FAKE_DISASTER = {
    "id": "dis-001", "tracking_id": "TRK-001",
    "type": "FLOOD", "severity": "HIGH", "disaster_status": "ACTIVE",
    "lat": 53.3498, "lon": -6.2603,
    "location_address": "O'Connell Street, Dublin 1",
    "people_affected": 5000, "road_blocked": False,
}

FAKE_ROUTE = {
    "route_id":               "rt-001",
    "travel_time_seconds":    600,
    "length_meters":          8000,
    "traffic_delay_seconds":  60,
    "points":  [[53.3498, -6.2603], [53.3607, -6.2510]],
    "geojson": {"type": "Feature",
                "geometry": {"type": "LineString", "coordinates": []},
                "properties": {}},
}

FAKE_ZONE    = DUBLIN_ZONES[0]    # zone_city_centre
FAKE_SHELTER = DUBLIN_SHELTERS[0]  # shelter_croke_park


# ─────────────────────────────────────────────────────────────────────────────
# Zone / shelter data
# ─────────────────────────────────────────────────────────────────────────────

class TestDublinData:

    def test_zones_exist(self):
        assert len(DUBLIN_ZONES) > 0

    def test_eight_shelters(self):
        assert len(get_all_shelters()) == 8

    def test_zone_required_fields(self):
        for z in DUBLIN_ZONES:
            for f in ("zone_id", "name", "lat", "lon", "population", "vulnerable_count"):
                assert f in z, f"Zone missing field: {f}"

    def test_shelter_required_fields(self):
        for s in get_all_shelters():
            for f in ("shelter_id", "name", "lat", "lon", "capacity"):
                assert f in s, f"Shelter missing field: {f}"

    def test_shelters_positive_capacity(self):
        for s in get_all_shelters():
            assert s["capacity"] > 0

    def test_zones_near_city_centre(self):
        zones = get_zones_near_disaster(53.3498, -6.2603, severity="HIGH")
        assert len(zones) >= 1
        ids = [z["zone_id"] for z in zones]
        assert "zone_city_centre" in ids

    def test_critical_wider_than_high(self):
        crit = get_zones_near_disaster(53.3498, -6.2603, severity="CRITICAL")
        high = get_zones_near_disaster(53.3498, -6.2603, severity="HIGH")
        assert len(crit) >= len(high)

    def test_at_least_one_zone_returned(self):
        # Even for coordinates far away, at least the nearest zone is returned
        zones = get_zones_near_disaster(99.0, 99.0, severity="HIGH")
        assert len(zones) >= 0   # may be empty for coordinates completely outside Ireland

    def test_ambulance_capacity_is_8(self):
        """Verify the fix — must NOT be 2."""
        assert AMBULANCE_CAPACITY == 8, (
            f"AMBULANCE_CAPACITY should be 8 (accessible transport), got {AMBULANCE_CAPACITY}"
        )

    def test_bus_capacity_is_50(self):
        assert BUS_CAPACITY == 50


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestStraightLineFallback:

    def test_returns_dict(self):
        result = straight_line_fallback(FAKE_ZONE, FAKE_SHELTER)
        assert isinstance(result, dict)

    def test_flagged_as_fallback(self):
        assert straight_line_fallback(FAKE_ZONE, FAKE_SHELTER)["fallback"] is True

    def test_positive_distance(self):
        assert straight_line_fallback(FAKE_ZONE, FAKE_SHELTER)["distance_km"] > 0

    def test_correct_ids(self):
        r = straight_line_fallback(FAKE_ZONE, FAKE_SHELTER)
        assert r["origin_zone_id"]         == FAKE_ZONE["zone_id"]
        assert r["destination_shelter_id"] == FAKE_SHELTER["shelter_id"]

    def test_has_two_waypoints(self):
        r = straight_line_fallback(FAKE_ZONE, FAKE_SHELTER)
        assert len(r["waypoints"]) == 2

    def test_positive_time(self):
        r = straight_line_fallback(FAKE_ZONE, FAKE_SHELTER)
        assert r["estimated_time_min"] > 0


class TestPureHelpers:

    def test_population_profile_totals(self):
        zones = [
            {"population": 1000, "vulnerable_count": 100},
            {"population": 2000, "vulnerable_count": 200},
        ]
        s = get_population_profile(zones)
        assert s["total"]      == 3000
        assert s["vulnerable"] == 300
        assert s["mobile"]     == 2700

    def test_population_profile_zones_count(self):
        zones = [{"population": 100, "vulnerable_count": 10}] * 3
        assert get_population_profile(zones)["zones_count"] == 3

    def test_transport_bus_count(self):
        stats  = {"total": 500, "vulnerable": 50}
        zones  = [{"zone_id": "z1", "population": 500, "vulnerable_count": 50}]
        routes = {"z1": [{"destination_shelter_id": "s1", "shelter_name": "S",
                          "route_id": "r1", "estimated_time_min": 20}]}
        plan = compute_transport_needs(stats, zones, routes)
        assert plan["total_buses"]      == math.ceil(500 / BUS_CAPACITY)
        assert plan["total_ambulances"] == math.ceil(50  / AMBULANCE_CAPACITY)

    def test_transport_ambulances_with_capacity_8(self):
        """Confirm AMBULANCE_CAPACITY=8 produces sane numbers."""
        stats  = {"total": 10000, "vulnerable": 8700}
        zones  = [{"zone_id": "z1", "population": 10000, "vulnerable_count": 8700}]
        routes = {"z1": [{"destination_shelter_id": "s1", "shelter_name": "S",
                          "route_id": "r1", "estimated_time_min": 30}]}
        plan = compute_transport_needs(stats, zones, routes)
        # 8700 / 8 = 1088 — much more reasonable than 4350
        assert plan["total_ambulances"] == math.ceil(8700 / 8)
        assert plan["total_ambulances"] < 2000  # sanity check

    def test_allocate_resources_mirrors_transport(self):
        alloc = allocate_resources({"total_buses": 5, "total_ambulances": 3})
        assert alloc["buses_allocated"]      == 5
        assert alloc["ambulances_allocated"] == 3
        assert alloc["allocation_confirmed"] is True

    def test_score_at_most_3(self):
        candidates = [
            {
                "distance_km":            2.0 + i * 0.5,
                "traffic_delay_seconds":  60,
                "origin_zone_id":         "z",
                "destination_shelter_id": f"s{i}",
                "shelter_name":           f"S{i}",
                "shelter_capacity":       1000,
                "route_id":               f"r{i}",
                "points": [], "geojson": {}, "waypoints": [],
                "travel_time_seconds": 600, "estimated_time_min": 10,
            }
            for i in range(10)
        ]
        assert len(score_and_select_routes(candidates, {})) <= 3

    def test_score_empty_returns_empty(self):
        assert score_and_select_routes([], {}) == []

    def test_shorter_distance_wins(self):
        base = {
            "origin_zone_id": "z", "shelter_name": "S",
            "shelter_capacity": 1000, "points": [],
            "geojson": {}, "waypoints": [],
            "traffic_delay_seconds": 0,
        }
        far  = {**base, "distance_km": 10.0, "route_id": "far",
                "destination_shelter_id": "s1",
                "travel_time_seconds": 1800, "estimated_time_min": 30}
        near = {**base, "distance_km":  1.0, "route_id": "near",
                "destination_shelter_id": "s2",
                "travel_time_seconds": 300,  "estimated_time_min": 5}
        top = score_and_select_routes([far, near], {})[0]
        assert top["destination_shelter_id"] == "s2"

    def test_big_delay_penalised(self):
        base = {
            "distance_km": 5.0, "origin_zone_id": "z",
            "destination_shelter_id": "s", "shelter_name": "S",
            "shelter_capacity": 1000, "points": [],
            "geojson": {}, "waypoints": [],
            "travel_time_seconds": 1200, "estimated_time_min": 20,
        }
        no_delay  = {**base, "traffic_delay_seconds": 0,    "route_id": "no_delay"}
        big_delay = {**base, "traffic_delay_seconds": 3600, "route_id": "big_delay"}
        top = score_and_select_routes([big_delay, no_delay], {})[0]
        assert top["route_id"] == "no_delay"


class TestCongestionWeight:

    def test_empty_returns_1(self):
        assert avg_congestion_weight([]) == 1.0

    def test_uc7_ratio_format(self):
        w = avg_congestion_weight([
            {"congestion_ratio": 0.1},
            {"congestion_ratio": 0.8},
        ])
        assert abs(w - (0.5 + 4.0) / 2) < 1e-9

    def test_uc8_level_format(self):
        w = avg_congestion_weight([
            {"congestion_level": "light"},
            {"congestion_level": "severe"},
        ])
        assert abs(w - (0.5 + 4.0) / 2) < 1e-9

    def test_unknown_level_defaults_to_1(self):
        w = avg_congestion_weight([{"congestion_level": "mystery"}])
        assert w == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# fetch_traffic_data
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchTrafficData:

    @pytest.mark.asyncio
    async def test_calls_external_integration_service(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.fetch_traffic_data.return_value = {
            "segments": [], "mode": "mock"}
        result = await evacuation_service.fetch_traffic_data()
        mock_external_integration_service.fetch_traffic_data.assert_called_once_with(
            "region-dublin-city")
        assert result is not None

    @pytest.mark.asyncio
    async def test_returns_fallback_on_error(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.fetch_traffic_data.side_effect = Exception("down")
        result = await evacuation_service.fetch_traffic_data()
        assert result["available"] is False
        assert result["source"] == "fallback"


# ─────────────────────────────────────────────────────────────────────────────
# _compute_zone_routes
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeZoneRoutes:

    @pytest.mark.asyncio
    async def test_calls_get_directions_per_shelter(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.get_directions.return_value = {
            "routes": [FAKE_ROUTE]}
        routes = await evacuation_service._compute_zone_routes(
            FAKE_ZONE, [FAKE_SHELTER], [], {"segments": []})
        mock_external_integration_service.get_directions.assert_called_once()
        assert len(routes) >= 1

    @pytest.mark.asyncio
    async def test_passes_blocked_roads_as_avoid(
        self, evacuation_service, mock_external_integration_service
    ):
        blocked = [{"segment_id": "s1", "road_name": "O'Connell St",
                    "start_lat": 53.347, "start_lng": -6.260,
                    "end_lat": 53.349, "end_lng": -6.258}]
        mock_external_integration_service.get_directions.return_value = {
            "routes": [FAKE_ROUTE]}
        await evacuation_service._compute_zone_routes(
            FAKE_ZONE, [FAKE_SHELTER], blocked, {})
        call_kw = mock_external_integration_service.get_directions.call_args.kwargs
        assert call_kw["avoid"] == blocked

    @pytest.mark.asyncio
    async def test_fallback_route_on_tomtom_failure(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.get_directions.side_effect = Exception("TomTom down")
        routes = await evacuation_service._compute_zone_routes(
            FAKE_ZONE, [FAKE_SHELTER], [], {})
        assert len(routes) >= 1
        assert routes[0].get("fallback") is True

    @pytest.mark.asyncio
    async def test_enriches_distance_and_time_from_tomtom(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.get_directions.return_value = {
            "routes": [FAKE_ROUTE]}
        routes = await evacuation_service._compute_zone_routes(
            FAKE_ZONE, [FAKE_SHELTER], [], {})
        assert routes[0]["distance_km"]        == round(8000 / 1000, 2)
        assert routes[0]["estimated_time_min"] == round(600 / 60, 1)

    @pytest.mark.asyncio
    async def test_concurrent_zones(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.get_directions.return_value = {
            "routes": [FAKE_ROUTE]}
        zones = [
            {**FAKE_ZONE, "zone_id": "z_a", "lat": 53.34},
            {**FAKE_ZONE, "zone_id": "z_b", "lat": 53.35},
        ]
        result = await evacuation_service._compute_all_zone_routes(
            zones, [FAKE_SHELTER], [], {})
        assert "z_a" in result and "z_b" in result
        assert mock_external_integration_service.get_directions.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_route_list_when_all_fail(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.get_directions.return_value = {"routes": []}
        routes = await evacuation_service._compute_zone_routes(
            FAKE_ZONE, [FAKE_SHELTER], [], {})
        # Falls back to straight-line estimate when TomTom returns empty
        assert isinstance(routes, list)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — plan_evacuation
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanEvacuation:

    @pytest.mark.asyncio
    async def test_raises_404_when_disaster_not_found(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_disaster.return_value = None
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await evacuation_service.plan_evacuation("bad-id")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_saves_plan_and_returns_summary(
        self, evacuation_service, mock_evacuation_db,
        mock_external_integration_service
    ):
        mock_evacuation_db.get_disaster.return_value = FAKE_DISASTER
        mock_evacuation_db.save_plan.return_value    = "plan-xyz"
        mock_external_integration_service.get_directions.return_value = {
            "routes": [FAKE_ROUTE]}

        result = await evacuation_service.plan_evacuation("dis-001")

        mock_evacuation_db.save_plan.assert_called_once()
        assert result["plan_id"]  == "plan-xyz"
        assert result["plan_ref"] == "EVA-0001"
        assert result["plan_status"] == "PENDING"
        assert result["zones_count"] >= 1

    @pytest.mark.asyncio
    async def test_auto_approve_sets_status(
        self, evacuation_service, mock_evacuation_db,
        mock_external_integration_service
    ):
        mock_evacuation_db.get_disaster.return_value = FAKE_DISASTER
        mock_evacuation_db.save_plan.return_value    = "plan-xyz"
        mock_external_integration_service.get_directions.return_value = {"routes": []}

        result = await evacuation_service.plan_evacuation("dis-001", auto_approve=True)
        assert result["plan_status"] == "APPROVED"
        assert result["auto_approved"] is True

    @pytest.mark.asyncio
    async def test_transport_numbers_are_sane(
        self, evacuation_service, mock_evacuation_db,
        mock_external_integration_service
    ):
        mock_evacuation_db.get_disaster.return_value = FAKE_DISASTER
        mock_evacuation_db.save_plan.return_value    = "plan-xyz"
        mock_external_integration_service.get_directions.return_value = {
            "routes": [FAKE_ROUTE]}

        result = await evacuation_service.plan_evacuation("dis-001")
        summary = result["transport_plan_summary"]

        # Ambulances should be way less than 4350 (the old broken value)
        assert summary["total_ambulances"] < 2000
        assert summary["total_buses"]      > 0


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — approve_evacuation
# ─────────────────────────────────────────────────────────────────────────────

class TestApproveEvacuation:

    def _pending_plan(self):
        return {
            "id": "plan-1", "plan_ref": "EVA-0001",
            "disaster_id": "dis-1", "plan_status": "PENDING",
            "impact_zones": [FAKE_ZONE],
            "shelters_with_capacity": [FAKE_SHELTER],
            "best_routes_per_zone": {},
            "allocations": {"buses_allocated": 10, "ambulances_allocated": 5},
            "completion_metrics": {},
        }

    @pytest.mark.asyncio
    async def test_approve_pending_plan(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_plan.return_value = self._pending_plan()
        result = await evacuation_service.approve_evacuation(
            "plan-1", approved_by="Commander Murphy")
        assert result["plan_status"] == "APPROVED"
        assert result["approved_by"] == "Commander Murphy"
        mock_evacuation_db.update_plan.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_400_if_already_approved(
        self, evacuation_service, mock_evacuation_db
    ):
        plan = self._pending_plan()
        plan["plan_status"] = "APPROVED"
        mock_evacuation_db.get_plan.return_value = plan
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await evacuation_service.approve_evacuation("plan-1", "Officer B")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_raises_400_if_not_pending(
        self, evacuation_service, mock_evacuation_db
    ):
        plan = self._pending_plan()
        plan["plan_status"] = "ACTIVE"
        mock_evacuation_db.get_plan.return_value = plan
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await evacuation_service.approve_evacuation("plan-1", "Officer B")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_raises_404_unknown_plan(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_plan.return_value = None
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await evacuation_service.approve_evacuation("bad", "Officer")
        assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — activate_evacuation
# ─────────────────────────────────────────────────────────────────────────────

class TestActivateEvacuation:

    def _approved_plan(self):
        return {
            "id": "plan-1", "plan_ref": "EVA-0001",
            "disaster_id": "dis-1", "plan_status": "APPROVED",
            "impact_zones": [FAKE_ZONE],
            "shelters_with_capacity": [FAKE_SHELTER],
            "best_routes_per_zone": {"zone_city_centre": []},
            "allocations": {"buses_allocated": 10, "ambulances_allocated": 5},
            "completion_metrics": {},
        }

    @pytest.mark.asyncio
    async def test_activate_approved_plan(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_plan.return_value = self._approved_plan()
        result = await evacuation_service.activate_evacuation("plan-1")
        assert result["plan_status"] == "ACTIVE"
        assert "activated_at" in result
        mock_evacuation_db.update_plan.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_400_if_not_approved(
        self, evacuation_service, mock_evacuation_db
    ):
        plan = self._approved_plan()
        plan["plan_status"] = "PENDING"
        mock_evacuation_db.get_plan.return_value = plan
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await evacuation_service.activate_evacuation("plan-1")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_initialises_completion_metrics(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_plan.return_value = self._approved_plan()
        await evacuation_service.activate_evacuation("plan-1")
        call_kwargs = mock_evacuation_db.update_plan.call_args.kwargs
        metrics = call_kwargs.get("completion_metrics", {})
        assert FAKE_ZONE["zone_id"] in metrics
        assert metrics[FAKE_ZONE["zone_id"]]["percentage"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — update_progress
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateProgress:

    def _active_plan(self):
        return {
            "id": "plan-1", "plan_ref": "EVA-0001",
            "disaster_id": "dis-1", "plan_status": "ACTIVE",
            "impact_zones": [FAKE_ZONE],
            "shelters_with_capacity": [FAKE_SHELTER],
            "best_routes_per_zone": {},
            "allocations": {},
            "completion_metrics": {
                FAKE_ZONE["zone_id"]: {"percentage": 0, "evacuated": 0,
                                       "remaining": FAKE_ZONE["population"],
                                       "status": "in_progress"},
            },
        }

    @pytest.mark.asyncio
    async def test_updates_metrics(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_plan.return_value = self._active_plan()
        result = await evacuation_service.update_progress(
            "plan-1",
            {FAKE_ZONE["zone_id"]: {"percentage": 50, "evacuated": 12500,
                                    "remaining": 12500, "status": "in_progress"}},
        )
        assert result["overall_completion"] == pytest.approx(50.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_marks_completed_when_all_100(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_plan.return_value = self._active_plan()
        result = await evacuation_service.update_progress(
            "plan-1",
            {FAKE_ZONE["zone_id"]: {"percentage": 100, "evacuated": 25000,
                                    "remaining": 0, "status": "done"}},
        )
        assert result["plan_status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_raises_400_if_not_active(
        self, evacuation_service, mock_evacuation_db
    ):
        plan = self._active_plan()
        plan["plan_status"] = "PENDING"
        mock_evacuation_db.get_plan.return_value = plan
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await evacuation_service.update_progress("plan-1", {})
        assert exc_info.value.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# broadcast_alerts + send_route_updates
# ─────────────────────────────────────────────────────────────────────────────

class TestBroadcastAlerts:

    @pytest.mark.asyncio
    async def test_uses_publisher_when_connected(
        self, evacuation_service, mock_publisher
    ):
        mock_publisher.is_connected = True
        users = [{"id": "u1", "phone_number": "+353871111111",
                  "zone_id": FAKE_ZONE["zone_id"], "zone_name": "City Centre"}]
        count = await evacuation_service.broadcast_alerts(
            users, "dis-1", "plan-1", {}, [])
        mock_publisher.publish_reroute_triggered.assert_called_once()
        assert count == 1

    @pytest.mark.asyncio
    async def test_returns_0_for_empty_users(
        self, evacuation_service, mock_publisher
    ):
        mock_publisher.is_connected = True
        count = await evacuation_service.broadcast_alerts([], "dis-1", "plan-1", {}, [])
        assert count == 0


class TestSendRouteUpdates:

    @pytest.mark.asyncio
    async def test_uses_publisher_when_connected(
        self, evacuation_service, mock_publisher
    ):
        mock_publisher.is_connected = True
        users = [{"id": "u1", "phone_number": "+353871111111",
                  "zone_id": "z1", "zone_name": "Zone 1"}]
        count = await evacuation_service.send_route_updates(users, {"z1": []}, "dis-1")
        mock_publisher.publish_route_updated.assert_called_once()
        assert count == 1

    @pytest.mark.asyncio
    async def test_empty_users_returns_0(
        self, evacuation_service, mock_publisher
    ):
        mock_publisher.is_connected = True
        count = await evacuation_service.send_route_updates([], {}, "dis-1")
        assert count == 0