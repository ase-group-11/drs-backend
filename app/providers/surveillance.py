"""
SurveillanceProvider — fetches nearby CCTV/surveillance camera data
from the OpenStreetMap Overpass API.

No API key required. Queries OSM nodes tagged man_made=surveillance
within a configurable radius of a geographic point.

Beyond a raw camera count, this provider scores the **verification
potential** of the surveillance coverage — how useful the cameras are
for ERT to confirm and assess the disaster.  Scoring factors:

  - Indoor vs outdoor: indoor cameras are irrelevant for outdoor disasters
  - Camera type: dome/panning cameras see more than fixed cameras
  - Surveillance type: CCTV cameras beat ALPR plate readers and guards
  - Operator: public-authority cameras (Garda, Luas, council) are more
    accessible to emergency services than private ones
  - Zone: a camera watching "traffic" is more relevant to road incidents
    than one watching a shop entrance

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

# ── Scoring weights for surveillance quality ──────────────────────────

# surveillance:type → how useful for visual verification
_TYPE_SCORE: Dict[str, float] = {
    "camera":  1.0,   # standard CCTV — full video
    "webcam":  0.8,   # public webcam, may have live feed
    "ALPR":    0.2,   # plate reader only — no visual context
    "guard":   0.3,   # human guard, can report but no footage
    "unknown": 0.5,
}

# camera:type → field-of-view quality
_CAMERA_TYPE_SCORE: Dict[str, float] = {
    "dome":    1.0,   # 360° or wide-angle — best coverage
    "panning": 0.9,   # motorised pan/tilt — good coverage
    "fixed":   0.5,   # narrow fixed view
}
_DEFAULT_CAMERA_TYPE_SCORE = 0.5

# surveillance (indoor/outdoor) → relevance for outdoor disasters
_LOCATION_SCORE: Dict[str, float] = {
    "outdoor": 1.0,
    "public":  0.8,   # public-area camera, likely useful
    "indoor":  0.1,   # almost never useful for outdoor disasters
}
_DEFAULT_LOCATION_SCORE = 0.6

# Known public-authority operator keywords — cameras ERT can request access to
_PUBLIC_OPERATOR_KEYWORDS = {
    "garda", "police", "council", "luas", "transdev",
    "transport", "dublin city", "iarnrod", "bus eireann",
    "national transport", "nta",
}


class SurveillanceProvider:
    """
    Queries the OpenStreetMap Overpass API for surveillance camera nodes
    near a given coordinate.

    Returns a normalised dict describing nearby cameras with a
    surveillance_quality_score (0.0–1.0) indicating how useful the
    coverage is for disaster verification.
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
                camera_count             — total cameras found
                outdoor_camera_count     — cameras tagged outdoor/public
                cameras                  — list of camera detail dicts
                surveillance_quality_score — 0.0–1.0 composite quality score
                has_public_authority_cam  — at least one public-operator camera
                radius_m                 — search radius used
                source                   — "openstreetmap"
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
        quality_score, outdoor_count, has_public = _score_cameras(cameras)

        return {
            "camera_count": len(cameras),
            "outdoor_camera_count": outdoor_count,
            "cameras": cameras,
            "surveillance_quality_score": quality_score,
            "has_public_authority_cam": has_public,
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
                "surveillance_type": tags.get("surveillance:type", "unknown"),
                "location": tags.get("surveillance", "unknown"),
                "zone": tags.get("surveillance:zone"),
                "operator": tags.get("operator"),
                "camera_type": tags.get("camera:type"),
                "camera_mount": tags.get("camera:mount"),
            })
        return cameras


def _score_cameras(
    cameras: List[Dict[str, Any]],
) -> tuple[float, int, bool]:
    """
    Compute a composite surveillance quality score from individual cameras.

    Returns:
        (quality_score, outdoor_count, has_public_authority_cam)
        quality_score is 0.0–1.0 — the average per-camera quality,
        weighted so more cameras = higher score (diminishing returns).
    """
    if not cameras:
        return 0.0, 0, False

    outdoor_count = 0
    has_public = False
    per_camera_scores: List[float] = []

    for cam in cameras:
        # Indoor/outdoor
        location = (cam.get("location") or "unknown").lower()
        loc_score = _LOCATION_SCORE.get(location, _DEFAULT_LOCATION_SCORE)
        if location in ("outdoor", "public"):
            outdoor_count += 1

        # Surveillance type (camera vs ALPR vs guard)
        surv_type = (cam.get("surveillance_type") or "unknown").lower()
        type_score = _TYPE_SCORE.get(surv_type, 0.5)

        # Camera hardware type
        cam_type = (cam.get("camera_type") or "").lower()
        cam_score = _CAMERA_TYPE_SCORE.get(cam_type, _DEFAULT_CAMERA_TYPE_SCORE)

        # Operator — public authority bonus
        operator = (cam.get("operator") or "").lower()
        operator_bonus = 0.0
        if operator:
            for keyword in _PUBLIC_OPERATOR_KEYWORDS:
                if keyword in operator:
                    operator_bonus = 0.15
                    has_public = True
                    break

        # Per-camera score: weighted combination
        score = (
            0.35 * loc_score
            + 0.30 * type_score
            + 0.20 * cam_score
            + 0.15 * (1.0 + operator_bonus)
        )
        per_camera_scores.append(min(1.0, score))

    # Average per-camera quality, then scale by coverage density
    # (diminishing returns: 1 camera = 50%, 5+ cameras = ~100%)
    avg_quality = sum(per_camera_scores) / len(per_camera_scores)
    coverage_factor = min(1.0, len(cameras) / 5.0)
    # Blend: 60% quality, 40% coverage
    quality_score = round(0.60 * avg_quality + 0.40 * coverage_factor, 4)

    return quality_score, outdoor_count, has_public
