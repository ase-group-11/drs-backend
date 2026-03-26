"""
PopulationDensityProvider — fetches population data near a coordinate
using the GeoNames REST API (geonames.org).

No payment required. Register for a free username at geonames.org/login.
Set GEONAMES_USERNAME in .env — if empty, MockPopulationProvider is used.

API used: http://api.geonames.org/findNearbyPlaceNameJSON
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

_BASE_URL = "http://api.geonames.org/findNearbyPlaceNameJSON"
_TIMEOUT_S = 8
_SEARCH_RADIUS_KM = 10
_MAX_ROWS = 1


class BasePopulationProvider(ABC):
    @abstractmethod
    async def get_population_near(self, lat: float, lon: float) -> Dict[str, Any]:
        """Return population context for the given coordinates."""
        ...


class MockPopulationProvider(BasePopulationProvider):
    """
    Static mock used when GEONAMES_USERNAME is not configured.

    Returns deterministic data so the evaluation pipeline works
    end-to-end without a live GeoNames account.
    """

    async def get_population_near(self, lat: float, lon: float) -> Dict[str, Any]:
        return {
            "nearest_place": "Unknown",
            "population": 0,
            "distance_km": 0.0,
            "source": "mock",
        }


class GeoNamesPopulationProvider(BasePopulationProvider):
    """
    Live population provider using the GeoNames findNearbyPlaceName API.

    Returns population of the nearest populated place within the search
    radius — a proxy for how urbanised the disaster area is.

    Requires a free GeoNames username (register at geonames.org/login).
    Falls back gracefully — EnrichmentPipeline catches exceptions and
    continues with population_context=None.
    """

    def __init__(self, username: str) -> None:
        self._username = username

    async def get_population_near(self, lat: float, lon: float) -> Dict[str, Any]:
        params = {
            "lat": lat,
            "lng": lon,
            "radius": _SEARCH_RADIUS_KM,
            "maxRows": _MAX_ROWS,
            "username": self._username,
            "style": "SHORT",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                _BASE_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT_S),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        places = data.get("geonames", [])
        if not places:
            return {
                "nearest_place": "Unknown",
                "population": 0,
                "distance_km": 0.0,
                "source": "geonames",
            }

        place = places[0]
        return {
            "nearest_place": place.get("name", "Unknown"),
            "population": int(place.get("population", 0)),
            "distance_km": round(float(place.get("distance", 0.0)), 2),
            "source": "geonames",
        }
