"""
tests/unit/test_reroute_service.py

Unit tests — ReRoute Service orchestration logic.
Covers sequence diagram Steps 4–9, feasibility checks,
degraded mode, priority routing, and operator overrides.
All dependencies (DB, External Integration, Mapping, Notification) are mocked.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, call

from app.services.reroute_service import RerouteService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def reroute_service(
    mock_db_repository,
    mock_external_integration_service,
    mock_mapping_service,
    mock_publisher,
):
    return RerouteService(
        db=mock_db_repository,
        external=mock_external_integration_service,
        mapping=mock_mapping_service,
        publisher=mock_publisher,
    )


# ---------------------------------------------------------------------------
# Step 2 — getBlockedRoads
# ---------------------------------------------------------------------------

class TestGetBlockedRoads:

    @pytest.mark.asyncio
    async def test_fetches_blocked_roads_for_disaster(
        self, reroute_service, mock_db_repository, disaster_id, sample_blocked_roads
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        roads = await reroute_service.get_blocked_roads(disaster_id)
        mock_db_repository.get_blocked_roads.assert_called_once_with(disaster_id)
        assert roads == sample_blocked_roads

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_roads_blocked(
        self, reroute_service, mock_db_repository, disaster_id
    ):
        mock_db_repository.get_blocked_roads.return_value = []
        roads = await reroute_service.get_blocked_roads(disaster_id)
        assert roads == []


# ---------------------------------------------------------------------------
# Step 3 — fetchTrafficData + degraded mode
# ---------------------------------------------------------------------------

class TestFetchTrafficData:

    @pytest.mark.asyncio
    async def test_calls_external_integration_service_for_traffic(
        self, reroute_service, mock_external_integration_service,
        region_id, sample_tomtom_traffic_response
    ):
        mock_external_integration_service.fetch_traffic_data.return_value = (
            sample_tomtom_traffic_response
        )
        result = await reroute_service.fetch_traffic_data(region_id)
        mock_external_integration_service.fetch_traffic_data.assert_called_once_with(region_id)
        assert result is not None

    @pytest.mark.asyncio
    async def test_activates_degraded_mode_on_tomtom_error(
        self, reroute_service, mock_external_integration_service, region_id
    ):
        """Step 3 alt: TomTom returns Error → switchToDegradedMode."""
        mock_external_integration_service.fetch_traffic_data.side_effect = Exception(
            "TomTom timeout"
        )
        result = await reroute_service.fetch_traffic_data(region_id)
        # Should not raise — must return degraded/cached result
        assert result is not None
        assert result.get("mode") == "degraded"

    @pytest.mark.asyncio
    async def test_degraded_mode_uses_cached_graph_and_default_speeds(
        self, reroute_service, mock_external_integration_service, region_id
    ):
        mock_external_integration_service.fetch_traffic_data.side_effect = Exception("timeout")
        result = await reroute_service.fetch_traffic_data(region_id)
        assert "cached_graph" in result or result.get("mode") == "degraded"


# ---------------------------------------------------------------------------
# Step 4 — findImpactedVehicles
# ---------------------------------------------------------------------------

class TestFindImpactedVehicles:

    @pytest.mark.asyncio
    async def test_returns_vehicles_on_blocked_segments(
        self, reroute_service, sample_blocked_roads, sample_impacted_vehicles
    ):
        with patch.object(
            reroute_service, "_query_vehicles_on_segments",
            new=AsyncMock(return_value=sample_impacted_vehicles)
        ):
            vehicles = await reroute_service.find_impacted_vehicles(
                region_id="region-dublin", blocked_roads=sample_blocked_roads
            )
        assert len(vehicles) == len(sample_impacted_vehicles)

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_vehicles_in_region(
        self, reroute_service, sample_blocked_roads
    ):
        with patch.object(
            reroute_service, "_query_vehicles_on_segments",
            new=AsyncMock(return_value=[])
        ):
            vehicles = await reroute_service.find_impacted_vehicles(
                region_id="region-empty", blocked_roads=sample_blocked_roads
            )
        assert vehicles == []


# ---------------------------------------------------------------------------
# Step 5 — getDirections (par + loop)
# ---------------------------------------------------------------------------

class TestGetDirections:

    @pytest.mark.asyncio
    async def test_calls_external_for_each_destination(
        self, reroute_service, mock_external_integration_service,
        sample_blocked_roads, sample_tomtom_routing_response
    ):
        destinations = [
            {"lat": 53.40, "lng": -6.25},
            {"lat": 53.35, "lng": -6.30},
        ]
        mock_external_integration_service.get_directions.return_value = (
            sample_tomtom_routing_response
        )
        routes = await reroute_service.calculate_alternative_routes(
            blocked_roads=sample_blocked_roads,
            destinations=destinations,
        )
        assert mock_external_integration_service.get_directions.call_count == len(destinations)

    @pytest.mark.asyncio
    async def test_passes_blocked_roads_as_avoidance_to_tomtom(
        self, reroute_service, mock_external_integration_service,
        sample_blocked_roads, sample_tomtom_routing_response
    ):
        mock_external_integration_service.get_directions.return_value = (
            sample_tomtom_routing_response
        )
        await reroute_service.calculate_alternative_routes(
            blocked_roads=sample_blocked_roads,
            destinations=[{"lat": 53.40, "lng": -6.25}],
        )
        call_kwargs = mock_external_integration_service.get_directions.call_args[1]
        assert "avoid" in call_kwargs
        assert len(call_kwargs["avoid"]) == len(sample_blocked_roads)

    @pytest.mark.asyncio
    async def test_requests_multiple_alternatives_from_tomtom(
        self, reroute_service, mock_external_integration_service,
        sample_blocked_roads, sample_tomtom_routing_response
    ):
        mock_external_integration_service.get_directions.return_value = (
            sample_tomtom_routing_response
        )
        await reroute_service.calculate_alternative_routes(
            blocked_roads=sample_blocked_roads,
            destinations=[{"lat": 53.40, "lng": -6.25}],
        )
        call_kwargs = mock_external_integration_service.get_directions.call_args[1]
        assert call_kwargs.get("alternatives") is True


# ---------------------------------------------------------------------------
# Step 7 — Feasibility check + temporary controls
# ---------------------------------------------------------------------------

class TestFeasibilityCheck:

    @pytest.mark.asyncio
    async def test_proceeds_when_feasible_routes_exist(
        self, reroute_service, sample_alternative_routes
    ):
        result = await reroute_service.evaluate_feasibility(sample_alternative_routes)
        assert result["feasible"] is True

    @pytest.mark.asyncio
    async def test_activates_temporary_controls_when_no_feasible_routes(
        self, reroute_service, mock_external_integration_service,
        sample_blocked_roads, sample_tomtom_routing_response
    ):
        mock_external_integration_service.get_directions.return_value = (
            sample_tomtom_routing_response
        )
        result = await reroute_service.handle_no_feasible_routes(
            blocked_roads=sample_blocked_roads,
            destinations=[{"lat": 53.40, "lng": -6.25}],
        )
        assert result["controls_activated"] is True
        # After activating controls, must retry TomTom routing
        assert mock_external_integration_service.get_directions.call_count >= 1

    @pytest.mark.asyncio
    async def test_temporary_controls_include_contraflow_option(
        self, reroute_service, mock_external_integration_service,
        sample_blocked_roads, sample_tomtom_routing_response
    ):
        mock_external_integration_service.get_directions.return_value = (
            sample_tomtom_routing_response
        )
        result = await reroute_service.handle_no_feasible_routes(
            blocked_roads=sample_blocked_roads,
            destinations=[{"lat": 53.40, "lng": -6.25}],
        )
        assert "contraflow" in result["activated_controls"]


# ---------------------------------------------------------------------------
# Steps 8–9 — Persist + update road status
# ---------------------------------------------------------------------------

class TestPersistence:

    @pytest.mark.asyncio
    async def test_saves_reroute_plan_to_database(
        self, reroute_service, mock_db_repository, disaster_id,
        sample_blocked_roads, sample_alternative_routes
    ):
        await reroute_service.save_reroute_plan(
            disaster_id=disaster_id,
            blocked_roads=sample_blocked_roads,
            chosen_routes=sample_alternative_routes,
        )
        mock_db_repository.save_reroute_plan.assert_called_once()
        args = mock_db_repository.save_reroute_plan.call_args[0]
        assert disaster_id in args or disaster_id == mock_db_repository.save_reroute_plan.call_args[1].get("disaster_id")

    @pytest.mark.asyncio
    async def test_updates_blocked_road_status_to_closed(
        self, reroute_service, mock_db_repository, sample_blocked_roads
    ):
        await reroute_service.update_road_status(sample_blocked_roads, status="closed")
        mock_db_repository.update_road_status.assert_called_once_with(
            sample_blocked_roads, "closed"
        )

    @pytest.mark.asyncio
    async def test_logs_reroute_event_to_database(
        self, reroute_service, mock_db_repository, disaster_id
    ):
        await reroute_service.log_event(
            disaster_id=disaster_id,
            event_type="traffic_rerouted",
            data={"routes": 3},
        )
        mock_db_repository.log_event.assert_called_once()


# ---------------------------------------------------------------------------
# Step 13c — Operator Override
# ---------------------------------------------------------------------------

class TestOperatorOverride:

    @pytest.mark.asyncio
    async def test_applies_override_to_database(
        self, reroute_service, mock_db_repository
    ):
        override = {
            "type": "close_lane",
            "segment_id": "seg-m50-junction-6-7",
            "operator_id": "op-001",
        }
        await reroute_service.receive_override(override)
        mock_db_repository.apply_override.assert_called_once()

    @pytest.mark.asyncio
    async def test_recomputes_routes_after_override(
        self, reroute_service, mock_external_integration_service,
        sample_blocked_roads, sample_tomtom_routing_response
    ):
        mock_external_integration_service.recompute_with_overrides.return_value = (
            sample_tomtom_routing_response
        )
        override = {"type": "pin_detour", "segment_id": "seg-X", "operator_id": "op-002"}
        await reroute_service.receive_override(override)
        mock_external_integration_service.recompute_with_overrides.assert_called_once()

    @pytest.mark.asyncio
    async def test_pushes_updated_routes_to_map_after_override(
        self, reroute_service, mock_mapping_service,
        mock_external_integration_service, sample_tomtom_routing_response
    ):
        mock_external_integration_service.recompute_with_overrides.return_value = (
            sample_tomtom_routing_response
        )
        override = {"type": "corridor_priority", "priority": "emergency", "operator_id": "op-003"}
        await reroute_service.receive_override(override)
        mock_mapping_service.highlight_alternative_routes.assert_called_once()

    @pytest.mark.asyncio
    async def test_publishes_event_after_override(
        self, reroute_service, mock_publisher,
        mock_external_integration_service,
    ):
        mock_external_integration_service.recompute_with_overrides.return_value = {"routes": []}
        override = {"type": "open_lane", "segment_id": "seg-Y", "operator_id": "op-004"}
        await reroute_service.receive_override(override)
        mock_publisher.publish_route_updated.assert_called_once()


# ---------------------------------------------------------------------------
# Priority Routing — Step 13b (emergency > PT > general)
# ---------------------------------------------------------------------------

class TestPriorityRouting:

    @pytest.mark.asyncio
    async def test_emergency_vehicles_assigned_first(
        self, reroute_service, three_routes_equal_capacity
    ):
        vehicles = [
            {"user_id": "ambulance-1", "type": "emergency", "destination": {"lat": 53.4, "lng": -6.2}},
            {"user_id": "bus-1", "type": "public_transport", "destination": {"lat": 53.4, "lng": -6.2}},
            {"user_id": "car-1", "type": "general", "destination": {"lat": 53.4, "lng": -6.2}},
        ]
        plan = await reroute_service.reprioritize_flows(vehicles, three_routes_equal_capacity)
        # Emergency vehicle should get the lowest-score (fastest) route
        emergency_route = plan.route_assignments["ambulance-1"]
        general_route = plan.route_assignments["car-1"]
        # Emergency route should have same or lower travel time than general
        emergency_time = next(
            r["travel_time_seconds"] for r in three_routes_equal_capacity
            if r["route_id"] == emergency_route
        )
        general_time = next(
            r["travel_time_seconds"] for r in three_routes_equal_capacity
            if r["route_id"] == general_route
        )
        assert emergency_time <= general_time

    @pytest.mark.asyncio
    async def test_public_transport_prioritized_over_general(
        self, reroute_service, three_routes_equal_capacity
    ):
        vehicles = [
            {"user_id": "bus-2", "type": "public_transport", "destination": {"lat": 53.4, "lng": -6.2}},
            {"user_id": "car-2", "type": "general", "destination": {"lat": 53.4, "lng": -6.2}},
        ]
        plan = await reroute_service.reprioritize_flows(vehicles, three_routes_equal_capacity)
        pt_route = plan.route_assignments["bus-2"]
        gen_route = plan.route_assignments["car-2"]
        pt_time = next(r["travel_time_seconds"] for r in three_routes_equal_capacity if r["route_id"] == pt_route)
        gen_time = next(r["travel_time_seconds"] for r in three_routes_equal_capacity if r["route_id"] == gen_route)
        assert pt_time <= gen_time


# ---------------------------------------------------------------------------
# Step 14 — Clearance & Restoration
# ---------------------------------------------------------------------------

class TestClearanceRestoration:

    @pytest.mark.asyncio
    async def test_updates_road_status_to_open_on_clearance(
        self, reroute_service, mock_db_repository, sample_blocked_roads
    ):
        await reroute_service.restore_normal_flow(
            disaster_id="disaster-001",
            cleared_segments=sample_blocked_roads,
        )
        mock_db_repository.update_road_status.assert_called_with(sample_blocked_roads, "open")

    @pytest.mark.asyncio
    async def test_clears_active_detours_on_restoration(
        self, reroute_service, mock_mapping_service, sample_blocked_roads
    ):
        await reroute_service.restore_normal_flow(
            disaster_id="disaster-001",
            cleared_segments=sample_blocked_roads,
        )
        mock_mapping_service.clear_detours.assert_called_once()

    @pytest.mark.asyncio
    async def test_sends_all_clear_notification_on_restoration(
        self, reroute_service, mock_notification_service,
        mock_db_repository, sample_blocked_roads
    ):
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": f"u-{i}"} for i in range(5)
        ]
        await reroute_service.restore_normal_flow(
            disaster_id="disaster-001",
            cleared_segments=sample_blocked_roads,
        )
        mock_notification_service.send_all_clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_restored_event_on_clearance(
        self, reroute_service, mock_db_repository, sample_blocked_roads
    ):
        await reroute_service.restore_normal_flow(
            disaster_id="disaster-001",
            cleared_segments=sample_blocked_roads,
        )
        log_calls = mock_db_repository.log_event.call_args_list
        event_types = [c[0][1] if c[0] else c[1].get("event_type") for c in log_calls]
        assert "restored" in event_types or any("restored" in str(c) for c in log_calls)


# ---------------------------------------------------------------------------
# Helpers for fixtures imported from conftest
# ---------------------------------------------------------------------------

@pytest.fixture
def three_routes_equal_capacity():
    return [
        {
            "route_id": f"route-{label}",
            "travel_time_seconds": base_time,
            "length_meters": 12000 + i * 2000,
            "segments": [f"seg-{label}-A", f"seg-{label}-B"],
            "segment_capacities": {f"seg-{label}-A": 200, f"seg-{label}-B": 200},
            "current_load": {f"seg-{label}-A": 0, f"seg-{label}-B": 0},
        }
        for i, (label, base_time) in enumerate(
            [("primary", 600), ("secondary", 800), ("tertiary", 1000)]
        )
    ]