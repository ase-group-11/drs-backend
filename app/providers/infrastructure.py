"""
InfrastructureProvider — fetches nearby critical facilities
(hospitals, fire stations, schools, police stations) from the
OpenStreetMap Overpass API.

Same pattern as SurveillanceProvider. No API key required.
Used by the EnrichmentPipeline to satisfy the spec requirement:
"Identify affected infrastructure including roads, buildings,
and critical facilities."
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import aiohttp

logger = logging.getLogger(__name__)

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_DEFAULT_RADIUS_M = 1000
_TIMEOUT_S = 10

# OSM amenity values we consider critical infrastructure
_FACILITY_TYPES = "hospital|fire_station|school|police"


class InfrastructureProvider:
    """
    Queries the OpenStreetMap Overpass API for critical facility nodes
    near a given coordinate.

    Returns a normalised dict so the evaluation pipeline and map layer
    can identify which facilities fall within a disaster's impact zone.
    """

    def __init__(
        self,
        radius_m: int = _DEFAULT_RADIUS_M,
        timeout_s: int = _TIMEOUT_S,
    ) -> None:
        self._radius_m = radius_m
        self._timeout_s = timeout_s

    async def get_facilities_near(
        self, lat: float, lon: float
    ) -> Dict[str, Any]:
        """
        Return critical facilities within radius of the given point.

        Args:
            lat: Latitude of the disaster location
            lon: Longitude of the disaster location

        Returns:
            Dict with keys:
                facilities  — list of facility detail dicts
                count       — number of facilities found
                radius_m    — search radius used
                source      — "openstreetmap"

        Raises:
            aiohttp.ClientError on network failure (caller catches this).
        """
        query = (
            f"[out:json][timeout:{self._timeout_s}];"
            f'node["amenity"~"^({_FACILITY_TYPES})$"]'
            f"(around:{self._radius_m},{lat},{lon});"
            f"out body;"
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                _OVERPASS_URL,
                data=query,
                timeout=aiohttp.ClientTimeout(total=self._timeout_s),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        facilities = self._parse(data)
        return {
            "facilities": facilities,
            "count": len(facilities),
            "radius_m": self._radius_m,
            "source": "openstreetmap",
        }

    @staticmethod
    def _parse(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normalise raw Overpass elements into a flat facility list."""
        facilities = []
        for element in data.get("elements", []):
            tags = element.get("tags", {})
            facilities.append({
                "id": element.get("id"),
                "lat": element.get("lat"),
                "lon": element.get("lon"),
                "amenity": tags.get("amenity"),
                "name": tags.get("name"),
                "operator": tags.get("operator"),
            })
        return facilities
