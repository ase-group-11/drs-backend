"""
Enrichment pipeline for disaster evaluation.

Gathers contextual data (traffic, weather) before evaluation so the strategy
has richer inputs without making blocking calls.

Phase 2: Replace MockWeatherProvider with a real implementation — no other
changes needed. The EnrichmentPipeline interface stays identical.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Tuple

import aiohttp

from app.providers.traffic import TrafficProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Weather provider abstraction
# ---------------------------------------------------------------------------


@dataclass
class WeatherContext:
    """Normalised weather data returned by any weather provider."""
    temperature_c: float
    condition: str           # e.g. "clear", "rain", "storm"
    wind_speed_kmh: float
    source: str              # "mock" | "openweathermap" | etc.


class BaseWeatherProvider(ABC):
    """
    Abstract weather provider.

    Phase 2: implement this with a real API and inject it into
    EnrichmentPipeline — no other code changes required.
    """

    @abstractmethod
    async def get_weather_at_point(
        self, lat: float, lon: float
    ) -> WeatherContext:
        """Fetch weather data for a geographic point."""
        ...


class MockWeatherProvider(BaseWeatherProvider):
    """
    Static mock weather provider for Phase 1.

    Returns deterministic data so unit tests are hermetic and the
    evaluation service works end-to-end without a live weather API.
    """

    async def get_weather_at_point(
        self, lat: float, lon: float
    ) -> WeatherContext:
        return WeatherContext(
            temperature_c=15.0,
            condition="clear",
            wind_speed_kmh=10.0,
            source="mock",
        )


# ---------------------------------------------------------------------------
# Enrichment pipeline
# ---------------------------------------------------------------------------


class EnrichmentPipeline:
    """
    Fetches traffic and weather data in parallel before evaluation.

    If either provider raises, that component returns None — the evaluation
    continues with reduced information rather than failing entirely.
    """

    def __init__(
        self,
        traffic_provider: TrafficProvider,
        weather_provider: BaseWeatherProvider,
    ) -> None:
        self._traffic = traffic_provider
        self._weather = weather_provider

    async def enrich(
        self, lat: float, lon: float
    ) -> Tuple[Optional[dict], Optional[dict]]:
        """
        Fetch traffic and weather data for the given coordinates.

        Returns:
            (traffic_context, weather_context) — either may be None on failure.
        """
        traffic_task = self._fetch_traffic(lat, lon)
        weather_task = self._fetch_weather(lat, lon)

        traffic_result, weather_result = await asyncio.gather(
            traffic_task, weather_task, return_exceptions=True
        )

        traffic_ctx = traffic_result if not isinstance(traffic_result, Exception) else None
        weather_ctx = weather_result if not isinstance(weather_result, Exception) else None

        if isinstance(traffic_result, Exception):
            logger.warning("Traffic enrichment failed: %s", traffic_result)
        if isinstance(weather_result, Exception):
            logger.warning("Weather enrichment failed: %s", weather_result)

        return traffic_ctx, weather_ctx

    async def _fetch_traffic(self, lat: float, lon: float) -> dict:
        """Fetch traffic flow data for a single point."""
        session = await self._traffic.get_session()
        segments = await self._traffic.fetch_flow_at_point(session, lat, lon)
        return {"flow": segments, "source": "tomtom"}

    async def _fetch_weather(self, lat: float, lon: float) -> dict:
        """Fetch weather data and serialise to a plain dict."""
        ctx: WeatherContext = await self._weather.get_weather_at_point(lat, lon)
        return {
            "temperature_c": ctx.temperature_c,
            "condition": ctx.condition,
            "wind_speed_kmh": ctx.wind_speed_kmh,
            "source": ctx.source,
        }
