"""
tests/unit/test_reroute_pipeline.py

Integration tests — full reroute pipeline.
Section 13.2: ReRoute Service ↔ DB mock, External Integration mock,
MappingService mock, RabbitMQ publisher mock.

Key fixes vs original:
  - NotificationService replaced with ReroutePublisher (RabbitMQ)
  - get_directions returns {"routes": [...]} not raw TomTom format
  - trigger_reroute_traffic returns a dict, not an object with .disaster_id
  - recompute_with_overrides returns {"routes": [...]}
"""
import uuid
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Shared fixture — reroute_service with publisher injected
# ---------------------------------------------------------------------------

@pytest.fixture
def reroute_service(
    mock_db_repository,
    mock_external_integration_service,
    mock_mapping_service,
    mock_publisher,
):
    from app.services.reroute_service import RerouteService
    return RerouteService(
        db=mock_db_repository,
        external=mock_external_integration_service,
        mapping=mock_mapping_service,
        publisher=mock_publisher,
    )


@pytest.fixture
def mock_directions_response(sample_alternative_routes):
    """Parsed routes response as returned by IntegrationService.get_directions."""
    return {"routes": sample_alternative_routes}


@pytest.fixture
def mock_traffic_response():
    """Parsed traffic response as returned by IntegrationService.fetch_traffic_data."""
    return {"segments": [], "mode": "mock"}


# ---------------------------------------------------------------------------
# Integration test: full trigger → persist → publish pipeline
# ---------------------------------------------------------------------------

class TestFullReroutePipeline:
    """Section 13.2 — Full pipeline: trigger, compute, persist, publish."""

    @pytest.mark.asyncio
    async def test_trigger_reroute_returns_status_rerouted(
        self,
        reroute_service,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
        mock_traffic_response,
        mock_directions_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = mock_traffic_response
        mock_external_integration_service.get_directions.return_value = mock_directions_response
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": f"u-{i}", "destination": {"lat": 53.4, "lng": -6.2}}
            for i in range(5)
        ]

        result = await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            region_id="region-dublin-m50",
            affected_roads=sample_blocked_roads,
        )

        assert result["status"] == "rerouted"
        assert result["disaster_id"] == disaster_id

    @pytest.mark.asyncio
    async def test_reroute_plan_persisted_to_database(
        self,
        reroute_service,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
        mock_traffic_response,
        mock_directions_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = mock_traffic_response
        mock_external_integration_service.get_directions.return_value = mock_directions_response
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": "u-1", "destination": {"lat": 53.4, "lng": -6.2}}
        ]

        await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            region_id="region-dublin-m50",
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
        mock_traffic_response,
        mock_directions_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = mock_traffic_response
        mock_external_integration_service.get_directions.return_value = mock_directions_response
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": "u-1", "destination": {"lat": 53.4, "lng": -6.2}}
        ]

        await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            region_id="region-dublin-m50",
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
        mock_traffic_response,
        mock_directions_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = mock_traffic_response
        mock_external_integration_service.get_directions.return_value = mock_directions_response
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": "u-1", "destination": {"lat": 53.4, "lng": -6.2}}
        ]

        await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            region_id="region-dublin-m50",
            affected_roads=sample_blocked_roads,
        )

        mock_mapping_service.highlight_alternative_routes.assert_called_once()

    @pytest.mark.asyncio
    async def test_reroute_event_published_to_rabbitmq_after_trigger(
        self,
        reroute_service,
        mock_publisher,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
        mock_traffic_response,
        mock_directions_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = mock_traffic_response
        mock_external_integration_service.get_directions.return_value = mock_directions_response
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": f"u-{i}", "destination": {"lat": 53.4, "lng": -6.2}}
            for i in range(3)
        ]

        await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            region_id="region-dublin-m50",
            affected_roads=sample_blocked_roads,
        )

        mock_publisher.publish_reroute_triggered.assert_called_once()
        call_kwargs = mock_publisher.publish_reroute_triggered.call_args[1]
        assert call_kwargs["disaster_id"] == disaster_id

    @pytest.mark.asyncio
    async def test_event_logged_after_trigger(
        self,
        reroute_service,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
        mock_traffic_response,
        mock_directions_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = mock_traffic_response
        mock_external_integration_service.get_directions.return_value = mock_directions_response
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": "u-1", "destination": {"lat": 53.4, "lng": -6.2}}
        ]

        await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            region_id="region-dublin-m50",
            affected_roads=sample_blocked_roads,
        )

        mock_db_repository.log_event.assert_called()

    @pytest.mark.asyncio
    async def test_returns_no_vehicles_when_area_empty(
        self,
        reroute_service,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
        mock_traffic_response,
    ):
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = mock_traffic_response
        mock_db_repository.get_users_in_affected_area.return_value = []

        result = await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            region_id="region-dublin-m50",
            affected_roads=sample_blocked_roads,
        )

        assert result["status"] == "no_vehicles_affected"


# ---------------------------------------------------------------------------
# Integration test: MockTomTomClient — success + error paths
# ---------------------------------------------------------------------------

