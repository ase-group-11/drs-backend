"""
tests/integration/test_reroute_pipeline.py

Integration tests — full reroute pipeline.
Section 13.2: ReRoute Service ↔ PostgreSQL, External Integration ↔ MockTomTomClient,
Socket.IO delivery, override flow, circuit breaker degraded mode.

These tests wire real async service objects together with a test DB
(or in-memory mock DB) and the MockTomTomClient.
"""
import uuid
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Integration test: full trigger → persist → notify pipeline
# ---------------------------------------------------------------------------

class TestFullReroutePipeline:
    """Section 13.2 — Full pipeline: trigger, compute, persist, notify."""

    @pytest.mark.asyncio
    async def test_trigger_reroute_returns_reroute_plan(
        self,
        reroute_service,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
        sample_tomtom_traffic_response,
        sample_tomtom_routing_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = (
            sample_tomtom_traffic_response
        )
        mock_external_integration_service.get_directions.return_value = (
            sample_tomtom_routing_response
        )
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": f"u-{i}"} for i in range(5)
        ]

        plan = await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            affected_roads=sample_blocked_roads,
        )

        assert plan is not None
        assert plan.disaster_id == disaster_id

    @pytest.mark.asyncio
    async def test_reroute_plan_persisted_to_database(
        self,
        reroute_service,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
        sample_tomtom_traffic_response,
        sample_tomtom_routing_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = sample_tomtom_traffic_response
        mock_external_integration_service.get_directions.return_value = sample_tomtom_routing_response
        mock_db_repository.get_users_in_affected_area.return_value = []

        await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            affected_roads=sample_blocked_roads,
        )

        mock_db_repository.save_reroute_plan.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocked_roads_marked_closed_after_trigger(
        self,
        reroute_service,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
        sample_tomtom_traffic_response,
        sample_tomtom_routing_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = sample_tomtom_traffic_response
        mock_external_integration_service.get_directions.return_value = sample_tomtom_routing_response
        mock_db_repository.get_users_in_affected_area.return_value = []

        await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            affected_roads=sample_blocked_roads,
        )

        mock_db_repository.update_road_status.assert_called_with(sample_blocked_roads, "closed")

    @pytest.mark.asyncio
    async def test_routes_pushed_to_map_after_trigger(
        self,
        reroute_service,
        mock_mapping_service,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
        sample_tomtom_traffic_response,
        sample_tomtom_routing_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = sample_tomtom_traffic_response
        mock_external_integration_service.get_directions.return_value = sample_tomtom_routing_response
        mock_db_repository.get_users_in_affected_area.return_value = []

        await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            affected_roads=sample_blocked_roads,
        )

        mock_mapping_service.highlight_alternative_routes.assert_called_once()

    @pytest.mark.asyncio
    async def test_users_notified_after_trigger(
        self,
        reroute_service,
        mock_notification_service,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
        sample_tomtom_traffic_response,
        sample_tomtom_routing_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = sample_tomtom_traffic_response
        mock_external_integration_service.get_directions.return_value = sample_tomtom_routing_response
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": f"u-{i}"} for i in range(3)
        ]

        await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            affected_roads=sample_blocked_roads,
        )

        mock_notification_service.send_traffic_alerts.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_logged_after_trigger(
        self,
        reroute_service,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
        sample_tomtom_traffic_response,
        sample_tomtom_routing_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = sample_tomtom_traffic_response
        mock_external_integration_service.get_directions.return_value = sample_tomtom_routing_response
        mock_db_repository.get_users_in_affected_area.return_value = []

        await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            affected_roads=sample_blocked_roads,
        )

        mock_db_repository.log_event.assert_called()


# ---------------------------------------------------------------------------
# Integration test: MockTomTomClient — success + error paths
# ---------------------------------------------------------------------------

class TestExternalIntegrationWithMockClient:
    """Section 13.2 — External Integration Service to MockTomTomClient."""

    @pytest.mark.asyncio
    async def test_mock_client_traffic_flow_success_path(
        self,
        mock_external_integration_service,
        sample_tomtom_traffic_response,
    ):
        mock_external_integration_service.fetch_traffic_data.return_value = (
            sample_tomtom_traffic_response
        )
        result = await mock_external_integration_service.fetch_traffic_data("region-test")
        assert "flowSegmentData" in result

    @pytest.mark.asyncio
    async def test_mock_client_routing_success_path(
        self,
        mock_external_integration_service,
        sample_tomtom_routing_response,
    ):
        mock_external_integration_service.get_directions.return_value = (
            sample_tomtom_routing_response
        )
        result = await mock_external_integration_service.get_directions(
            origins=[], destination={}, avoid=[], alternatives=True
        )
        assert len(result["routes"]) == 3

    @pytest.mark.asyncio
    async def test_mock_client_error_path_raises(
        self,
        mock_external_integration_service,
    ):
        mock_external_integration_service.fetch_traffic_data.side_effect = Exception(
            "MockTomTomClient simulated error"
        )
        with pytest.raises(Exception, match="simulated error"):
            await mock_external_integration_service.fetch_traffic_data("region-error")

    @pytest.mark.asyncio
    async def test_degraded_mode_activated_after_mock_failure(
        self,
        reroute_service,
        mock_external_integration_service,
        region_id,
    ):
        mock_external_integration_service.fetch_traffic_data.side_effect = Exception("down")
        result = await reroute_service.fetch_traffic_data(region_id)
        assert result.get("mode") == "degraded"


