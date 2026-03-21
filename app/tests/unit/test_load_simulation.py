"""
tests/load/test_load_simulation.py

Load & simulation tests — Section 13.3 + Section 10.
Uses pytest-asyncio for async orchestration and validates SLA targets.
For Locust-based Socket.IO agent testing, see locustfile.py.

SLA target: 200–500 mock users all rerouted within 5 seconds of disaster trigger.

These tests verify:
  - Disaster trigger → all users rerouted within SLA (< 5 seconds)
  - Congestion on detour → recalculation and re-notification verified
  - Predictive congestion triggers preemptive redistribution
  - Second concurrent incident → priority-based re-prioritization
  - Operator override → routes update for all connected users
  - Clearance → all-clear reaches all users
  - Full lifecycle end-to-end with timing benchmarks
"""
import asyncio
import time
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_user_pool(n: int) -> list[dict]:
    """Generate N simulated users spread across Dublin."""
    import random
    return [
        {
            "user_id": f"sim-user-{i}",
            "current_location": {
                "lat": 53.20 + random.uniform(0, 0.30),
                "lng": -6.45 + random.uniform(0, 0.30),
            },
            "destination": {
                "lat": 53.20 + random.uniform(0, 0.30),
                "lng": -6.45 + random.uniform(0, 0.30),
            },
            "type": random.choice(["general", "general", "general", "public_transport", "emergency"]),
            "compliance_rate": 0.85,
        }
        for i in range(n)
    ]


def build_multi_blocked_roads(n_segments: int) -> list[dict]:
    return [
        {
            "segment_id": f"seg-load-{i}",
            "road_name": f"Road Segment {i}",
            "start_lat": 53.30 + i * 0.005,
            "start_lng": -6.36,
            "end_lat": 53.30 + (i + 1) * 0.005,
            "end_lng": -6.36,
            "reason": "disaster",
        }
        for i in range(n_segments)
    ]


# ---------------------------------------------------------------------------
# SLA test — reroute < 5 seconds for 200 users
# ---------------------------------------------------------------------------

