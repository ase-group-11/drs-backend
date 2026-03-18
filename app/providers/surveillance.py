"""
SurveillanceProvider — fetches nearby CCTV/surveillance camera data
from the OpenStreetMap Overpass API.

No API key required. Queries OSM nodes tagged man_made=surveillance
within a configurable radius of a geographic point.

Used by the EnrichmentPipeline to satisfy the spec requirement:
"External data (traffic, weather, surveillance, population density)"
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_DEFAULT_RADIUS_M = 500
_TIMEOUT_S = 10


class SurveillanceProvider:
    """
    Queries the OpenStreetMap Overpass API for surveillance camera nodes
    near a given coordinate.

    Returns a normalised dict describing nearby cameras so the evaluation
    pipeline can factor surveillance coverage into its assessment.
    """

    def __init__(
        self,
        radius_m: int = _DEFAULT_RADIUS_M,
        timeout_s: int = _TIMEOUT_S,
    ) -> None:
        self._radius_m = radius_m
        self._timeout_s = timeout_s

    async def get_cameras_near(
        self, lat: float, lon: float
    ) -> Dict[str, Any]:
        """
        Return surveillance camera data within radius of the given point.

        Args:
            lat: Latitude of the disaster location
            lon: Longitude of the disaster location

        Returns:
            Dict with keys:
                camera_count  — number of cameras found
                cameras       — list of camera detail dicts
                radius_m      — search radius used
                source        — "openstreetmap"

        Raises:
            aiohttp.ClientError on network failure (caller catches this).
        """
        query = (
            f"[out:json][timeout:{self._timeout_s}];"
            f'node["man_made"="surveillance"]'
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

        cameras = self._parse(data)
        return {
            "camera_count": len(cameras),
            "cameras": cameras,
            "radius_m": self._radius_m,
            "source": "openstreetmap",
        }

    @staticmethod
    def _parse(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normalise raw Overpass elements into a flat camera list."""
        cameras = []
        for element in data.get("elements", []):
            tags = element.get("tags", {})
            cameras.append({
                "id": element.get("id"),
                "lat": element.get("lat"),
                "lon": element.get("lon"),
                "type": tags.get("surveillance:type", tags.get("surveillance", "unknown")),
                "operator": tags.get("operator"),
                "camera_type": tags.get("camera:type"),
                "mount": tags.get("camera:mount"),
            })
        return cameras
