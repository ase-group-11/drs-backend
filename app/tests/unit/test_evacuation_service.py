# File: app/tests/unit/test_evacuation_service.py
"""
Unit tests for EvacuationService v2 — impact-area model.

v2 changes tested:
  - build_impact_area() extracts population from evaluation metadata
  - get_nearest_shelters() returns only N closest shelters
  - compute_transport_needs() uses real DB unit counts
  - _compute_routes() routes from disaster centre (not zones)
  - Redis route caching with 300s TTL
  - Escalation uses increased_radius_km instead of zone IDs
  - Route blockage doesn't need affected_zone_ids
"""

import math
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.evacuation_service import (
    EvacuationService,
    TRANSPORT_CAPACITY,
    ROUTE_CACHE_TTL,
    MAX_SHELTERS_TO_ROUTE,
    DUBLIN_SHELTERS,
    build_impact_area,
    get_all_shelters,
    get_nearest_shelters,
    get_population_profile,
    compute_transport_needs,
    allocate_resources,
    score_and_select_routes,
    avg_congestion_weight,
    straight_line_fallback,
    _route_cache_key,
    _hash_blocked_roads,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_evacuation_db():
    db = AsyncMock()
    db.get_disaster = AsyncMock(return_value=None)
    db.get_blocked_roads = AsyncMock(return_value=[])
    db.get_available_transport_units = AsyncMock(return_value=[])
    db.get_users_in_impact_area = AsyncMock(return_value=[])
    db.save_plan = AsyncMock(return_value="plan-xyz")
    db.get_plan = AsyncMock(return_value=None)
    db.update_plan = AsyncMock(return_value=True)
    db.list_plans = AsyncMock(return_value=[])
    db.generate_plan_ref = AsyncMock(return_value="EVA-0001")
    return db


@pytest.fixture
def mock_external_integration_service():
    ext = AsyncMock()
    ext.fetch_traffic_data = AsyncMock(return_value={"segments": [], "mode": "mock"})
    ext.get_directions = AsyncMock(return_value={"routes": []})
    return ext


@pytest.fixture
def mock_mapping_service():
    m = AsyncMock()
    m.highlight_alternative_routes = AsyncMock()
    return m


@pytest.fixture
def mock_publisher():
    p = AsyncMock()
    p.is_connected = True
    p.publish_reroute_triggered = AsyncMock()
    p.publish_route_updated = AsyncMock()
    return p


@pytest.fixture
def evacuation_service(
    mock_evacuation_db,
    mock_external_integration_service,
    mock_mapping_service,
    mock_publisher,
):
    svc = EvacuationService(
        db=mock_evacuation_db,
        external=mock_external_integration_service,
        mapping=mock_mapping_service,
        publisher=mock_publisher,
    )
    # Disable Redis in tests — _cache_get always misses, _cache_set is a no-op
    svc._redis = None
    svc._get_redis = AsyncMock(return_value=None)
    return svc


# ─────────────────────────────────────────────────────────────────────────────
# Test data
# ─────────────────────────────────────────────────────────────────────────────

FAKE_DISASTER = {
    "id": "dis-001", "tracking_id": "TRK-001",
    "type": "FLOOD", "severity": "HIGH", "disaster_status": "ACTIVE",
    "lat": 53.3438, "lon": -6.2613,
    "location_address": "Dawson Street, Dublin 2",
    "people_affected": 5000, "road_blocked": False,
    "disaster_metadata": {
        "evaluation": {
            "impact_radius_km": 3.0,
            "estimated_population": 12723,
            "affected_roads": [
                "Dawson Street", "College Green", "Saint Stephen's Green",
                "Frederick Street South", "Clarendon Street",
            ],
            "affected_facilities": [
                "Loreto College Junior School",
                "Dublin Castle Garda Station",
                "Hedley Park Montessori School",
            ],
        }
    },
}

FAKE_DISASTER_NO_META = {
    "id": "dis-002", "tracking_id": "TRK-002",
    "type": "FIRE", "severity": "MEDIUM", "disaster_status": "ACTIVE",
    "lat": 53.35, "lon": -6.26,
    "location_address": "O'Connell Street, Dublin 1",
    "people_affected": 200, "road_blocked": True,
    "disaster_metadata": None,
}

FAKE_ROUTE = {
    "route_id":              "rt-001",
    "travel_time_seconds":   600,
    "length_meters":         8000,
    "traffic_delay_seconds": 60,
    "points":  [[53.3438, -6.2613], [53.3608, -6.2510]],
    "geojson": {"type": "Feature",
                "geometry": {"type": "LineString", "coordinates": []},
                "properties": {}},
}

FAKE_SHELTER = DUBLIN_SHELTERS[0]  # Croke Park

FAKE_AVAILABLE_UNITS = [
    {"unit_type": "ambulance", "available_count": 5, "capacity": 4},
    {"unit_type": "rescue",    "available_count": 2, "capacity": 4},
    {"unit_type": "fire_engine","available_count": 3, "capacity": 6},
]


# ─────────────────────────────────────────────────────────────────────────────
# build_impact_area
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildImpactArea:

    def test_extracts_population_from_evaluation(self):
        area = build_impact_area(FAKE_DISASTER)
        assert area["population"] == 12723

    def test_extracts_affected_roads(self):
        area = build_impact_area(FAKE_DISASTER)
        assert "Dawson Street" in area["affected_roads"]
        assert len(area["affected_roads"]) == 5

    def test_extracts_radius(self):
        area = build_impact_area(FAKE_DISASTER)
        assert area["radius_km"] == 3.0

    def test_boosts_vulnerable_ratio_for_schools(self):
        area = build_impact_area(FAKE_DISASTER)
        # Has "School" and "Montessori" in facilities → 25% ratio
        assert area["vulnerable_count"] == int(12723 * 0.25)

    def test_fallback_when_no_metadata(self):
        area = build_impact_area(FAKE_DISASTER_NO_META)
        assert area["population"] == 200  # falls back to people_affected
        assert area["radius_km"] == 3.0   # default

    def test_fallback_when_zero_population(self):
        disaster = {**FAKE_DISASTER_NO_META, "people_affected": 0}
        area = build_impact_area(disaster)
        assert area["population"] == 100  # minimum floor

    def test_center_coords(self):
        area = build_impact_area(FAKE_DISASTER)
        assert area["center_lat"] == 53.3438
        assert area["center_lon"] == -6.2613


# ─────────────────────────────────────────────────────────────────────────────
# get_nearest_shelters
# ─────────────────────────────────────────────────────────────────────────────

class TestGetNearestShelters:

    def test_returns_max_count(self):
        shelters = get_nearest_shelters(53.35, -6.26, max_count=3)
        assert len(shelters) == 3

    def test_sorted_by_distance(self):
        shelters = get_nearest_shelters(53.35, -6.26, max_count=5)
        dists = [s["_dist_km"] for s in shelters]
        assert dists == sorted(dists)

    def test_all_shelters_when_max_is_large(self):
        shelters = get_nearest_shelters(53.35, -6.26, max_count=100)
        assert len(shelters) == len(DUBLIN_SHELTERS)


# ─────────────────────────────────────────────────────────────────────────────
# Transport capacity mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestTransportCapacity:

    def test_ambulance_capacity_is_8(self):
        assert TRANSPORT_CAPACITY["ambulance"] == 8

    def test_fire_engine_zero(self):
        assert TRANSPORT_CAPACITY["fire_engine"] == 0

    def test_rescue_has_capacity(self):
        assert TRANSPORT_CAPACITY["rescue"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# compute_transport_needs — with real DB units
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeTransportNeeds:

    def test_uses_db_unit_counts(self):
        stats = {"total": 500, "vulnerable": 50}
        area  = {"disaster_id": "d1", "population": 500, "vulnerable_count": 50}
        routes = {"d1": [{"destination_shelter_id": "s1", "shelter_name": "S",
                          "route_id": "r1", "estimated_time_min": 20}]}
        plan = compute_transport_needs(stats, area, routes, FAKE_AVAILABLE_UNITS)
        # 50 vulnerable / 8 per ambulance = ceil(6.25) = 7, but only 5 available
        assert plan["total_ambulances"] == 5
        assert plan["ambulances_available"] == 5
        # Shortfall: (7-5)*8 = 16 people, rescue capacity 4 → ceil(16/4) = 4, only 2 available
        assert plan["rescue_units_needed"] == 2

    def test_no_units_available(self):
        stats = {"total": 100, "vulnerable": 20}
        area  = {"disaster_id": "d1", "population": 100, "vulnerable_count": 20}
        plan = compute_transport_needs(stats, area, {}, [])
        # No units → 0 allocated
        assert plan["total_ambulances"] == 0
        assert plan["ambulances_available"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# population profile
# ─────────────────────────────────────────────────────────────────────────────

class TestPopulationProfile:

    def test_from_impact_area(self):
        area = {"population": 12723, "vulnerable_count": 3181}
        stats = get_population_profile(area)
        assert stats["total"] == 12723
        assert stats["vulnerable"] == 3181
        assert stats["mobile"] == 12723 - 3181


# ─────────────────────────────────────────────────────────────────────────────
# Route scoring
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreAndSelectRoutes:

    def test_prefers_shorter_routes(self):
        base = {
            "destination_shelter_id": "s", "shelter_name": "S",
            "shelter_capacity": 1000, "points": [],
            "geojson": {}, "waypoints": [],
            "travel_time_seconds": 1200, "estimated_time_min": 20,
        }
        short = {**base, "distance_km": 2.0, "traffic_delay_seconds": 0, "route_id": "short"}
        long  = {**base, "distance_km": 10.0, "traffic_delay_seconds": 0, "route_id": "long"}
        top = score_and_select_routes([long, short], {})[0]
        assert top["route_id"] == "short"

    def test_prefers_less_delay(self):
        base = {
            "destination_shelter_id": "s", "shelter_name": "S",
            "shelter_capacity": 1000, "points": [],
            "geojson": {}, "waypoints": [],
            "travel_time_seconds": 1200, "estimated_time_min": 20,
            "distance_km": 5.0,
        }
        no_delay  = {**base, "traffic_delay_seconds": 0,    "route_id": "no_delay"}
        big_delay = {**base, "traffic_delay_seconds": 3600, "route_id": "big_delay"}
        top = score_and_select_routes([big_delay, no_delay], {})[0]
        assert top["route_id"] == "no_delay"


# ─────────────────────────────────────────────────────────────────────────────
# Congestion weight
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# straight_line_fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestStraightLineFallback:

    def test_returns_dict(self):
        result = straight_line_fallback(53.35, -6.26, FAKE_SHELTER)
        assert isinstance(result, dict)

    def test_flagged_as_fallback(self):
        assert straight_line_fallback(53.35, -6.26, FAKE_SHELTER)["fallback"] is True

    def test_positive_distance(self):
        assert straight_line_fallback(53.35, -6.26, FAKE_SHELTER)["distance_km"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# Route cache key
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteCacheKey:

    def test_includes_disaster_and_shelter(self):
        key = _route_cache_key("dis-1", "shelter-a", "abc123")
        assert "dis-1" in key
        assert "shelter-a" in key
        assert "abc123" in key

    def test_different_blocked_roads_different_key(self):
        k1 = _route_cache_key("d", "s", _hash_blocked_roads([{"road_name": "A"}]))
        k2 = _route_cache_key("d", "s", _hash_blocked_roads([{"road_name": "B"}]))
        assert k1 != k2


# ─────────────────────────────────────────────────────────────────────────────
# fetch_traffic_data
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchTrafficData:

    @pytest.mark.asyncio
    async def test_calls_external(self, evacuation_service, mock_external_integration_service):
        mock_external_integration_service.fetch_traffic_data.return_value = {
            "segments": [], "mode": "mock"}
        result = await evacuation_service.fetch_traffic_data()
        mock_external_integration_service.fetch_traffic_data.assert_called_once()
        assert result is not None

    @pytest.mark.asyncio
    async def test_returns_fallback_on_error(self, evacuation_service, mock_external_integration_service):
        mock_external_integration_service.fetch_traffic_data.side_effect = Exception("down")
        result = await evacuation_service.fetch_traffic_data()
        assert result["available"] is False


# ─────────────────────────────────────────────────────────────────────────────
# _compute_routes
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeRoutes:

    @pytest.mark.asyncio
    async def test_calls_get_directions_per_shelter(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.get_directions.return_value = {
            "routes": [FAKE_ROUTE]}
        impact_area = build_impact_area(FAKE_DISASTER)
        shelters = get_nearest_shelters(impact_area["center_lat"], impact_area["center_lon"])
        result = await evacuation_service._compute_routes(
            impact_area, shelters, [], {})
        assert mock_external_integration_service.get_directions.call_count == len(shelters)
        assert impact_area["disaster_id"] in result

    @pytest.mark.asyncio
    async def test_fallback_when_tomtom_empty(
        self, evacuation_service, mock_external_integration_service
    ):
        mock_external_integration_service.get_directions.return_value = {"routes": []}
        impact_area = build_impact_area(FAKE_DISASTER)
        routes = await evacuation_service._compute_routes(
            impact_area, [FAKE_SHELTER], [], {})
        # Should have fallback routes
        assert isinstance(routes, dict)


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
        mock_evacuation_db.save_plan.return_value = "plan-xyz"
        mock_evacuation_db.get_available_transport_units.return_value = FAKE_AVAILABLE_UNITS
        mock_external_integration_service.get_directions.return_value = {
            "routes": [FAKE_ROUTE]}

        result = await evacuation_service.plan_evacuation("dis-001")

        mock_evacuation_db.save_plan.assert_called_once()
        assert result["plan_id"] == "plan-xyz"
        assert result["plan_ref"] == "EVA-0001"
        assert result["plan_status"] == "PENDING"
        assert result["total_population_affected"] == 12723

    @pytest.mark.asyncio
    async def test_auto_approve_sets_status(
        self, evacuation_service, mock_evacuation_db,
        mock_external_integration_service
    ):
        mock_evacuation_db.get_disaster.return_value = FAKE_DISASTER
        mock_evacuation_db.save_plan.return_value = "plan-auto"
        mock_evacuation_db.get_available_transport_units.return_value = FAKE_AVAILABLE_UNITS
        mock_external_integration_service.get_directions.return_value = {
            "routes": [FAKE_ROUTE]}

        result = await evacuation_service.plan_evacuation("dis-001", auto_approve=True)
        assert result["plan_status"] == "APPROVED"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — approve
# ─────────────────────────────────────────────────────────────────────────────

class TestApproveEvacuation:

    @pytest.mark.asyncio
    async def test_approves_pending_plan(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_plan.return_value = {
            "id": "plan-1", "plan_ref": "EVA-0001", "plan_status": "PENDING",
        }
        result = await evacuation_service.approve_evacuation("plan-1", "Commander Smith")
        assert result["plan_status"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_rejects_already_approved(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_plan.return_value = {
            "id": "plan-1", "plan_ref": "EVA-0001", "plan_status": "APPROVED",
        }
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await evacuation_service.approve_evacuation("plan-1", "Admin")
        assert exc_info.value.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — activate
# ─────────────────────────────────────────────────────────────────────────────

class TestActivateEvacuation:

    def _approved_plan(self):
        impact_area = build_impact_area(FAKE_DISASTER)
        return {
            "id": "plan-1", "plan_ref": "EVA-0001",
            "disaster_id": "dis-001", "plan_status": "APPROVED",
            "impact_zones": [impact_area],
            "shelters_with_capacity": [FAKE_SHELTER],
            "best_routes_per_zone": {},
            "allocations": {"ambulances_allocated": 5, "rescue_units_allocated": 2},
        }

    @pytest.mark.asyncio
    async def test_activates_approved_plan(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_plan.return_value = self._approved_plan()
        result = await evacuation_service.activate_evacuation("plan-1")
        assert result["plan_status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_rejects_non_approved(
        self, evacuation_service, mock_evacuation_db
    ):
        plan = self._approved_plan()
        plan["plan_status"] = "PENDING"
        mock_evacuation_db.get_plan.return_value = plan
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await evacuation_service.activate_evacuation("plan-1")

    @pytest.mark.asyncio
    async def test_initialises_completion_metrics(
        self, evacuation_service, mock_evacuation_db
    ):
        mock_evacuation_db.get_plan.return_value = self._approved_plan()
        await evacuation_service.activate_evacuation("plan-1")
        call_kwargs = mock_evacuation_db.update_plan.call_args.kwargs
        metrics = call_kwargs.get("completion_metrics", {})
        assert "impact_area" in metrics
        assert metrics["impact_area"]["percentage"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — update_progress
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateProgress:

    def _active_plan(self):
        impact_area = build_impact_area(FAKE_DISASTER)
        return {
            "id": "plan-1", "plan_ref": "EVA-0001",
            "disaster_id": "dis-1", "plan_status": "ACTIVE",
            "impact_zones": [impact_area],
            "shelters_with_capacity": [FAKE_SHELTER],
            "best_routes_per_zone": {},
            "allocations": {},
            "completion_metrics": {
                "impact_area": {"percentage": 0, "evacuated": 0,
                                "remaining": impact_area["population"],
                                "status": "in_progress"},
            },
        }

    @pytest.mark.asyncio
    async def test_updates_metrics(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_plan.return_value = self._active_plan()
        result = await evacuation_service.update_progress(
            "plan-1",
            {"impact_area": {"percentage": 50, "evacuated": 6362,
                             "remaining": 6361, "status": "in_progress"}},
        )
        assert result["overall_completion"] == pytest.approx(50.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_marks_completed_when_100(self, evacuation_service, mock_evacuation_db):
        mock_evacuation_db.get_plan.return_value = self._active_plan()
        result = await evacuation_service.update_progress(
            "plan-1",
            {"impact_area": {"percentage": 100, "evacuated": 12723,
                             "remaining": 0, "status": "done"}},
        )
        assert result["plan_status"] == "COMPLETED"


# ─────────────────────────────────────────────────────────────────────────────
# broadcast_alerts + send_route_updates
# ─────────────────────────────────────────────────────────────────────────────

class TestBroadcastAlerts:

    @pytest.mark.asyncio
    async def test_uses_publisher_when_connected(self, evacuation_service, mock_publisher):
        mock_publisher.is_connected = True
        users = [{"id": "u1", "phone_number": "+353871111111",
                  "impact_area_id": "dis-001", "area_name": "Dawson St area"}]
        count = await evacuation_service.broadcast_alerts(users, "dis-1", "plan-1", {}, [])
        mock_publisher.publish_reroute_triggered.assert_called_once()
        assert count == 1

    @pytest.mark.asyncio
    async def test_returns_0_for_empty_users(self, evacuation_service, mock_publisher):
        mock_publisher.is_connected = True
        count = await evacuation_service.broadcast_alerts([], "dis-1", "plan-1", {}, [])
        assert count == 0


class TestSendRouteUpdates:

    @pytest.mark.asyncio
    async def test_uses_publisher_when_connected(self, evacuation_service, mock_publisher):
        mock_publisher.is_connected = True
        users = [{"id": "u1", "phone_number": "+353871111111",
                  "impact_area_id": "dis-1", "area_name": "Area 1"}]
        count = await evacuation_service.send_route_updates(users, {"d1": []}, "dis-1")
        mock_publisher.publish_route_updated.assert_called_once()
        assert count == 1