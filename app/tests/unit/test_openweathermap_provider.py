"""
Unit tests for OpenWeatherMapProvider.

All HTTP calls are mocked — no real network requests made.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.evaluation.enrichment import OpenWeatherMapProvider, WeatherContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_owm_response(
    temp: float = 18.5,
    wind_ms: float = 5.0,
    condition_id: int = 800,  # Clear sky
) -> dict:
    """Build a minimal OWM API response payload."""
    return {
        "main": {"temp": temp},
        "wind": {"speed": wind_ms},
        "weather": [{"id": condition_id, "description": "clear sky"}],
    }


def mock_aiohttp_session(response_data: dict, status: int = 200):
    """Return a context-manager mock for aiohttp.ClientSession that yields the given response."""
    mock_response = AsyncMock()
    mock_response.status = status
    mock_response.json = AsyncMock(return_value=response_data)
    mock_response.raise_for_status = MagicMock()
    if status >= 400:
        from aiohttp import ClientResponseError
        mock_response.raise_for_status.side_effect = ClientResponseError(
            request_info=MagicMock(), history=(), status=status
        )

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_ctx)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    return mock_session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_weather_context_on_success():
    provider = OpenWeatherMapProvider(api_key="test-key")
    session = mock_aiohttp_session(make_owm_response(temp=20.0, wind_ms=10.0, condition_id=800))

    with patch("aiohttp.ClientSession", return_value=session):
        result = await provider.get_weather_at_point(53.35, -6.26)

    assert isinstance(result, WeatherContext)
    assert result.temperature_c == 20.0
    assert result.source == "openweathermap"


@pytest.mark.asyncio
async def test_wind_speed_converted_from_ms_to_kmh():
    provider = OpenWeatherMapProvider(api_key="test-key")
    # 10 m/s == 36.0 km/h
    session = mock_aiohttp_session(make_owm_response(wind_ms=10.0))

    with patch("aiohttp.ClientSession", return_value=session):
        result = await provider.get_weather_at_point(53.35, -6.26)

    assert result.wind_speed_kmh == 36.0


@pytest.mark.asyncio
async def test_condition_clear_sky():
    provider = OpenWeatherMapProvider(api_key="test-key")
    session = mock_aiohttp_session(make_owm_response(condition_id=800))

    with patch("aiohttp.ClientSession", return_value=session):
        result = await provider.get_weather_at_point(53.35, -6.26)

    assert result.condition == "clear"


@pytest.mark.asyncio
async def test_condition_thunderstorm():
    provider = OpenWeatherMapProvider(api_key="test-key")
    session = mock_aiohttp_session(make_owm_response(condition_id=211))  # Thunderstorm

    with patch("aiohttp.ClientSession", return_value=session):
        result = await provider.get_weather_at_point(53.35, -6.26)

    assert result.condition == "storm"


@pytest.mark.asyncio
async def test_condition_rain():
    provider = OpenWeatherMapProvider(api_key="test-key")
    session = mock_aiohttp_session(make_owm_response(condition_id=501))  # Moderate rain

    with patch("aiohttp.ClientSession", return_value=session):
        result = await provider.get_weather_at_point(53.35, -6.26)

    assert result.condition == "rain"


@pytest.mark.asyncio
async def test_condition_drizzle_maps_to_rain():
    provider = OpenWeatherMapProvider(api_key="test-key")
    session = mock_aiohttp_session(make_owm_response(condition_id=301))  # Drizzle

    with patch("aiohttp.ClientSession", return_value=session):
        result = await provider.get_weather_at_point(53.35, -6.26)

    assert result.condition == "rain"


@pytest.mark.asyncio
async def test_condition_clouds():
    provider = OpenWeatherMapProvider(api_key="test-key")
    session = mock_aiohttp_session(make_owm_response(condition_id=803))  # Broken clouds

    with patch("aiohttp.ClientSession", return_value=session):
        result = await provider.get_weather_at_point(53.35, -6.26)

    assert result.condition == "cloudy"


@pytest.mark.asyncio
async def test_condition_snow_maps_to_storm():
    provider = OpenWeatherMapProvider(api_key="test-key")
    session = mock_aiohttp_session(make_owm_response(condition_id=601))  # Snow

    with patch("aiohttp.ClientSession", return_value=session):
        result = await provider.get_weather_at_point(53.35, -6.26)

    assert result.condition == "storm"


@pytest.mark.asyncio
async def test_condition_atmosphere_maps_to_cloudy():
    provider = OpenWeatherMapProvider(api_key="test-key")
    session = mock_aiohttp_session(make_owm_response(condition_id=741))  # Fog

    with patch("aiohttp.ClientSession", return_value=session):
        result = await provider.get_weather_at_point(53.35, -6.26)

    assert result.condition == "cloudy"


@pytest.mark.asyncio
async def test_unknown_condition_code_defaults_to_clear():
    provider = OpenWeatherMapProvider(api_key="test-key")
    session = mock_aiohttp_session(make_owm_response(condition_id=999))

    with patch("aiohttp.ClientSession", return_value=session):
        result = await provider.get_weather_at_point(53.35, -6.26)

    assert result.condition == "clear"


@pytest.mark.asyncio
async def test_http_error_propagates():
    """EnrichmentPipeline expects exceptions to bubble up so it can catch them."""
    from aiohttp import ClientResponseError

    provider = OpenWeatherMapProvider(api_key="bad-key")
    session = mock_aiohttp_session(make_owm_response(), status=401)

    with patch("aiohttp.ClientSession", return_value=session):
        with pytest.raises(ClientResponseError):
            await provider.get_weather_at_point(53.35, -6.26)


@pytest.mark.asyncio
async def test_api_key_is_passed_in_params():
    """Verify the API key is actually sent to OWM."""
    provider = OpenWeatherMapProvider(api_key="my-secret-key")
    session = mock_aiohttp_session(make_owm_response())

    with patch("aiohttp.ClientSession", return_value=session):
        await provider.get_weather_at_point(53.35, -6.26)

    call_kwargs = session.get.call_args
    params = call_kwargs[1]["params"]
    assert params["appid"] == "my-secret-key"
    assert params["units"] == "metric"
    assert params["lat"] == 53.35
    assert params["lon"] == -6.26
