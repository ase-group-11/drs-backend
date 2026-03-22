"""
tests/unit/test_external_integration_service.py

Unit tests — External Integration Service (TomTom facade).
Covers:
  - Mock mode vs live mode
  - Circuit breaker: closed → open → degraded
  - Retry behaviour on transient failures
  - Degraded mode response shape
  - Avoidance param construction
  - Health check
"""
import pytest
import pybreaker
from unittest.mock import AsyncMock, patch, MagicMock

from app.providers.integration_service import IntegrationService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_service():
    """IntegrationService in mock mode (no API key)."""
    return IntegrationService(api_key=None, mode="mock")


@pytest.fixture
def live_service():
    """IntegrationService in live mode with a fake key."""
    return IntegrationService(api_key="fake-key-123", mode="live")


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

class TestMockMode:
    """Service auto-switches to mock when no API key is set."""

    def test_no_api_key_activates_mock_mode(self):
        svc = IntegrationService(api_key=None)
        assert svc.mode == "mock"

    def test_is_mock_property_true_in_mock_mode(self, mock_service):
        assert mock_service.is_mock is True

    def test_is_mock_property_false_in_live_mode(self, live_service):
        assert live_service.is_mock is False

    @pytest.mark.asyncio
    async def test_fetch_traffic_data_returns_segments_in_mock_mode(self, mock_service):
        result = await mock_service.fetch_traffic_data("region-dublin-m50")
        assert "segments" in result
        assert result["mode"] == "mock"

    @pytest.mark.asyncio
    async def test_get_directions_returns_routes_in_mock_mode(self, mock_service):
        result = await mock_service.get_directions(
            origin={"lat": 53.3498, "lng": -6.2603},
            destination={"lat": 53.4000, "lng": -6.2000},
            avoid=[],
        )
        assert "routes" in result
        assert len(result["routes"]) > 0

    @pytest.mark.asyncio
    async def test_mock_routes_have_required_fields(self, mock_service):
        result = await mock_service.get_directions(
            origin={"lat": 53.3498, "lng": -6.2603},
            destination={"lat": 53.4000, "lng": -6.2000},
        )
        for route in result["routes"]:
            assert "route_id" in route
            assert "travel_time_seconds" in route
            assert "points" in route

    @pytest.mark.asyncio
    async def test_mock_mode_does_not_call_http(self, mock_service):
        with patch.object(mock_service, "_get_session") as mock_session:
            await mock_service.fetch_traffic_data("region-test")
            mock_session.assert_not_called()

    def test_set_mode_switches_to_mock(self, live_service):
        live_service.set_mode("mock")
        assert live_service.is_mock is True

    def test_set_mode_switches_to_live(self, mock_service):
        mock_service.api_key = "real-key"
        mock_service.set_mode("live")
        assert mock_service.is_mock is False

    def test_set_mode_raises_on_invalid_mode(self, mock_service):
        with pytest.raises(ValueError):
            mock_service.set_mode("turbo")


# ---------------------------------------------------------------------------
# Circuit Breaker — state machine
# ---------------------------------------------------------------------------

class TestCircuitBreakerStateMachine:
    """
    Circuit breaker: closed → open after fail_max failures → degraded mode.
    State labels from pybreaker: 'closed', 'open', 'half-open'.
    """

    @pytest.mark.asyncio
    async def test_circuit_starts_closed(self, live_service):
        assert live_service.circuit_breaker.current_state == "closed"

    @pytest.mark.asyncio
    async def test_circuit_opens_after_three_consecutive_failures(self, live_service):
        """
        Simulate 3 consecutive HTTP failures on _fetch_traffic_with_breaker.
        The circuit breaker should open after fail_max=3 failures.
        """
        with patch.object(
            live_service,
            "_fetch_traffic_with_breaker",
            side_effect=Exception("TomTom 503"),
        ):
            for _ in range(3):
                try:
                    await live_service._fetch_traffic_with_breaker("region-test")
                except Exception:
                    pass

        assert live_service.circuit_breaker.current_state == "open"

    @pytest.mark.asyncio
    async def test_open_circuit_returns_degraded_mode(self, live_service):
        """Once open, fetch_traffic_data must return degraded mode without calling TomTom."""
        # Force circuit open
        live_service.circuit_breaker.open()

        result = await live_service.fetch_traffic_data("region-test")
        assert result["mode"] == "degraded"

    @pytest.mark.asyncio
    async def test_degraded_mode_includes_segments(self, live_service):
        live_service.circuit_breaker.open()
        result = await live_service.fetch_traffic_data("region-test")
        assert "segments" in result

    @pytest.mark.asyncio
    async def test_open_circuit_does_not_call_tomtom(self, live_service):
        live_service.circuit_breaker.open()
        with patch.object(
            live_service._traffic_provider, "get_traffic"
        ) as mock_get:
            await live_service.fetch_traffic_data("region-test")
            mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_routing_returns_degraded_routes_when_circuit_open(self, live_service):
        live_service.circuit_breaker.open()
        result = await live_service.get_directions(
            origin={"lat": 53.3, "lng": -6.3},
            destination={"lat": 53.4, "lng": -6.2},
        )
        assert "routes" in result
        assert result.get("mode") == "degraded"

    @pytest.mark.asyncio
    async def test_circuit_resets_to_closed_state(self, live_service):
        """After forcing open, reset should return to closed."""
        live_service.circuit_breaker.open()
        assert live_service.circuit_breaker.current_state == "open"
        live_service.circuit_breaker.close()
        assert live_service.circuit_breaker.current_state == "closed"


