"""
app/providers/tomtom_parser.py

Pure parsing functions for all TomTom API responses.

Two consumers:
  - TrafficProvider (live map) → parse_flow_for_display()
  - IntegrationService (reroute) → parse_flow_for_reroute(), parse_routing_response(),
                                    build_avoidance_params(), extract_geojson()

No HTTP calls, no side effects — all functions are pure transformations.
"""

import uuid
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flow API — Live Map
# ---------------------------------------------------------------------------

def parse_flow_for_display(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parse a TomTom Flow Segment Data API response for live map display.

    Produces the same structure the existing TrafficProvider contract expects:
      - segment_id
      - current_speed
      - free_flow_speed
      - confidence
      - congestion_level  (light | moderate | heavy | severe | unknown)
      - coordinates       [[lat, lng], ...]
      - road_name
      - road_type

    Args:
        data: Raw JSON response from TomTom Flow Segment Data API

    Returns:
        List of normalised segment dicts (usually 1 per API call)
    """
    segments = []
    flow_segment = data.get("flowSegmentData")
    if not flow_segment:
        return segments

    current_speed = flow_segment.get("currentSpeed", 0)
    free_flow_speed = flow_segment.get("freeFlowSpeed", 0)
    confidence = flow_segment.get("confidence", 0.0)
    congestion_level = _speed_ratio_to_congestion_level(current_speed, free_flow_speed)
    coord_list = _extract_coord_list(flow_segment)

    segments.append({
        "segment_id": f"seg_{hash(str(coord_list)) % 1_000_000}",
        "current_speed": current_speed,
        "free_flow_speed": free_flow_speed,
        "confidence": confidence,
        "congestion_level": congestion_level,
        "coordinates": coord_list,
        "road_name": flow_segment.get("roadName", "Unknown Road"),
        "road_type": flow_segment.get("frc", ""),
    })

    return segments


# ---------------------------------------------------------------------------
# Flow API — Reroute
# ---------------------------------------------------------------------------

def parse_flow_for_reroute(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parse a TomTom Flow Segment Data API response for reroute scoring.

    Adds congestion_ratio (float 0.0–1.0) on top of the display fields.
    congestion_ratio = 1 - (current_speed / free_flow_speed)
      → 0.0 = free flow, 1.0 = fully congested

    Used by traffic_distribution.py to score routes via Innovation 1.

    Args:
        data: Raw JSON response from TomTom Flow Segment Data API

    Returns:
        List of segment dicts with congestion_ratio field added
    """
    segments = parse_flow_for_display(data)
    flow_segment = data.get("flowSegmentData", {})
    current_speed = flow_segment.get("currentSpeed", 0)
    free_flow_speed = flow_segment.get("freeFlowSpeed", 0)
    current_travel_time = flow_segment.get("currentTravelTime", 0)
    free_flow_travel_time = flow_segment.get("freeFlowTravelTime", 0)

    congestion_ratio = _compute_congestion_ratio(current_speed, free_flow_speed)

    for seg in segments:
        seg["congestion_ratio"] = congestion_ratio
        seg["current_travel_time"] = current_travel_time
        seg["free_flow_travel_time"] = free_flow_travel_time

    return segments


# ---------------------------------------------------------------------------
# Routing API — Alternative Routes
# ---------------------------------------------------------------------------

def parse_routing_response(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parse a TomTom Routing API response into alternative route objects.

    Each route gets a stable unique route_id (UUID) so the distribution
    algorithm and the DB can reference them consistently.

    Input shape (TomTom Routing API v1):
        {
            "routes": [
                {
                    "summary": {
                        "lengthInMeters": 12000,
                        "travelTimeInSeconds": 900,
                        "trafficDelayInSeconds": 120,
                        ...
                    },
                    "legs": [{"points": [{"latitude": ..., "longitude": ...}]}],
                    "guidance": {"instructions": [...]}
                }
            ]
        }

    Returns:
        List of route dicts:
            - route_id:               str (UUID)
            - travel_time_seconds:    int
            - length_meters:          int
            - traffic_delay_seconds:  int
            - points:                 [[lat, lng], ...]  (full geometry)
            - geojson:                GeoJSON LineString feature
            - instructions:           list of turn-by-turn instructions
    """
    routes = data.get("routes", [])
    parsed = []

    for route in routes:
        summary = route.get("summary", {})
        legs = route.get("legs", [])
        guidance = route.get("guidance", {})

        points = _extract_route_points(legs)
        geojson = _points_to_geojson_feature(points)

        parsed.append({
            "route_id": str(uuid.uuid4()),
            "travel_time_seconds": summary.get("travelTimeInSeconds", 0),
            "length_meters": summary.get("lengthInMeters", 0),
            "traffic_delay_seconds": summary.get("trafficDelayInSeconds", 0),
            "departure_time": summary.get("departureTime"),
            "arrival_time": summary.get("arrivalTime"),
            "points": points,
            "geojson": geojson,
            "instructions": guidance.get("instructions", []),
        })

    logger.debug(f"parse_routing_response: parsed {len(parsed)} alternative routes")
    return parsed


# ---------------------------------------------------------------------------
# Avoidance constraints
# ---------------------------------------------------------------------------

def build_avoidance_params(blocked_roads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert blocked road segments into TomTom Routing API avoidance constraints.

    TomTom supports avoidAreaRectangle — we build a tight bounding box around
    each blocked segment using its start/end coordinates.

    Args:
        blocked_roads: List of segment dicts with start_lat, start_lng, end_lat, end_lng

    Returns:
        List of avoidAreaRectangle dicts ready to pass to the Routing API:
            [
                {
                    "avoidAreaRectangle": {
                        "southWestCorner": {"latitude": ..., "longitude": ...},
                        "northEastCorner": {"latitude": ..., "longitude": ...}
                    }
                }
            ]
    """
    avoid_params = []

    for road in blocked_roads:
        start_lat = road.get("start_lat")
        start_lng = road.get("start_lng")
        end_lat = road.get("end_lat")
        end_lng = road.get("end_lng")

        if None in (start_lat, start_lng, end_lat, end_lng):
            logger.warning(f"Skipping road with missing coordinates: {road.get('segment_id')}")
            continue

        # Add small padding (0.001° ≈ 100m) to ensure the segment is fully avoided
        padding = 0.001
        south = min(start_lat, end_lat) - padding
        north = max(start_lat, end_lat) + padding
        west = min(start_lng, end_lng) - padding
        east = max(start_lng, end_lng) + padding

        avoid_params.append({
            "avoidAreaRectangle": {
                "southWestCorner": {"latitude": south, "longitude": west},
                "northEastCorner": {"latitude": north, "longitude": east},
            }
        })

    logger.debug(f"build_avoidance_params: built {len(avoid_params)} avoidance constraints")
    return avoid_params


# ---------------------------------------------------------------------------
# GeoJSON extraction
# ---------------------------------------------------------------------------

def extract_geojson(routes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convert a list of parsed routes into a GeoJSON FeatureCollection.

    Used by MappingService to send route overlays to Mapbox GL JS
    via Socket.IO.

    Args:
        routes: List of parsed route dicts (output of parse_routing_response)

    Returns:
        GeoJSON FeatureCollection with one LineString Feature per route
    """
    features = []

    for i, route in enumerate(routes):
        feature = route.get("geojson")
        if not feature:
            points = route.get("points", [])
            feature = _points_to_geojson_feature(points)

        # Attach route metadata as properties
        feature["properties"] = {
            "route_id": route.get("route_id"),
            "route_index": i,
            "travel_time_seconds": route.get("travel_time_seconds"),
            "length_meters": route.get("length_meters"),
            "traffic_delay_seconds": route.get("traffic_delay_seconds"),
        }
        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _speed_ratio_to_congestion_level(current_speed: float, free_flow_speed: float) -> str:
    """Convert speed ratio to a human-readable congestion level."""
    if free_flow_speed <= 0:
        return "unknown"
    ratio = current_speed / free_flow_speed
    if ratio >= 0.8:
        return "light"
    elif ratio >= 0.5:
        return "moderate"
    elif ratio >= 0.3:
        return "heavy"
    return "severe"


def _compute_congestion_ratio(current_speed: float, free_flow_speed: float) -> float:
    """
    Compute congestion ratio as a float between 0.0 and 1.0.
    0.0 = free flow, 1.0 = fully congested.
    """
    if free_flow_speed <= 0:
        return 0.0
    ratio = 1.0 - (current_speed / free_flow_speed)
    return max(0.0, min(1.0, ratio))


def _extract_coord_list(flow_segment: Dict[str, Any]) -> List[List[float]]:
    """
    Extract [[lat, lng], ...] from TomTom flow segment coordinates.

    TomTom nests coordinates under flowSegmentData.coordinates.coordinate
    with {latitude, longitude} objects — note the coordinate system is
    already WGS84 so no flip needed here (lat first).
    """
    coordinates = flow_segment.get("coordinates", {})
    coord_items = coordinates.get("coordinate", [])
    return [
        [c.get("latitude"), c.get("longitude")]
        for c in coord_items
        if c.get("latitude") is not None and c.get("longitude") is not None
    ]


def _extract_route_points(legs: List[Dict[str, Any]]) -> List[List[float]]:
    """
    Extract all points from route legs as [[lat, lng], ...].

    TomTom Routing API uses {latitude, longitude} per point inside legs[].points.
    GeoJSON convention is [lng, lat] — we keep [lat, lng] internally and
    flip only when building GeoJSON features.
    """
    points = []
    for leg in legs:
        for point in leg.get("points", []):
            lat = point.get("latitude")
            lng = point.get("longitude")
            if lat is not None and lng is not None:
                points.append([lat, lng])
    return points


def _points_to_geojson_feature(points: List[List[float]]) -> Dict[str, Any]:
    """
    Convert [[lat, lng], ...] to a GeoJSON LineString Feature.

    GeoJSON requires [lng, lat] coordinate order — we flip here.
    """
    # Flip to [lng, lat] for GeoJSON
    geojson_coords = [[lng, lat] for lat, lng in points]

    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": geojson_coords,
        },
        "properties": {},
    }