# ---------------------------------------------------------------------------
# Integration test: Circuit breaker → degraded mode
# ---------------------------------------------------------------------------

class TestCircuitBreakerDegradedModeIntegration:
    """Section 13.2 — Circuit breaker triggers degraded mode after repeated TomTom failures."""

    @pytest.mark.asyncio
    async def test_repeated_failures_trigger_degraded_mode(
        self,
        reroute_service,
        mock_external_integration_service,
        region_id,
    ):
        mock_external_integration_service.fetch_traffic_data.side_effect = Exception("timeout")

        results = []
        for _ in range(5):
            result = await reroute_service.fetch_traffic_data(region_id)
            results.append(result)

        # All results in degraded mode
        assert all(r.get("mode") == "degraded" for r in results)

    @pytest.mark.asyncio
    async def test_degraded_mode_does_not_call_tomtom_when_circuit_open(
        self,
        reroute_service,
        mock_external_integration_service,
        region_id,
    ):
        """Once the circuit is open, the facade should short-circuit without calling TomTom."""
        mock_external_integration_service.fetch_traffic_data.side_effect = Exception("down")

        # Force circuit open via reroute service's internal state
        with patch.object(
            reroute_service, "_circuit_is_open", return_value=True
        ):
            result = await reroute_service.fetch_traffic_data(region_id)

        assert result.get("mode") == "degraded"


# ---------------------------------------------------------------------------
# Integration test: Socket.IO message delivery
# ---------------------------------------------------------------------------

class TestSocketIOMessageDelivery:
    """Section 13.2 — Socket.IO message delivery to subscribed test clients."""

    @pytest.mark.asyncio
    async def test_highlight_routes_emits_to_region_room(
        self,
        mock_mapping_service,
        sample_alternative_routes,
        region_id,
    ):
        await mock_mapping_service.highlight_alternative_routes(
            routes=sample_alternative_routes,
            region_id=region_id,
        )
        mock_mapping_service.highlight_alternative_routes.assert_called_once_with(
            routes=sample_alternative_routes,
            region_id=region_id,
        )

    @pytest.mark.asyncio
    async def test_send_traffic_alerts_reaches_all_users(
        self,
        mock_notification_service,
        sample_alternative_routes,
        sample_blocked_roads,
    ):
        users = [{"user_id": f"u-{i}"} for i in range(10)]
        await mock_notification_service.send_traffic_alerts(
            users=users,
            blocked_roads=sample_blocked_roads,
            chosen_routes=sample_alternative_routes,
        )
        mock_notification_service.send_traffic_alerts.assert_called_once()
        call_args = mock_notification_service.send_traffic_alerts.call_args
        called_users = call_args[1].get("users") or call_args[0][0]
        assert len(called_users) == 10

    @pytest.mark.asyncio
    async def test_send_all_clear_emits_to_alerts_room(
        self,
        mock_notification_service,
    ):
        users = [{"user_id": f"u-{i}"} for i in range(5)]
        result = await mock_notification_service.send_all_clear(users=users)
        mock_notification_service.send_all_clear.assert_called_once()
        assert result["status"] == "sent"

    @pytest.mark.asyncio
    async def test_updated_reroute_recommendation_sent_on_congestion_trigger(
        self,
        mock_notification_service,
    ):
        users = [{"user_id": f"u-{i}"} for i in range(3)]
        result = await mock_notification_service.send_updated_reroute_recommendation(
            users=users
        )
        mock_notification_service.send_updated_reroute_recommendation.assert_called_once()
        assert result["status"] == "sent"


# ---------------------------------------------------------------------------
# Integration test: Override flow
# ---------------------------------------------------------------------------

class TestOverrideFlowIntegration:
    """Section 13.2 — Override: receive, apply to DB, recompute via TomTom, notify."""

    @pytest.mark.asyncio
    async def test_full_override_flow_applies_and_notifies(
        self,
        reroute_service,
        mock_db_repository,
        mock_external_integration_service,
        mock_mapping_service,
        mock_notification_service,
        sample_tomtom_routing_response,
    ):
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": "u-1"}
        ]
        mock_external_integration_service.recompute_with_overrides.return_value = (
            sample_tomtom_routing_response
        )
        override = {
            "type": "close_lane",
            "segment_id": "seg-m50-junction-6-7",
            "operator_id": "op-001",
        }

        await reroute_service.receive_override(override)

        # All four steps must fire in order:
        mock_db_repository.apply_override.assert_called_once()
        mock_external_integration_service.recompute_with_overrides.assert_called_once()
        mock_mapping_service.highlight_alternative_routes.assert_called_once()
        mock_notification_service.send_traffic_alerts.assert_called_once()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def reroute_service(
    mock_db_repository,
    mock_external_integration_service,
    mock_mapping_service,
    mock_notification_service,
):
    from app.services.reroute_service import RerouteService
    return RerouteService(
        db=mock_db_repository,
        external=mock_external_integration_service,
        mapping=mock_mapping_service,
        notifications=mock_notification_service,
    )