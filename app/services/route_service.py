"""
app/services/route_service.py

UC6 Deploy Services — Route Calculation.

Uses the existing IntegrationService (TomTom wrapper built by the reroute team).
Falls back to a haversine road-factor estimate when TomTom is unavailable.

IntegrationService already has:
  - Circuit breaker (pybreaker)
  - Retry with exponential backoff (tenacity)
  - Redis caching (60 s TTL)
  - Mock/degraded mode (returns stub data when API key missing)

So we never call TomTom directly — we always go through IntegrationService.
"""

import math
import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.integration_service import IntegrationService, get_integration_service

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Haversine helper
# ─────────────────────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line great-circle distance in km between two WGS-84 points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ─────────────────────────────────────────────────────────────────────────────
# RouteService
# ─────────────────────────────────────────────────────────────────────────────

class RouteService:
    """
    Calculates driving routes for deployed emergency units.

    Constructor accepts an optional IntegrationService so tests can inject a
    mock without patching module globals.
    """

    # Road-factor applied to straight-line haversine distance
    _ROAD_FACTOR = 1.4
    # Default speed when no GPS speed is available
    _DEFAULT_SPEED_KMH = 40.0

    def __init__(
        self,
        db: AsyncSession,
        integration: Optional[IntegrationService] = None,
    ) -> None:
        self.db          = db
        self.integration = integration or get_integration_service()

    # ─────────────────────────────────────────────────────────
    # Route for a specific deployment
    # ─────────────────────────────────────────────────────────

    async def get_deployment_route(self, deployment_id: str) -> Dict[str, Any]:
        """
        Calculate driving route for a deployed unit.

        Origin:      current GPS position (if available) OR unit's station
        Destination: disaster location

        Sequence diagram: calculateRoute(unitLocation, disasterLocation)
                          → getDirections(origin, destination, optimize=true)
                          ← routeData (distance, duration, polyline)
        """
        # ── Fetch all needed data in one JOIN ───────────────────────────────
        result = await self.db.execute(text("""
            SELECT
                dep.id                  AS deployment_id,
                dep.deployment_status,
                dep.current_latitude,
                dep.current_longitude,
                eu.unit_code,
                eu.unit_name,
                ST_Y(eu.station_location::geometry) AS station_lat,
                ST_X(eu.station_location::geometry) AS station_lon,
                ST_Y(dis.location::geometry)         AS dis_lat,
                ST_X(dis.location::geometry)         AS dis_lon,
                dis.location_address
            FROM deployments     dep
            JOIN emergency_units eu  ON dep.unit_id    = eu.id
            JOIN disasters       dis ON dep.disaster_id = dis.id
            WHERE dep.id = :did AND dep.deleted_at IS NULL
        """), {"did": deployment_id})
        row = result.mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="Deployment not found.")

        # ── Determine origin ────────────────────────────────────────────────
        # Prefer live GPS (unit already moving) over station
        if row["current_latitude"] is not None:
            origin_lat   = float(row["current_latitude"])
            origin_lon   = float(row["current_longitude"])
            origin_label = "current GPS position"
        elif row["station_lat"] is not None:
            origin_lat   = float(row["station_lat"])
            origin_lon   = float(row["station_lon"])
            origin_label = "station"
        else:
            raise HTTPException(
                status_code=422,
                detail="Unit has no station or GPS location configured.",
            )

        dis_lat = float(row["dis_lat"])
        dis_lon = float(row["dis_lon"])

        route_data = await self._calculate_route(origin_lat, origin_lon, dis_lat, dis_lon)

        return {
            "deployment_id":    deployment_id,
            "unit_code":        str(row["unit_code"]),
            "unit_name":        str(row["unit_name"]),
            "deployment_status": str(row["deployment_status"]),
            "origin": {
                "lat":   origin_lat,
                "lon":   origin_lon,
                "label": origin_label,
            },
            "destination": {
                "lat":   dis_lat,
                "lon":   dis_lon,
                "label": row["location_address"],
            },
            **route_data,
        }

    # ─────────────────────────────────────────────────────────
    # General A → B route
    # ─────────────────────────────────────────────────────────

    async def calculate_route(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
    ) -> Dict[str, Any]:
        """
        General-purpose point-to-point route calculation.
        Useful for the admin calculating custom routes on the map.
        """
        return await self._calculate_route(origin_lat, origin_lon, dest_lat, dest_lon)

    # ─────────────────────────────────────────────────────────
    # Internal: TomTom → haversine fallback
    # ─────────────────────────────────────────────────────────

    async def _calculate_route(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
    ) -> Dict[str, Any]:
        """
        Try TomTom first.  On any failure (timeout, circuit open, empty response),
        fall back to the haversine estimate.  Always returns the same dict shape
        so callers never have to branch on source.

        Return shape:
          source          : "tomtom" | "estimate"
          distance_km     : float
          duration_minutes: float
          polyline        : list of [lat, lng] pairs (empty for estimate)
          geojson         : GeoJSON LineString or None
          note            : str (estimate only, explains the fallback)
        """
        try:
            # IntegrationService.get_directions() expects {"lat": ..., "lng": ...}
            # Note: it's "lng" not "lon" — matching the reroute service convention
            result = await self.integration.get_directions(
                origin={"lat": origin_lat, "lng": origin_lon},
                destination={"lat": dest_lat, "lng": dest_lon},
                alternatives=False,
            )
            routes: List[Dict] = result.get("routes", [])

            if routes:
                r = routes[0]
                return {
                    "source":           "tomtom",
                    "distance_km":      round(r.get("length_meters", 0) / 1000, 2),
                    "duration_minutes": round(r.get("travel_time_seconds", 0) / 60, 1),
                    "polyline":         r.get("points", []),
                    "geojson":          r.get("geojson"),
                }

            # TomTom returned an empty route list — fall through to estimate
            logger.warning("RouteService: TomTom returned no routes, using estimate")

        except Exception as exc:
            logger.warning(f"RouteService: TomTom call failed ({exc}), using haversine estimate")

        return self._haversine_fallback(origin_lat, origin_lon, dest_lat, dest_lon)

    def _haversine_fallback(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
    ) -> Dict[str, Any]:
        """
        Road-factor haversine estimate.
        straight-line × 1.4  at 40 km/h → duration in minutes.
        No polyline — frontend can draw a straight dashed line instead.
        """
        straight_km  = _haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
        road_km      = round(straight_km * self._ROAD_FACTOR, 2)
        duration_min = round(road_km / self._DEFAULT_SPEED_KMH * 60, 1)

        return {
            "source":           "estimate",
            "distance_km":      road_km,
            "duration_minutes": duration_min,
            "polyline":         [],
            "geojson":          None,
            "note":             (
                "Estimated route (TomTom unavailable). "
                "Straight-line distance × 1.4 road factor at 40 km/h."
            ),
        }