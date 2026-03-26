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

import aiohttp, math

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

def _dist_km(lat1, lng1, lat2, lng2):
    return math.sqrt(((lat1-lat2)*111)**2 + ((lng1-lng2)*73)**2)

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
        tomtom_api_key: Optional[str] = None,
    ) -> None:
        self._live_map = live_map_service
        self._weather = weather_provider
        self._surveillance = surveillance_provider
        self._population = population_provider
        self._infrastructure = infrastructure_provider or InfrastructureProvider()
        self._image_analysis = image_analysis_provider or CLIPImageAnalysisProvider()
        # TomTom API key for reverse geocoding — falls back to settings
        if tomtom_api_key:
            self._tomtom_api_key = tomtom_api_key
        else:
            from app.core.config import settings
            self._tomtom_api_key = settings.TRAFFIC_API_KEY

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

    async def _fetch_traffic_at_point(
        self,
        lat: float,
        lon: float,
    ) -> Optional[dict]:
        """Query TomTom flow directly at the disaster point and 4 nearby offsets."""
        import aiohttp

        api_key = self._tomtom_api_key
        if not api_key:
            return None

        offset = 0.002  # ~200m
        sample_points = [
            (lat,          lon),
            (lat + offset, lon),
            (lat - offset, lon),
            (lat,          lon + offset),
            (lat,          lon - offset),
        ]

        url = "https://api.tomtom.com/traffic/services/4/flowSegmentData/relative/14/json"

        async with aiohttp.ClientSession() as session:
            tasks = [
                self._fetch_flow_at_exact_point(session, url, api_key, p_lat, p_lon)
                for p_lat, p_lon in sample_points
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_segments = []
        seen = set()
        for result in results:
            if isinstance(result, Exception) or not result:
                continue
            for seg in result:
                key = str(seg.get("coordinates", ""))
                if key not in seen:
                    seen.add(key)
                    all_segments.append(seg)

        return {"flow": all_segments, "source": "tomtom_direct"} if all_segments else None


    async def _fetch_flow_at_exact_point(
        self,
        session: aiohttp.ClientSession,
        url: str,
        api_key: str,
        lat: float,
        lon: float,
    ) -> list:
        """Single TomTom flow segment call for one coordinate point."""
        try:
            async with session.get(
                url,
                params={"key": api_key, "point": f"{lat},{lon}", "unit": "KMPH"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                flow_data = data.get("flowSegmentData", {})
                coords_raw = flow_data.get("coordinates", {}).get("coordinate", [])
                if not coords_raw:
                    return []
                coords = [[c["latitude"], c["longitude"]] for c in coords_raw]
                return [{
                    "coordinates": coords,
                    "road_name": flow_data.get("roadName", ""),
                    "current_speed": flow_data.get("currentSpeed", 0),
                    "free_flow_speed": flow_data.get("freeFlowSpeed", 0),
                }]
        except Exception as exc:
            logger.debug(f"_fetch_flow_at_exact_point failed ({lat},{lon}): {exc}")
            return []

    async def identify_affected_roads_async(
        self,
        traffic_context: Optional[dict],
        lat: float,
        lon: float,
    ) -> List[dict]:
        """
        Get real road names + coordinates via TomTom Reverse Geocoding API.

        Returns a list of dicts with road name AND the actual start/end
        coordinates of each affected segment, so road_segments table gets
        correct per-road coordinates rather than hardcoded values.

        Each dict:
            {
                "road_name": "M50",
                "start_lat": 53.302,
                "start_lng": -6.361,
                "end_lat":   53.318,
                "end_lng":   -6.349,
            }

        Falls back to coordinate-only descriptors if geocoding fails.
        Returns at most 5 unique roads.
        """
        
        # Query disaster point directly for immediate roads
        direct_traffic = await self._fetch_traffic_at_point(lat, lon)
        flow = (direct_traffic or {}).get("flow", [])
        
        if not flow:
            flow = (traffic_context or {}).get("flow", [])

        if not flow:
            return [{"road_name": f"Road network near ({lat:.4f}, {lon:.4f})",
                    "start_lat": lat, "start_lng": lon,
                    "end_lat": lat, "end_lng": lon}]
        
        nearby_flow = [
            seg for seg in flow
            if seg.get("coordinates") and
            _dist_km(seg["coordinates"][0][0], seg["coordinates"][0][1], lat, lon) <= 2.0
        ]

        # Collect unique sample coords from flow segments (max 5)
        # Keep the full segment so we have start + end coords
        sample_segments = []
        seen_coords: set = set()
        for nearby_flow in flow:
            coords = nearby_flow.get("coordinates", [])
            if not coords:
                continue
            key = (round(coords[0][0], 3), round(coords[0][1], 3))
            if key in seen_coords:
                continue
            seen_coords.add(key)
            if len(coords) >= 2:
                # Normal segment — use actual start/end
                sample_segments.append({
                    "start": coords[0],
                    "end":   coords[-1],
                    "mid":   coords[len(coords) // 2],
                })
            else:
                # Single-point segment — create a small offset so
                # start != end and TomTom routing call is valid
                mid = coords[0]
                offset = 0.001  # ~100m
                sample_segments.append({
                    "start": [mid[0] - offset, mid[1]],
                    "end":   [mid[0] + offset, mid[1]],
                    "mid":   mid,
                })
            if len(sample_segments) >= 5:
                break

        if not sample_segments:
            return [{"road_name": f"Road network near ({lat:.4f}, {lon:.4f})",
                     "start_lat": lat, "start_lng": lon,
                     "end_lat": lat, "end_lng": lon}]

        # Reverse geocode midpoints in parallel to get road names
        tasks = [
            self._reverse_geocode_point(seg["mid"][0], seg["mid"][1])
            for seg in sample_segments
        ]
        names = await asyncio.gather(*tasks, return_exceptions=True)

        seen_names: set[str] = set()
        road_list: List[dict] = []

        for seg, name in zip(sample_segments, names):
            if isinstance(name, Exception) or not name:
                name = f"Road near ({seg['mid'][0]:.3f}, {seg['mid'][1]:.3f})"

            if name not in seen_names:
                seen_names.add(name)
                road_list.append({
                    "road_name": name,
                    "start_lat": seg["start"][0],
                    "start_lng": seg["start"][1],
                    "end_lat":   seg["end"][0],
                    "end_lng":   seg["end"][1],
                })

        return road_list if road_list else [
            {"road_name": f"Road network near ({lat:.4f}, {lon:.4f})",
             "start_lat": lat, "start_lng": lon,
             "end_lat": lat, "end_lng": lon}
        ]

    def identify_affected_roads(
        self,
        traffic_context: Optional[dict],
        lat: float,
        lon: float,
    ) -> List[str]:
        """
        Sync fallback — returns FRC-based descriptors.
        Use identify_affected_roads_async() for real road names.
        """
        if not traffic_context:
            return [f"Road network near ({lat:.4f}, {lon:.4f})"]

        FRC_LABELS = {
            "FRC0": "Motorway", "FRC1": "Major Road", "FRC2": "Major Road",
            "FRC3": "Secondary Road", "FRC4": "Local Road",
            "FRC5": "Local Road", "FRC6": "Local Road",
        }
        flow = traffic_context.get("flow", [])
        seen: set[str] = set()
        roads: List[str] = []
        for segment in flow:
            road_type = segment.get("road_type", "")
            label = FRC_LABELS.get(road_type, "")
            coords = segment.get("coordinates", [])
            if label and coords:
                first = coords[0]
                descriptor = f"{label} near ({first[0]:.3f}, {first[1]:.3f})"
                if descriptor not in seen:
                    seen.add(descriptor)
                    roads.append(descriptor)
        return roads[:5] if roads else [f"Road network near ({lat:.4f}, {lon:.4f})"]

    async def _reverse_geocode_point(self, lat: float, lon: float) -> Optional[str]:
        """
        Call TomTom Reverse Geocoding API to get a road name for a coordinate.

        API: GET https://api.tomtom.com/search/2/reverseGeocode/{lat},{lon}.json
        Returns the street name or road number (e.g. "M50", "N4", "Grafton Street").
        Returns None on failure.
        """
        if not self._tomtom_api_key:
            return None

        url = f"https://api.tomtom.com/search/2/reverseGeocode/{lat},{lon}.json"
        params = {
            "key": self._tomtom_api_key,
            "radius": 100,              # 100m search radius
            "returnSpeedLimit": "false",
            "returnRoadUse": "true",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    if response.status != 200:
                        return None
                    data = await response.json()

            addresses = data.get("addresses", [])
            if not addresses:
                return None

            addr = addresses[0].get("address", {})

            # Priority: street → road number → municipality
            road_name = (
                addr.get("streetName")
                or addr.get("routeNumbers", [None])[0]
                or addr.get("municipality")
            )
            return road_name if road_name else None

        except Exception as e:
            logger.debug(f"Reverse geocode failed for ({lat}, {lon}): {e}")
            return None

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