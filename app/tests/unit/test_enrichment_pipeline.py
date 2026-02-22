"""
Unit tests for the EnrichmentPipeline.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.evaluation.enrichment import (
    EnrichmentPipeline,
    MockWeatherProvider,
    WeatherContext,
)


@pytest.fixture
def mock_traffic_provider():
    provider = MagicMock()
    provider.get_session = AsyncMock(return_value=MagicMock())
    provider.fetch_flow_at_point = AsyncMock(
        return_value=[{"congestion_level": "moderate", "current_speed": 40}]
    )
    return provider


@pytest.fixture
def mock_weather_provider():
    provider = AsyncMock()
    provider.get_weather_at_point = AsyncMock(
        return_value=WeatherContext(
            temperature_c=15.0,
            condition="clear",
            wind_speed_kmh=10.0,
            source="mock",
        )
    )
    return provider


@pytest.fixture
def pipeline(mock_traffic_provider, mock_weather_provider):
    return EnrichmentPipeline(
        traffic_provider=mock_traffic_provider,
        weather_provider=mock_weather_provider,
    )


# ---------------------------------------------------------------------------
# MockWeatherProvider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mock_weather_provider_returns_expected_structure():
    provider = MockWeatherProvider()
    ctx = await provider.get_weather_at_point(53.35, -6.26)
    assert ctx.temperature_c == 15.0
    assert ctx.condition == "clear"
    assert ctx.wind_speed_kmh == 10.0
    assert ctx.source == "mock"


# ---------------------------------------------------------------------------
# EnrichmentPipeline — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_returns_traffic_and_weather(pipeline):
    traffic_ctx, weather_ctx = await pipeline.enrich(53.35, -6.26)
    assert traffic_ctx is not None
    assert weather_ctx is not None
    assert "flow" in traffic_ctx
    assert "condition" in weather_ctx


@pytest.mark.asyncio
async def test_enrich_weather_structure(pipeline):
    _, weather_ctx = await pipeline.enrich(53.35, -6.26)
    assert weather_ctx["temperature_c"] == 15.0
    assert weather_ctx["condition"] == "clear"
    assert weather_ctx["source"] == "mock"


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_traffic_failure_returns_none(mock_weather_provider):
    failing_traffic = MagicMock()
    failing_traffic.get_session = AsyncMock(return_value=MagicMock())
    failing_traffic.fetch_flow_at_point = AsyncMock(
        side_effect=Exception("TomTom API error")
    )
    pipeline = EnrichmentPipeline(
        traffic_provider=failing_traffic,
        weather_provider=mock_weather_provider,
    )
    traffic_ctx, weather_ctx = await pipeline.enrich(53.35, -6.26)
    assert traffic_ctx is None
    assert weather_ctx is not None  # weather still succeeds


@pytest.mark.asyncio
async def test_weather_failure_returns_none(mock_traffic_provider):
    failing_weather = AsyncMock()
    failing_weather.get_weather_at_point = AsyncMock(
        side_effect=Exception("Weather API error")
    )
    pipeline = EnrichmentPipeline(
        traffic_provider=mock_traffic_provider,
        weather_provider=failing_weather,
    )
    traffic_ctx, weather_ctx = await pipeline.enrich(53.35, -6.26)
    assert traffic_ctx is not None  # traffic still succeeds
    assert weather_ctx is None


@pytest.mark.asyncio
async def test_both_failures_return_none_none(mock_traffic_provider):
    failing_traffic = MagicMock()
    failing_traffic.get_session = AsyncMock(return_value=MagicMock())
    failing_traffic.fetch_flow_at_point = AsyncMock(side_effect=Exception("fail"))
    failing_weather = AsyncMock()
    failing_weather.get_weather_at_point = AsyncMock(side_effect=Exception("fail"))

    pipeline = EnrichmentPipeline(
        traffic_provider=failing_traffic,
        weather_provider=failing_weather,
    )
    traffic_ctx, weather_ctx = await pipeline.enrich(53.35, -6.26)
    assert traffic_ctx is None
    assert weather_ctx is None


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_both_providers_called(pipeline, mock_traffic_provider, mock_weather_provider):
    await pipeline.enrich(53.35, -6.26)
    mock_traffic_provider.fetch_flow_at_point.assert_called_once()
    mock_weather_provider.get_weather_at_point.assert_called_once_with(53.35, -6.26)