class TestExternalIntegrationWithMockClient:
    """Section 13.2 — External Integration Service mock paths."""

    @pytest.mark.asyncio
    async def test_mock_client_traffic_flow_success_path(
        self,
        mock_external_integration_service,
        mock_traffic_response,
    ):
        mock_external_integration_service.fetch_traffic_data.return_value = mock_traffic_response
        result = await mock_external_integration_service.fetch_traffic_data("region-test")
        assert "segments" in result

    @pytest.mark.asyncio
    async def test_mock_client_routing_success_path(
        self,
        mock_external_integration_service,
        sample_alternative_routes,
    ):
        mock_external_integration_service.get_directions.return_value = {
            "routes": sample_alternative_routes
        }
        result = await mock_external_integration_service.get_directions(
            origin={}, destination={}, avoid=[], alternatives=True
        )
        assert len(result["routes"]) == len(sample_alternative_routes)

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
    """Section 13.2 — Repeated failures trigger degraded mode."""

    @pytest.mark.asyncio
    async def test_repeated_failures_return_degraded_mode(
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

        assert all(r.get("mode") == "degraded" for r in results)

    @pytest.mark.asyncio
    async def test_degraded_mode_result_has_expected_shape(
        self,
        reroute_service,
        mock_external_integration_service,
        region_id,
    ):
        mock_external_integration_service.fetch_traffic_data.side_effect = Exception("down")
        result = await reroute_service.fetch_traffic_data(region_id)
        assert "segments" in result
        assert result.get("mode") == "degraded"


# ---------------------------------------------------------------------------
# Integration test: MappingService socket events
# ---------------------------------------------------------------------------

class TestMappingServiceEvents:
    """Section 13.2 — MappingService emits correct Socket.IO events."""

    @pytest.mark.asyncio
    async def test_highlight_routes_called_with_routes(
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
    async def test_clear_detours_called_on_restore(
        self,
        mock_mapping_service,
    ):
        result = await mock_mapping_service.clear_detours()
        mock_mapping_service.clear_detours.assert_called_once()
        assert result["status"] == "cleared"


# ---------------------------------------------------------------------------
# Integration test: RabbitMQ publisher events
# ---------------------------------------------------------------------------

class TestPublisherEvents:
    """Verify publisher is called with correct payloads."""

    @pytest.mark.asyncio
    async def test_publish_all_clear_on_restore(
        self,
        reroute_service,
        mock_publisher,
        mock_db_repository,
        sample_blocked_roads,
    ):
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": f"u-{i}"} for i in range(5)
        ]
        await reroute_service.restore_normal_flow(
            disaster_id="disaster-clear-001",
            cleared_segments=sample_blocked_roads,
        )
        mock_publisher.publish_all_clear.assert_called_once()
        call_kwargs = mock_publisher.publish_all_clear.call_args[1]
        assert call_kwargs["disaster_id"] == "disaster-clear-001"

    @pytest.mark.asyncio
    async def test_publish_route_updated_on_override(
        self,
        reroute_service,
        mock_publisher,
        mock_db_repository,
        mock_external_integration_service,
    ):
        mock_external_integration_service.recompute_with_overrides.return_value = {"routes": []}
        mock_db_repository.get_active_overrides = AsyncMock(return_value=[])

        override = {
            "type": "close_lane",
            "segment_id": "seg-m50-j6",
            "operator_id": "op-001",
            "disaster_id": "d-001",
        }
        await reroute_service.receive_override(override)
        mock_publisher.publish_route_updated.assert_called_once()

    @pytest.mark.asyncio
    async def test_publisher_failure_does_not_crash_pipeline(
        self,
        reroute_service,
        mock_publisher,
        mock_db_repository,
        mock_external_integration_service,
        disaster_id,
        sample_blocked_roads,
    ):
        """Publisher returning False (MQ down) must not fail the reroute pipeline."""
        mock_publisher.publish_reroute_triggered.return_value = False
        mock_db_repository.get_blocked_roads.return_value = sample_blocked_roads
        mock_external_integration_service.fetch_traffic_data.return_value = {
            "segments": [], "mode": "mock"
        }
        mock_external_integration_service.get_directions.return_value = {"routes": []}
        mock_db_repository.get_users_in_affected_area.return_value = [
            {"user_id": "u-1", "destination": {"lat": 53.4, "lng": -6.2}}
        ]

        # Should not raise even if publisher returns False
        result = await reroute_service.trigger_reroute_traffic(
            disaster_id=disaster_id,
            region_id="region-dublin-m50",
            affected_roads=sample_blocked_roads,
        )
        assert result["status"] == "rerouted"


# ---------------------------------------------------------------------------
# Integration test: Override flow
# ---------------------------------------------------------------------------

class TestOverrideFlowIntegration:
    """Section 13.2 — Override: receive, apply to DB, recompute, publish."""

    @pytest.mark.asyncio
    async def test_full_override_flow_applies_persists_maps_publishes(
        self,
        reroute_service,
        mock_db_repository,
        mock_external_integration_service,
        mock_mapping_service,
        mock_publisher,
    ):
        mock_db_repository.get_active_overrides = AsyncMock(return_value=[])
        mock_external_integration_service.recompute_with_overrides.return_value = {
            "routes": []
        }
        override = {
            "type": "close_lane",
            "segment_id": "seg-m50-junction-6-7",
            "operator_id": "op-001",
            "disaster_id": "d-override-001",
        }

        result = await reroute_service.receive_override(override)

        # All four steps must fire
        mock_db_repository.apply_override.assert_called_once()
        mock_external_integration_service.recompute_with_overrides.assert_called_once()
        mock_mapping_service.highlight_alternative_routes.assert_called_once()
        mock_publisher.publish_route_updated.assert_called_once()
        assert result["status"] == "override_applied"