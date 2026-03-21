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
from typing import List, Optional, Tuple

import aiohttp

from app.providers.image_analysis import CLIPImageAnalysisProvider
from app.providers.infrastructure import InfrastructureProvider
from app.providers.population_density import BasePopulationProvider
from app.providers.surveillance import SurveillanceProvider
from app.services.live_map_service import LiveMapService

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
    Static mock weather provider for tests and local runs without an API key.

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


class OpenWeatherMapProvider(BaseWeatherProvider):
    """
    Live weather provider using the OpenWeatherMap Current Weather API.

    Requires OPENWEATHER_API_KEY to be set in the environment / .env file.
    Falls back gracefully — if the request fails the EnrichmentPipeline
    catches the exception and continues with weather_context=None.

    API used: https://api.openweathermap.org/data/2.5/weather
    """

    _BASE_URL = "https://api.openweathermap.org/data/2.5/weather"

    # OWM condition codes → normalised condition strings used by the feature builder
    _CONDITION_MAP = {
        range(200, 300): "storm",     # Thunderstorm
        range(300, 400): "rain",      # Drizzle
        range(500, 600): "rain",      # Rain
        range(600, 700): "storm",     # Snow (treat as severe)
        range(700, 800): "cloudy",    # Atmosphere (mist, fog, etc.)
        range(800, 801): "clear",     # Clear sky
        range(801, 900): "cloudy",    # Clouds
    }

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def get_weather_at_point(
        self, lat: float, lon: float
    ) -> WeatherContext:
        params = {
            "lat": lat,
            "lon": lon,
            "appid": self._api_key,
            "units": "metric",  # °C and m/s
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                self._BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        condition_id = data["weather"][0]["id"]
        condition = self._map_condition(condition_id)

        # OWM returns wind speed in m/s — convert to km/h
        wind_ms = data.get("wind", {}).get("speed", 0.0)
        wind_kmh = wind_ms * 3.6

        return WeatherContext(
            temperature_c=data["main"]["temp"],
            condition=condition,
            wind_speed_kmh=round(wind_kmh, 1),
            source="openweathermap",
        )

    def _map_condition(self, code: int) -> str:
        for r, label in self._CONDITION_MAP.items():
            if code in r:
                return label
        return "clear"


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
        live_map_service: LiveMapService,
        weather_provider: BaseWeatherProvider,
        surveillance_provider: SurveillanceProvider,
        population_provider: BasePopulationProvider,
        infrastructure_provider: Optional[InfrastructureProvider] = None,
        image_analysis_provider: Optional[CLIPImageAnalysisProvider] = None,
    ) -> None:
        self._live_map = live_map_service
        self._weather = weather_provider
        self._surveillance = surveillance_provider
        self._population = population_provider
        self._infrastructure = infrastructure_provider or InfrastructureProvider()
        self._image_analysis = image_analysis_provider or CLIPImageAnalysisProvider()

    async def enrich(
        self,
        lat: float,
        lon: float,
        photo_urls: Optional[List[str]] = None,
    ) -> Tuple[Optional[dict], Optional[dict], Optional[dict], Optional[dict], Optional[dict], Optional[dict]]:
        """
        Fetch traffic, weather, surveillance, population, infrastructure,
        and image analysis data in parallel.

        Any failure returns None for that source — evaluation continues
        with reduced information rather than failing entirely.

        Returns:
            (traffic_context, weather_context, surveillance_context,
             population_context, infrastructure_context, image_analysis_context)
            — any element may be None on failure.
        """
        (
            traffic_result,
            weather_result,
            surveillance_result,
            population_result,
            infrastructure_result,
            image_analysis_result,
        ) = await asyncio.gather(
            self._fetch_traffic(lat, lon),
            self._fetch_weather(lat, lon),
            self._fetch_surveillance(lat, lon),
            self._fetch_population(lat, lon),
            self._fetch_infrastructure(lat, lon),
            self._fetch_image_analysis(photo_urls or []),
            return_exceptions=True,
        )

        traffic_ctx = traffic_result if not isinstance(traffic_result, Exception) else None
        weather_ctx = weather_result if not isinstance(weather_result, Exception) else None
        surveillance_ctx = surveillance_result if not isinstance(surveillance_result, Exception) else None
        population_ctx = population_result if not isinstance(population_result, Exception) else None
        infrastructure_ctx = infrastructure_result if not isinstance(infrastructure_result, Exception) else None
        image_analysis_ctx = image_analysis_result if not isinstance(image_analysis_result, Exception) else None

        if isinstance(traffic_result, Exception):
            logger.warning("Traffic enrichment failed: %s", traffic_result)
        if isinstance(weather_result, Exception):
            logger.warning("Weather enrichment failed: %s", weather_result)
        if isinstance(surveillance_result, Exception):
            logger.warning("Surveillance enrichment failed: %s", surveillance_result)
        if isinstance(population_result, Exception):
            logger.warning("Population enrichment failed: %s", population_result)
        if isinstance(infrastructure_result, Exception):
            logger.warning("Infrastructure enrichment failed: %s", infrastructure_result)
        if isinstance(image_analysis_result, Exception):
            logger.warning("Image analysis enrichment failed: %s", image_analysis_result)

        return traffic_ctx, weather_ctx, surveillance_ctx, population_ctx, infrastructure_ctx, image_analysis_ctx

    async def _fetch_traffic(self, lat: float, lon: float) -> Optional[dict]:
        """Fetch traffic flow data for a point via LiveMapService (zoom=12 bounds)."""
        delta_lat = 180 / (2 ** 12)  # ~0.044 degrees
        delta_lon = 360 / (2 ** 12)  # ~0.088 degrees
        bounds = f"{lat - delta_lat},{lon - delta_lon},{lat + delta_lat},{lon + delta_lon}"
        return await self._live_map.get_traffic(bounds=bounds)

    def identify_affected_roads(
        self,
        traffic_context: Optional[dict],
        lat: float,
        lon: float,
    ) -> List[str]:
        """
        Extract affected road names from already-fetched traffic context.

        No extra HTTP call — uses the traffic data gathered during enrichment.
        Falls back to a location descriptor when traffic data is unavailable.

        Args:
            traffic_context: Dict from _fetch_traffic (may be None)
            lat: Disaster latitude (used in fallback descriptor)
            lon: Disaster longitude (used in fallback descriptor)

        Returns:
            Deduplicated list of affected road name strings (non-empty).
        """
        if not traffic_context:
            return [f"area near ({lat:.4f}, {lon:.4f})"]

        flow = traffic_context.get("flow", [])
        seen: set[str] = set()
        roads: List[str] = []
        for segment in flow:
            road_name = (
                segment.get("road_name")
                or segment.get("street")
                or segment.get("id")
            )
            if road_name and str(road_name) not in seen:
                seen.add(str(road_name))
                roads.append(str(road_name))

        return roads if roads else [f"area near ({lat:.4f}, {lon:.4f})"]

    async def _fetch_weather(self, lat: float, lon: float) -> dict:
        """Fetch weather data and serialise to a plain dict."""
        ctx: WeatherContext = await self._weather.get_weather_at_point(lat, lon)
        return {
            "temperature_c": ctx.temperature_c,
            "condition": ctx.condition,
            "wind_speed_kmh": ctx.wind_speed_kmh,
            "source": ctx.source,
        }

    async def _fetch_surveillance(self, lat: float, lon: float) -> dict:
        """Fetch nearby surveillance camera data from OpenStreetMap Overpass API."""
        return await self._surveillance.get_cameras_near(lat, lon)

    async def _fetch_population(self, lat: float, lon: float) -> dict:
        """Fetch population data for the nearest populated place via GeoNames."""
        return await self._population.get_population_near(lat, lon)

    async def _fetch_infrastructure(self, lat: float, lon: float) -> dict:
        """Fetch nearby critical facilities (hospitals, fire stations, schools, police) from OSM."""
        return await self._infrastructure.get_facilities_near(lat, lon)

    async def _fetch_image_analysis(self, photo_urls: List[str]) -> Optional[dict]:
        """Run CLIP zero-shot disaster classification on report photos."""
        if not photo_urls:
            return None
        return await self._image_analysis.analyse_photos(photo_urls)