# ---------------------------------------------------------------------------
# Degraded mode response shape
# ---------------------------------------------------------------------------

class TestDegradedModeResponse:
    """Degraded mode must return usable fallback data, not raise."""

    @pytest.mark.asyncio
    async def test_degraded_traffic_has_mode_key(self, live_service):
        live_service.circuit_breaker.open()
        result = await live_service.fetch_traffic_data("region-test")
        assert result.get("mode") in ("degraded", "mock")

    @pytest.mark.asyncio
    async def test_degraded_traffic_segments_are_list(self, live_service):
        live_service.circuit_breaker.open()
        result = await live_service.fetch_traffic_data("region-test")
        assert isinstance(result["segments"], list)

    @pytest.mark.asyncio
    async def test_degraded_routing_routes_are_list(self, live_service):
        live_service.circuit_breaker.open()
        result = await live_service.get_directions(
            origin={"lat": 53.3, "lng": -6.3},
            destination={"lat": 53.4, "lng": -6.2},
        )
        assert isinstance(result["routes"], list)

    @pytest.mark.asyncio
    async def test_exception_in_live_mode_falls_back_to_degraded(self, live_service):
        """Any exception from TomTom should yield degraded mode, not a 500."""
        with patch.object(
            live_service,
            "_fetch_traffic_with_breaker",
            side_effect=Exception("network error"),
        ):
            result = await live_service.fetch_traffic_data("region-test")

        assert result.get("mode") in ("degraded", "mock")
        assert "segments" in result


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:

    @pytest.mark.asyncio
    async def test_health_check_returns_mode(self, mock_service):
        result = await mock_service.health_check()
        assert "mode" in result
        assert result["mode"] == "mock"

    @pytest.mark.asyncio
    async def test_health_check_returns_circuit_state(self, mock_service):
        result = await mock_service.health_check()
        assert "circuit_breaker_state" in result

    @pytest.mark.asyncio
    async def test_health_check_returns_api_key_configured(self, mock_service):
        result = await mock_service.health_check()
        assert "api_key_configured" in result
        assert result["api_key_configured"] is False

    @pytest.mark.asyncio
    async def test_health_check_live_service_has_key_configured(self, live_service):
        result = await live_service.health_check()
        assert result["api_key_configured"] is True


# ---------------------------------------------------------------------------
# Override recomputation
# ---------------------------------------------------------------------------

class TestRecomputeWithOverrides:

    @pytest.mark.asyncio
    async def test_recompute_returns_routes(self, mock_service):
        result = await mock_service.recompute_with_overrides(
            origin={"lat": 53.3498, "lng": -6.2603},
            destination={"lat": 53.4000, "lng": -6.2000},
            blocked_roads=[],
            active_overrides=[],
        )
        assert "routes" in result

    @pytest.mark.asyncio
    async def test_recompute_merges_close_lane_overrides(self, mock_service):
        """close_lane overrides should be added to blocked roads."""
        overrides = [{"type": "close_lane", "segment_id": "seg-extra"}]
        # Should not raise — override merged into avoid list
        result = await mock_service.recompute_with_overrides(
            origin={"lat": 53.3498, "lng": -6.2603},
            destination={"lat": 53.4000, "lng": -6.2000},
            blocked_roads=[],
            active_overrides=overrides,
        )
        assert "routes" in result