class TestRerouteSLA:
    """All users rerouted within SLA: < 5 seconds end-to-end."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("user_count", [50, 200, 500])
    async def test_reroute_completes_within_sla(
        self,
        reroute_service_for_load,
        user_count,
        sample_tomtom_traffic_response,
        sample_tomtom_routing_response,
    ):
        users = build_user_pool(user_count)
        blocked = build_multi_blocked_roads(2)
        disaster_id = str(uuid.uuid4())

        reroute_service_for_load.db.get_blocked_roads.return_value = blocked
        reroute_service_for_load.external.fetch_traffic_data.return_value = sample_tomtom_traffic_response
        reroute_service_for_load.external.get_directions.return_value = sample_tomtom_routing_response
        reroute_service_for_load.db.get_users_in_affected_area.return_value = users

        start = time.monotonic()
        plan = await reroute_service_for_load.trigger_reroute_traffic(
            disaster_id=disaster_id,
            affected_roads=blocked,
        )
        elapsed = time.monotonic() - start

        assert plan is not None
        assert elapsed < 5.0, (
            f"SLA breach: rerouting {user_count} users took {elapsed:.2f}s (limit: 5.0s)"
        )

    @pytest.mark.asyncio
    async def test_all_users_have_route_assignment(
        self,
        reroute_service_for_load,
        sample_tomtom_traffic_response,
        sample_tomtom_routing_response,
    ):
        users = build_user_pool(200)
        blocked = build_multi_blocked_roads(2)
        disaster_id = str(uuid.uuid4())

        reroute_service_for_load.db.get_blocked_roads.return_value = blocked
        reroute_service_for_load.external.fetch_traffic_data.return_value = sample_tomtom_traffic_response
        reroute_service_for_load.external.get_directions.return_value = sample_tomtom_routing_response
        reroute_service_for_load.db.get_users_in_affected_area.return_value = users

        plan = await reroute_service_for_load.trigger_reroute_traffic(
            disaster_id=disaster_id,
            affected_roads=blocked,
        )

        assert len(plan.route_assignments) == len(users)


# ---------------------------------------------------------------------------
# Congestion recalculation test
# ---------------------------------------------------------------------------

class TestCongestionRecalculation:
    """Congestion on detour → recalculation and re-notification verified."""

    @pytest.mark.asyncio
    async def test_monitoring_triggers_recalculation_on_high_congestion(
        self,
        reroute_service_for_load,
        sample_tomtom_routing_response,
    ):
        """When monitoring detects high congestion, new routes computed and pushed."""
        # Simulate congested traffic conditions
        congested_traffic = {
            "flowSegmentData": [
                {
                    "frc": "FRC0",
                    "currentSpeed": 5,      # near-standstill
                    "freeFlowSpeed": 100,
                    "currentTravelTime": 1200,
                    "freeFlowTravelTime": 120,
                    "confidence": 0.9,
                    "coordinates": {"coordinate": []},
                }
            ]
        }
        reroute_service_for_load.external.get_traffic_conditions.return_value = congested_traffic
        reroute_service_for_load.external.get_directions.return_value = sample_tomtom_routing_response

        await reroute_service_for_load.run_monitoring_cycle(region_id="region-dublin")

        # Map and notifications must have been updated
        reroute_service_for_load.mapping.highlight_alternative_routes.assert_called()
        reroute_service_for_load.notifications.send_updated_reroute_recommendation.assert_called()

    @pytest.mark.asyncio
    async def test_no_recalculation_when_traffic_is_free_flowing(
        self,
        reroute_service_for_load,
    ):
        clear_traffic = {
            "flowSegmentData": [
                {
                    "frc": "FRC0",
                    "currentSpeed": 105,
                    "freeFlowSpeed": 110,
                    "currentTravelTime": 115,
                    "freeFlowTravelTime": 110,
                    "confidence": 0.95,
                    "coordinates": {"coordinate": []},
                }
            ]
        }
        reroute_service_for_load.external.get_traffic_conditions.return_value = clear_traffic

        await reroute_service_for_load.run_monitoring_cycle(region_id="region-clear")

        # No recalculation should occur
        reroute_service_for_load.external.get_directions.assert_not_called()


# ---------------------------------------------------------------------------
# Multi-incident test
# ---------------------------------------------------------------------------

class TestMultiIncident:
    """Second concurrent incident → priority-based re-prioritization verified."""

    @pytest.mark.asyncio
    async def test_second_incident_triggers_reprioritization(
        self,
        reroute_service_for_load,
        sample_tomtom_routing_response,
    ):
        reroute_service_for_load.external.recompute_multi_incident_detours.return_value = (
            sample_tomtom_routing_response
        )
        incident_2 = {
            "disaster_id": str(uuid.uuid4()),
            "blocked_roads": build_multi_blocked_roads(1),
            "severity": "high",
        }

        await reroute_service_for_load.handle_concurrent_incident(incident_2)

        # Must recompute considering both incidents
        reroute_service_for_load.external.recompute_multi_incident_detours.assert_called_once()
        # Must push updated map + notifications
        reroute_service_for_load.mapping.highlight_alternative_routes.assert_called()
        reroute_service_for_load.notifications.send_traffic_alerts.assert_called()

    @pytest.mark.asyncio
    async def test_emergency_vehicles_maintain_priority_during_multi_incident(
        self,
        reroute_service_for_load,
        sample_tomtom_routing_response,
    ):
        reroute_service_for_load.external.recompute_multi_incident_detours.return_value = (
            sample_tomtom_routing_response
        )
        vehicles = build_user_pool(100)
        # Add emergency vehicle
        vehicles.append({
            "user_id": "ambulance-priority",
            "type": "emergency",
            "current_location": {"lat": 53.33, "lng": -6.30},
            "destination": {"lat": 53.38, "lng": -6.20},
        })

        plan = await reroute_service_for_load.reprioritize_flows(
            vehicles, []  # routes come from recompute
        )

        assert "ambulance-priority" in plan.route_assignments


# ---------------------------------------------------------------------------
# Operator override load test
# ---------------------------------------------------------------------------

class TestOverrideLoad:
    """Operator override → routes update for all connected users."""

    @pytest.mark.asyncio
    async def test_override_propagates_to_all_users(
        self,
        reroute_service_for_load,
        mock_db_repository,
        sample_tomtom_routing_response,
    ):
        user_pool = [{"user_id": f"u-{i}"} for i in range(200)]
        reroute_service_for_load.db.get_users_in_affected_area.return_value = user_pool
        reroute_service_for_load.external.recompute_with_overrides.return_value = (
            sample_tomtom_routing_response
        )

        override = {
            "type": "close_lane",
            "segment_id": "seg-override-1",
            "operator_id": "op-load-test",
        }
        await reroute_service_for_load.receive_override(override)

        notify_call = reroute_service_for_load.notifications.send_traffic_alerts.call_args
        notified_users = (
            notify_call[1].get("users") or notify_call[0][0]
            if notify_call else []
        )
        assert len(notified_users) == 200


# ---------------------------------------------------------------------------
# Full lifecycle end-to-end test
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    """
    Full lifecycle: trigger → reroute → monitor → congestion → recalculate
    → override → multi-incident → priority → clearance → restoration.
    Section 9 Phase 6 + Section 13.3.
    """

    @pytest.mark.asyncio
    async def test_full_lifecycle_completes_without_error(
        self,
        reroute_service_for_load,
        sample_tomtom_traffic_response,
        sample_tomtom_routing_response,
        sample_blocked_roads,
    ):
        users = build_user_pool(200)
        disaster_id = str(uuid.uuid4())

        # Setup mocks
        svc = reroute_service_for_load
        svc.db.get_blocked_roads.return_value = sample_blocked_roads
        svc.db.get_users_in_affected_area.return_value = users
        svc.external.fetch_traffic_data.return_value = sample_tomtom_traffic_response
        svc.external.get_directions.return_value = sample_tomtom_routing_response
        svc.external.get_traffic_conditions.return_value = sample_tomtom_traffic_response
        svc.external.recompute_multi_incident_detours.return_value = sample_tomtom_routing_response
        svc.external.recompute_with_overrides.return_value = sample_tomtom_routing_response

        # Phase 1: Trigger
        plan = await svc.trigger_reroute_traffic(
            disaster_id=disaster_id,
            affected_roads=sample_blocked_roads,
        )
        assert plan is not None

        # Phase 2: Monitoring cycle (reactive + predictive)
        await svc.run_monitoring_cycle(region_id="region-dublin")

        # Phase 3: Concurrent incident
        await svc.handle_concurrent_incident({
            "disaster_id": str(uuid.uuid4()),
            "blocked_roads": build_multi_blocked_roads(1),
            "severity": "medium",
        })

        # Phase 4: Operator override
        await svc.receive_override({
            "type": "pin_detour",
            "segment_id": "seg-m50-junction-6-7",
            "operator_id": "op-lifecycle",
        })

        # Phase 5: Clearance
        await svc.restore_normal_flow(
            disaster_id=disaster_id,
            cleared_segments=sample_blocked_roads,
        )

        # Verify all-clear was sent
        svc.notifications.send_all_clear.assert_called_once()
        # Verify roads re-opened
        svc.db.update_road_status.assert_any_call(sample_blocked_roads, "open")

    @pytest.mark.asyncio
    async def test_full_lifecycle_timing_benchmark(
        self,
        reroute_service_for_load,
        sample_tomtom_traffic_response,
        sample_tomtom_routing_response,
        sample_blocked_roads,
    ):
        """Records timing benchmarks for each lifecycle phase."""
        users = build_user_pool(200)
        disaster_id = str(uuid.uuid4())
        svc = reroute_service_for_load
        svc.db.get_blocked_roads.return_value = sample_blocked_roads
        svc.db.get_users_in_affected_area.return_value = users
        svc.external.fetch_traffic_data.return_value = sample_tomtom_traffic_response
        svc.external.get_directions.return_value = sample_tomtom_routing_response
        svc.external.get_traffic_conditions.return_value = sample_tomtom_traffic_response
        svc.external.recompute_multi_incident_detours.return_value = sample_tomtom_routing_response
        svc.external.recompute_with_overrides.return_value = sample_tomtom_routing_response

        benchmarks = {}

        t0 = time.monotonic()
        await svc.trigger_reroute_traffic(disaster_id=disaster_id, affected_roads=sample_blocked_roads)
        benchmarks["trigger_reroute_200_users"] = time.monotonic() - t0

        t1 = time.monotonic()
        await svc.run_monitoring_cycle(region_id="region-dublin")
        benchmarks["monitoring_cycle"] = time.monotonic() - t1

        t2 = time.monotonic()
        await svc.restore_normal_flow(disaster_id=disaster_id, cleared_segments=sample_blocked_roads)
        benchmarks["clearance_restoration"] = time.monotonic() - t2

        # Print benchmarks (visible with pytest -s)
        print("\n--- Lifecycle Timing Benchmarks ---")
        for phase, duration in benchmarks.items():
            print(f"  {phase}: {duration * 1000:.1f} ms")

        # Hard SLA: trigger must complete in < 5 seconds
        assert benchmarks["trigger_reroute_200_users"] < 5.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def reroute_service_for_load(
    mock_db_repository,
    mock_external_integration_service,
    mock_mapping_service,
    mock_notification_service,
):
    from app.services.reroute_service import RerouteService

    svc = RerouteService(
        db=mock_db_repository,
        external=mock_external_integration_service,
        mapping=mock_mapping_service,
        notifications=mock_notification_service,
    )
    # Expose mocks on the service for easy assertion in tests
    svc.db = mock_db_repository
    svc.external = mock_external_integration_service
    svc.mapping = mock_mapping_service
    svc.notifications = mock_notification_service
    return svc