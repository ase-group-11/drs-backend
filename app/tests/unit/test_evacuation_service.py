# File: app/tests/unit/test_evacuation_service.py
"""
Unit tests — EvacuationService.

Mirrors test_reroute.py / test_reroute_pipeline.py exactly:
  - EvacuationService built by constructor injection, no patch() needed.
  - mock_external_integration_service, mock_mapping_service, mock_publisher
    come from conftest.py (already defined there for UC7).
  - mock_evacuation_db is defined locally (same pattern as mock_db_repository).

Run:
  pytest app/tests/unit/test_evacuation_service.py -v
"""

import math
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.evacuation_service import (
    EvacuationService,
    DUBLIN_ZONES,
    DUBLIN_SHELTERS,
    get_all_zones,
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
    _haversine,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_evacuation_db():
    """AsyncMock of EvacuationRepository — mirrors mock_db_repository from conftest."""
    repo = AsyncMock()
    repo.get_disaster       = AsyncMock(return_value=None)
    repo.get_blocked_roads  = AsyncMock(return_value=[])
    repo.get_users_in_zones = AsyncMock(return_value=[])
    repo.generate_plan_ref  = AsyncMock(return_value="EVA-0001")
    repo.save_plan          = AsyncMock(return_value="plan-001")
    repo.get_plan           = AsyncMock(return_value=None)
    repo.update_plan        = AsyncMock(return_value=True)
    repo.list_plans         = AsyncMock(return_value=[])
    repo.get_disaster_by_plan = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def evacuation_service(
    mock_evacuation_db,
    mock_external_integration_service,   # from conftest.py
    mock_mapping_service,                # from conftest.py
    mock_publisher,                      # from conftest.py
):
    """Build EvacuationService by constructor injection — no patch() needed."""
    return EvacuationService(
        db=mock_evacuation_db,
        external=mock_external_integration_service,
        mapping=mock_mapping_service,
        publisher=mock_publisher,
    )


# Fake disaster returned by mock_evacuation_db.get_disaster
FAKE_DISASTER = {
    "id": "dis-001", "tracking_id": "TRK-001",
    "type": "FLOOD", "severity": "HIGH", "disaster_status": "ACTIVE",
    "lat": 53.3498, "lon": -6.2603,
    "location_address": "O'Connell Street, Dublin 1",
    "people_affected": 5000, "road_blocked": False,
}

# Fake TomTom route dict (matches parse_routing_response output)
FAKE_ROUTE = {
    "route_id":            "rt-001",
    "travel_time_seconds": 600,
    "length_meters":       8000,
    "traffic_delay_seconds": 60,
    "points": [[53.3498, -6.2603], [53.3607, -6.2510]],
    "geojson": {"type": "Feature",
                "geometry": {"type": "LineString", "coordinates": []},
                "properties": {}},
    "instructions": [],
}

FAKE_ZONE    = DUBLIN_ZONES[0]   # zone_city_centre
FAKE_SHELTER = {**DUBLIN_SHELTERS[1], "current_occupancy": 0, "available": 22000}  # croke_park


# ---------------------------------------------------------------------------
# Zone / shelter data
# ---------------------------------------------------------------------------

class TestDublinData:

    def test_ten_zones(self):
        assert len(get_all_zones()) == 10

    def test_eight_shelters(self):
        assert len(get_all_shelters()) == 8

    def test_zone_required_fields(self):
        for z in get_all_zones():
            for f in ("zone_id", "name", "lat", "lon", "population", "vulnerable_count"):
                assert f in z

    def test_shelter_required_fields(self):
        for s in get_all_shelters():
            for f in ("shelter_id", "name", "lat", "lon", "capacity", "available"):
                assert f in s

    def test_shelters_positive_capacity(self):
        for s in get_all_shelters():
            assert s["capacity"] > 0 and s["available"] >= 0

    def test_zones_near_city_centre(self):
        zones = get_zones_near_disaster(53.3498, -6.2603, severity="HIGH")
        assert len(zones) >= 1
        assert "zone_city_centre" in [z["zone_id"] for z in zones]

    def test_zones_sorted_nearest_first(self):
        zones = get_zones_near_disaster(53.3498, -6.2603, severity="CRITICAL")
        dists = [z["distance_from_disaster_km"] for z in zones]
        assert dists == sorted(dists)

    def test_closest_has_priority_1(self):
        zones = get_zones_near_disaster(53.3498, -6.2603, severity="HIGH")
        assert zones[0]["priority"] == 1

    def test_no_zones_outside_radius(self):
        assert get_zones_near_disaster(99.0, 99.0) == []

    def test_critical_wider_than_high(self):
        crit = get_zones_near_disaster(53.3498, -6.2603, severity="CRITICAL")
        high = get_zones_near_disaster(53.3498, -6.2603, severity="HIGH")
        assert len(crit) >= len(high)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_same_point_zero(self):
        assert _haversine(53.3498, -6.2603, 53.3498, -6.2603) == 0.0

    def test_dublin_to_cork_approx_220km(self):
        assert 210 < _haversine(53.3498, -6.2603, 51.8985, -8.4756) < 230

    def test_symmetric(self):
        a = _haversine(53.3498, -6.2603, 53.3607, -6.2510)
        b = _haversine(53.3607, -6.2510, 53.3498, -6.2603)
        assert abs(a - b) < 1e-9


class TestStraightLineFallback:
    def test_returns_dict(self):
        assert isinstance(straight_line_fallback(FAKE_ZONE, FAKE_SHELTER), dict)

    def test_flagged_as_fallback(self):
        assert straight_line_fallback(FAKE_ZONE, FAKE_SHELTER)["fallback"] is True

    def test_two_waypoints(self):
        assert len(straight_line_fallback(FAKE_ZONE, FAKE_SHELTER)["waypoints"]) == 2

    def test_positive_distance(self):
        assert straight_line_fallback(FAKE_ZONE, FAKE_SHELTER)["distance_km"] > 0

    def test_correct_ids(self):
        r = straight_line_fallback(FAKE_ZONE, FAKE_SHELTER)
        assert r["origin_zone_id"]         == FAKE_ZONE["zone_id"]
        assert r["destination_shelter_id"] == FAKE_SHELTER["shelter_id"]


class TestPureHelpers:

    def test_population_profile_totals(self):
        zones = [{"population": 1000, "vulnerable_count": 100},
                 {"population": 2000, "vulnerable_count": 200}]
        s = get_population_profile(zones)
        assert s["total"] == 3000
        assert s["vulnerable"] == 300
        assert s["children"] == int(3000 * 0.15)

    def test_transport_bus_count(self):
        stats  = {"total": 500, "vulnerable": 20}
        zones  = [{"zone_id": "z1", "population": 500, "vulnerable_count": 20}]
        routes = {"z1": [{"destination_shelter_id": "s1", "shelter_name": "S",
                          "route_id": "r1", "estimated_time_min": 20}]}
        plan   = compute_transport_needs(stats, zones, routes)
        assert plan["total_buses"]      == math.ceil(500 / BUS_CAPACITY)
        assert plan["total_ambulances"] == math.ceil(20 / AMBULANCE_CAPACITY)

    def test_allocate_resources(self):
        alloc = allocate_resources({"total_buses": 5, "total_ambulances": 3})
        assert alloc["buses_allocated"]      == 5
        assert alloc["ambulances_allocated"] == 3
        assert alloc["allocation_confirmed"] is True

    def test_score_at_most_3(self):
        candidates = [
            {"distance_km": 2.0 + i * 0.5, "traffic_delay_seconds": 60,
             "origin_zone_id": "z", "destination_shelter_id": f"s{i}",
             "shelter_name": f"S{i}", "shelter_capacity": 1000, "route_id": f"r{i}",
             "points": [], "geojson": {}, "waypoints": [],
             "travel_time_seconds": 600, "estimated_time_min": 10}
            for i in range(10)
        ]
        assert len(score_and_select_routes(candidates, {})) <= 3

    def test_score_empty_returns_empty(self):
        assert score_and_select_routes([], {}) == []

    def test_shorter_distance_wins(self):
        far  = {"distance_km": 10.0, "traffic_delay_seconds": 0,
                "origin_zone_id": "z", "destination_shelter_id": "s1",
                "shelter_name": "S1", "shelter_capacity": 1000, "route_id": "r1",
                "points": [], "geojson": {}, "waypoints": [],
                "travel_time_seconds": 1800, "estimated_time_min": 30}
        near = {"distance_km":  1.0, "traffic_delay_seconds": 0,
                "origin_zone_id": "z", "destination_shelter_id": "s2",
                "shelter_name": "S2", "shelter_capacity": 1000, "route_id": "r2",
                "points": [], "geojson": {}, "waypoints": [],
                "travel_time_seconds": 300, "estimated_time_min": 5}
        assert score_and_select_routes([far, near], {})[0]["destination_shelter_id"] == "s2"

    def test_big_delay_penalised(self):
        base = {"distance_km": 5.0, "origin_zone_id": "z", "destination_shelter_id": "s",
                "shelter_name": "S", "shelter_capacity": 1000,
                "points": [], "geojson": {}, "waypoints": [],
                "travel_time_seconds": 1200, "estimated_time_min": 20}
        no_delay  = {**base, "traffic_delay_seconds": 0,    "route_id": "no_delay"}
        big_delay = {**base, "traffic_delay_seconds": 3600, "route_id": "big_delay"}
        assert score_and_select_routes([big_delay, no_delay], {})[0]["route_id"] == "no_delay"

    def test_congestion_empty_returns_1(self):
        assert avg_congestion_weight([]) == 1.0

    def test_congestion_uc7_ratio_format(self):
        w = avg_congestion_weight([{"congestion_ratio": 0.1}, {"congestion_ratio": 0.8}])
        assert abs(w - (0.5 + 4.0) / 2) < 1e-9

    def test_congestion_uc8_level_format(self):
        w = avg_congestion_weight([{"congestion_level": "light"}, {"congestion_level": "severe"}])
        assert abs(w - (0.5 + 4.0) / 2) < 1e-9


# ---------------------------------------------------------------------------
# Phase 1 — plan_evacuation
# ---------------------------------------------------------------------------

class TestPlanEvacuation:

    @pytest.mark.asyncio
    async def test_calls_get_disaster(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_disaster.return_value = FAKE_DISASTER
        mock_evacuation_db.save_plan.return_value    = "plan-001"
        mock_evacuation_db.generate_plan_ref.return_value = "EVA-0001"
        mock_evacuation_db.get_blocked_roads.return_value = []
        evacuation_service.external.fetch_traffic_data.return_value = {"segments": []}
        evacuation_service.external.get_directions.return_value = {"routes": [FAKE_ROUTE]}

        await evacuation_service.plan_evacuation("dis-001")
        mock_evacuation_db.get_disaster.assert_called_once_with("dis-001")

    @pytest.mark.asyncio
    async def test_raises_404_for_missing_disaster(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_disaster.return_value = None
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await evacuation_service.plan_evacuation("no-such")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_400_when_no_zones_in_radius(self, evacuation_service, mock_evacuation_db):
        # Disaster far outside Dublin
        mock_evacuation_db.get_disaster.return_value = {
            **FAKE_DISASTER, "lat": 99.0, "lon": 99.0}
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await evacuation_service.plan_evacuation("dis-001")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_fetches_blocked_roads_from_db(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_disaster.return_value = FAKE_DISASTER
        mock_evacuation_db.save_plan.return_value    = "plan-001"
        mock_evacuation_db.generate_plan_ref.return_value = "EVA-0001"
        evacuation_service.external.fetch_traffic_data.return_value = {"segments": []}
        evacuation_service.external.get_directions.return_value = {"routes": [FAKE_ROUTE]}

        await evacuation_service.plan_evacuation("dis-001")
        mock_evacuation_db.get_blocked_roads.assert_called_once_with("dis-001")

    @pytest.mark.asyncio
    async def test_calls_external_for_traffic(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_disaster.return_value = FAKE_DISASTER
        mock_evacuation_db.save_plan.return_value    = "plan-001"
        mock_evacuation_db.generate_plan_ref.return_value = "EVA-0001"
        evacuation_service.external.fetch_traffic_data.return_value = {"segments": []}
        evacuation_service.external.get_directions.return_value = {"routes": [FAKE_ROUTE]}

        await evacuation_service.plan_evacuation("dis-001")
        evacuation_service.external.fetch_traffic_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_calls_get_directions_for_routing(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_disaster.return_value = FAKE_DISASTER
        mock_evacuation_db.save_plan.return_value    = "plan-001"
        mock_evacuation_db.generate_plan_ref.return_value = "EVA-0001"
        evacuation_service.external.fetch_traffic_data.return_value = {"segments": []}
        evacuation_service.external.get_directions.return_value = {"routes": [FAKE_ROUTE]}

        await evacuation_service.plan_evacuation("dis-001")
        assert evacuation_service.external.get_directions.call_count >= 1

    @pytest.mark.asyncio
    async def test_persists_plan(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_disaster.return_value = FAKE_DISASTER
        mock_evacuation_db.save_plan.return_value    = "plan-001"
        mock_evacuation_db.generate_plan_ref.return_value = "EVA-0001"
        evacuation_service.external.fetch_traffic_data.return_value = {"segments": []}
        evacuation_service.external.get_directions.return_value = {"routes": [FAKE_ROUTE]}

        result = await evacuation_service.plan_evacuation("dis-001")
        mock_evacuation_db.save_plan.assert_called_once()
        assert result["plan_id"] == "plan-001"
        assert result["plan_ref"] == "EVA-0001"

    @pytest.mark.asyncio
    async def test_auto_approve_sets_status(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_disaster.return_value = FAKE_DISASTER
        mock_evacuation_db.save_plan.return_value    = "plan-001"
        mock_evacuation_db.generate_plan_ref.return_value = "EVA-0001"
        evacuation_service.external.fetch_traffic_data.return_value = {"segments": []}
        evacuation_service.external.get_directions.return_value = {"routes": [FAKE_ROUTE]}

        result = await evacuation_service.plan_evacuation("dis-001", auto_approve=True)
        assert result["plan_status"] == "APPROVED"
        assert result["auto_approved"] is True

    @pytest.mark.asyncio
    async def test_fallback_route_used_on_tomtom_failure(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_disaster.return_value = FAKE_DISASTER
        mock_evacuation_db.save_plan.return_value    = "plan-001"
        mock_evacuation_db.generate_plan_ref.return_value = "EVA-0001"
        evacuation_service.external.fetch_traffic_data.return_value = {"segments": []}
        evacuation_service.external.get_directions.side_effect = Exception("TomTom down")

        # Should not raise — fallback routes used
        result = await evacuation_service.plan_evacuation("dis-001")
        assert result["plan_id"] == "plan-001"


# ---------------------------------------------------------------------------
# Phase 2 — approve_evacuation
# ---------------------------------------------------------------------------

class TestApproveEvacuation:

    @pytest.fixture
    def pending_plan(self):
        return {"id": "plan-001", "plan_ref": "EVA-0001",
                "plan_status": "PENDING", "disaster_id": "dis-001"}

    @pytest.fixture
    def approved_plan(self):
        return {"id": "plan-001", "plan_ref": "EVA-0001",
                "plan_status": "APPROVED", "disaster_id": "dis-001"}

    @pytest.mark.asyncio
    async def test_approves_pending_plan(self, evacuation_service, mock_evacuation_db, pending_plan):
        mock_evacuation_db.get_plan.return_value = pending_plan
        result = await evacuation_service.approve_evacuation("plan-001", "Officer Murphy")
        mock_evacuation_db.update_plan.assert_called_once()
        assert result["plan_status"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_raises_400_if_already_approved(self, evacuation_service, mock_evacuation_db, approved_plan):
        mock_evacuation_db.get_plan.return_value = approved_plan
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await evacuation_service.approve_evacuation("plan-001", "Officer B")
        assert exc.value.status_code == 400
        assert "already approved" in exc.value.detail

    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_plan(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_plan.return_value = None
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await evacuation_service.approve_evacuation("no-plan", "Officer")
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Phase 3 — activate_evacuation
# ---------------------------------------------------------------------------

class TestActivateEvacuation:

    @pytest.fixture
    def approved_plan(self):
        return {
            "id": "plan-001", "plan_ref": "EVA-0001",
            "plan_status": "APPROVED", "disaster_id": "dis-001",
            "impact_zones": [{"zone_id": "zone_city_centre", "name": "City Centre",
                               "population": 25000, "vulnerable_count": 3000}],
            "best_routes_per_zone": {},
            "shelters_with_capacity": [FAKE_SHELTER],
            "transport_plan": {"total_buses": 500, "total_ambulances": 1500},
            "allocations": {"buses_allocated": 500, "ambulances_allocated": 1500},
        }

    @pytest.mark.asyncio
    async def test_gets_users_for_zones(self, evacuation_service, mock_evacuation_db, approved_plan):
        mock_evacuation_db.get_plan.return_value = approved_plan
        mock_evacuation_db.get_users_in_zones.return_value = []
        await evacuation_service.activate_evacuation("plan-001")
        mock_evacuation_db.get_users_in_zones.assert_called_once()

    @pytest.mark.asyncio
    async def test_pushes_map_overlay(self, evacuation_service, mock_evacuation_db, approved_plan):
        mock_evacuation_db.get_plan.return_value = approved_plan
        mock_evacuation_db.get_users_in_zones.return_value = []
        await evacuation_service.activate_evacuation("plan-001")
        evacuation_service.mapping.highlight_alternative_routes.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_plan_to_active(self, evacuation_service, mock_evacuation_db, approved_plan):
        mock_evacuation_db.get_plan.return_value = approved_plan
        mock_evacuation_db.get_users_in_zones.return_value = []
        result = await evacuation_service.activate_evacuation("plan-001")
        mock_evacuation_db.update_plan.assert_called()
        assert result["plan_status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_raises_400_if_not_approved(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_plan.return_value = {
            "id": "plan-001", "plan_ref": "EVA-0001",
            "plan_status": "PENDING", "impact_zones": [],
        }
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await evacuation_service.activate_evacuation("plan-001")
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Phase 3 — broadcast_alerts
# ---------------------------------------------------------------------------

class TestBroadcastAlerts:

    USERS = [
        {"id": "u1", "phone_number": "+353871111111",
         "zone_id": "zone_city_centre", "zone_name": "City Centre"},
        {"id": "u2", "phone_number": "+353872222222",
         "zone_id": "zone_city_centre", "zone_name": "City Centre"},
    ]

    @pytest.mark.asyncio
    async def test_publishes_via_rabbitmq_when_connected(
        self, evacuation_service, mock_publisher
    ):
        mock_publisher.is_connected = True
        mock_publisher._publish = AsyncMock(return_value=True)

        sent = await evacuation_service.broadcast_alerts(
            self.USERS, "dis-1", "plan-1", {}, [FAKE_SHELTER])

        mock_publisher._publish.assert_called_once()
        call_kw = mock_publisher._publish.call_args.kwargs
        assert call_kw["routing_key"] == "evacuation.triggered"
        assert sent == 2

    @pytest.mark.asyncio
    async def test_deduplicates_users(self, evacuation_service, mock_publisher):
        mock_publisher.is_connected = True
        mock_publisher._publish = AsyncMock(return_value=True)
        dupes = self.USERS + [self.USERS[0]]   # u1 appears twice

        sent = await evacuation_service.broadcast_alerts(
            dupes, "dis-1", "plan-1", {}, [FAKE_SHELTER])

        assert sent == 2   # only 2 unique users

    @pytest.mark.asyncio
    async def test_empty_list_returns_0(self, evacuation_service, mock_publisher):
        mock_publisher.is_connected = False
        sent = await evacuation_service.broadcast_alerts(
            [], "dis-1", "plan-1", {}, [])
        assert sent == 0


# ---------------------------------------------------------------------------
# Phase 3 — display_evacuation_on_map
# ---------------------------------------------------------------------------

class TestDisplayEvacuationOnMap:

    @pytest.mark.asyncio
    async def test_calls_mapping_service(self, evacuation_service, mock_mapping_service):
        result = await evacuation_service.display_evacuation_on_map(
            "plan-1", [FAKE_ZONE],
            {"zone_city_centre": [FAKE_ROUTE]}, [FAKE_SHELTER],
        )
        mock_mapping_service.highlight_alternative_routes.assert_called_once()
        call_kw = mock_mapping_service.highlight_alternative_routes.call_args.kwargs
        assert call_kw["region_id"] == "region-dublin-city"
        assert result is True

    @pytest.mark.asyncio
    async def test_mapping_failure_is_nonfatal(self, evacuation_service, mock_mapping_service):
        mock_mapping_service.highlight_alternative_routes.side_effect = Exception("Socket error")
        result = await evacuation_service.display_evacuation_on_map("plan-1", [], {}, [])
        assert result is True


# ---------------------------------------------------------------------------
# Phase 4 — update_progress
# ---------------------------------------------------------------------------

class TestUpdateProgress:

    @pytest.fixture
    def active_plan(self):
        return {
            "id": "plan-001", "plan_ref": "EVA-0001",
            "plan_status": "ACTIVE", "disaster_id": "dis-001",
            "impact_zones": [{"zone_id": "z1", "population": 100},
                             {"zone_id": "z2", "population": 200}],
            "completion_metrics": {
                "z1": {"percentage": 50, "evacuated":  50, "remaining":  50},
                "z2": {"percentage": 60, "evacuated": 120, "remaining":  80},
            },
        }

    @pytest.mark.asyncio
    async def test_marks_completed_when_all_100(
        self, evacuation_service, mock_evacuation_db, active_plan
    ):
        mock_evacuation_db.get_plan.return_value = active_plan
        result = await evacuation_service.update_progress("plan-001", {
            "z1": {"percentage": 100, "evacuated": 100, "remaining": 0},
            "z2": {"percentage": 100, "evacuated": 200, "remaining": 0},
        })
        assert result["plan_status"]      == "COMPLETED"
        assert result["overall_completion"] == 100.0

    @pytest.mark.asyncio
    async def test_stays_active_below_100(
        self, evacuation_service, mock_evacuation_db, active_plan
    ):
        mock_evacuation_db.get_plan.return_value = active_plan
        result = await evacuation_service.update_progress("plan-001", {
            "z1": {"percentage": 60, "evacuated": 60, "remaining": 40},
        })
        assert result["plan_status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_raises_400_when_plan_not_active(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_plan.return_value = {
            "id": "plan-001", "plan_ref": "EVA-0001", "plan_status": "PENDING"}
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await evacuation_service.update_progress("plan-001", {})
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Phase 4 — handle_route_blockage
# ---------------------------------------------------------------------------

class TestHandleRouteBlockage:

    @pytest.fixture
    def active_plan(self):
        return {
            "id": "plan-001", "plan_ref": "EVA-0001",
            "plan_status": "ACTIVE", "disaster_id": "dis-001",
            "impact_zones": [{"zone_id": "zone_city_centre", "name": "City Centre",
                               "population": 25000, "vulnerable_count": 3000,
                               "lat": 53.3498, "lon": -6.2603}],
            "best_routes_per_zone": {},
            "shelters_with_capacity": [FAKE_SHELTER],
            "blocked_roads": [],
            "traffic_snapshot": {},
        }

    @pytest.mark.asyncio
    async def test_recomputes_routes_via_external(
        self, evacuation_service, mock_evacuation_db, active_plan,
        mock_external_integration_service,
    ):
        mock_evacuation_db.get_plan.return_value = active_plan
        mock_external_integration_service.get_directions.return_value = {"routes": [FAKE_ROUTE]}

        await evacuation_service.handle_route_blockage(
            "plan-001", ["O'Connell Street"], ["zone_city_centre"])

        mock_external_integration_service.get_directions.assert_called()

    @pytest.mark.asyncio
    async def test_raises_400_if_plan_not_active(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_plan.return_value = {
            "id": "plan-001", "plan_ref": "EVA-0001", "plan_status": "PENDING"}
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await evacuation_service.handle_route_blockage(
                "plan-001", ["Road A"], ["zone_a"])

    @pytest.mark.asyncio
    async def test_raises_400_if_zone_not_in_plan(
        self, evacuation_service, mock_evacuation_db, active_plan
    ):
        mock_evacuation_db.get_plan.return_value = active_plan
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await evacuation_service.handle_route_blockage(
                "plan-001", ["Road A"], ["zone_not_in_plan"])
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_updates_map_after_recompute(
        self, evacuation_service, mock_evacuation_db, active_plan,
        mock_mapping_service, mock_external_integration_service,
    ):
        mock_evacuation_db.get_plan.return_value = active_plan
        mock_external_integration_service.get_directions.return_value = {"routes": [FAKE_ROUTE]}

        await evacuation_service.handle_route_blockage(
            "plan-001", ["O'Connell Street"], ["zone_city_centre"])

        mock_mapping_service.highlight_alternative_routes.assert_called()


# ---------------------------------------------------------------------------
# Phase 4 — handle_disaster_escalation
# ---------------------------------------------------------------------------

class TestHandleDisasterEscalation:

    @pytest.fixture
    def active_plan(self):
        return {
            "id": "plan-001", "plan_ref": "EVA-0001",
            "plan_status": "ACTIVE", "disaster_id": "dis-001",
            "impact_zones": [{"zone_id": "zone_city_centre", "name": "City Centre",
                               "population": 25000, "vulnerable_count": 3000}],
            "best_routes_per_zone": {}, "shelters_with_capacity": [FAKE_SHELTER],
            "blocked_roads": [], "traffic_snapshot": {},
            "completion_metrics": {}, "allocations": {}, "notes": "",
        }

    @pytest.mark.asyncio
    async def test_adds_new_zones(
        self, evacuation_service, mock_evacuation_db, active_plan,
        mock_external_integration_service,
    ):
        mock_evacuation_db.get_plan.return_value = active_plan
        mock_evacuation_db.get_users_in_zones.return_value = []
        mock_external_integration_service.get_directions.return_value = {"routes": [FAKE_ROUTE]}

        result = await evacuation_service.handle_disaster_escalation(
            "plan-001", ["zone_northside"], "Fire spread north")

        assert result["new_zones_added"] == 1
        mock_evacuation_db.update_plan.assert_called()

    @pytest.mark.asyncio
    async def test_skips_existing_zones(
        self, evacuation_service, mock_evacuation_db, active_plan
    ):
        mock_evacuation_db.get_plan.return_value = active_plan
        result = await evacuation_service.handle_disaster_escalation(
            "plan-001", ["zone_city_centre"], "already in plan")
        assert "already included" in result["message"]

    @pytest.mark.asyncio
    async def test_raises_400_if_plan_not_active(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_plan.return_value = {
            "id": "plan-001", "plan_ref": "EVA-0001", "plan_status": "APPROVED"}
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await evacuation_service.handle_disaster_escalation(
                "plan-001", ["zone_northside"], "reason")

    @pytest.mark.asyncio
    async def test_raises_400_for_invalid_zone_ids(
        self, evacuation_service, mock_evacuation_db, active_plan
    ):
        mock_evacuation_db.get_plan.return_value = active_plan
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await evacuation_service.handle_disaster_escalation(
                "plan-001", ["zone_does_not_exist"], "bad zone")
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# send_route_updates
# ---------------------------------------------------------------------------

class TestSendRouteUpdates:

    @pytest.mark.asyncio
    async def test_publishes_route_updated_via_publisher(
        self, evacuation_service, mock_publisher
    ):
        mock_publisher.is_connected = True
        users = [{"id": "u1", "phone_number": "+353871111111",
                  "zone_id": "z1", "zone_name": "Zone 1"}]

        count = await evacuation_service.send_route_updates(users, {"z1": []}, "dis-1")

        mock_publisher.publish_route_updated.assert_called_once()
        assert count == 1

    @pytest.mark.asyncio
    async def test_empty_users_returns_0(self, evacuation_service, mock_publisher):
        mock_publisher.is_connected = True
        count = await evacuation_service.send_route_updates([], {}, "dis-1")
        assert count == 0


# ---------------------------------------------------------------------------
# fetch_traffic_data
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# _compute_zone_routes (routing detail)
# ---------------------------------------------------------------------------

class TestComputeZoneRoutes:

    @pytest.mark.asyncio
    async def test_calls_get_directions_per_shelter(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.get_directions.return_value = {"routes": [FAKE_ROUTE]}
        shelters = [FAKE_SHELTER]

        routes = await evacuation_service._compute_zone_routes(
            FAKE_ZONE, shelters, [], {"segments": []})

        mock_external_integration_service.get_directions.assert_called_once_with(
            origin={"lat": FAKE_ZONE["lat"], "lng": FAKE_ZONE["lon"]},
            destination={"lat": FAKE_SHELTER["lat"], "lng": FAKE_SHELTER["lon"]},
            avoid=[],
            alternatives=True,
        )
        assert len(routes) >= 1

    @pytest.mark.asyncio
    async def test_passes_blocked_roads_as_avoid(
        self, evacuation_service, mock_external_integration_service
    ):
        blocked = [{"segment_id": "s1", "road_name": "O'Connell St",
                    "start_lat": 53.347, "start_lng": -6.260,
                    "end_lat": 53.349, "end_lng": -6.258}]
        mock_external_integration_service.get_directions.return_value = {"routes": [FAKE_ROUTE]}

        await evacuation_service._compute_zone_routes(
            FAKE_ZONE, [FAKE_SHELTER], blocked, {})

        call_kw = mock_external_integration_service.get_directions.call_args.kwargs
        assert call_kw["avoid"] == blocked

    @pytest.mark.asyncio
    async def test_skips_full_shelters(
        self, evacuation_service, mock_external_integration_service
    ):
        full_shelter = {**FAKE_SHELTER, "available": 0}
        await evacuation_service._compute_zone_routes(
            FAKE_ZONE, [full_shelter], [], {})
        mock_external_integration_service.get_directions.assert_not_called()

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
    async def test_enriches_distance_and_time_from_tomtom_fields(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.get_directions.return_value = {"routes": [FAKE_ROUTE]}
        routes = await evacuation_service._compute_zone_routes(
            FAKE_ZONE, [FAKE_SHELTER], [], {})
        assert routes[0]["distance_km"]        == round(8000 / 1000, 2)
        assert routes[0]["estimated_time_min"] == round(600 / 60, 1)

    @pytest.mark.asyncio
    async def test_concurrent_zones(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.get_directions.return_value = {"routes": [FAKE_ROUTE]}
        zones = [
            {**FAKE_ZONE, "zone_id": "z_a", "lat": 53.34},
            {**FAKE_ZONE, "zone_id": "z_b", "lat": 53.35},
        ]
        result = await evacuation_service._compute_all_zone_routes(
            zones, [FAKE_SHELTER], [], {})

        assert "z_a" in result and "z_b" in result
        assert mock_external_integration_service.get_directions.call_count == 2
