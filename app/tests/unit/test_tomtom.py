"""
tests/unit/test_tomtom.py

Unit tests — TomTom API response parsing.
Validates that tomtom_parser.py correctly interprets
TomTom Traffic Flow and Routing API responses.
No external I/O. Pure function tests.
"""
import pytest
from app.providers.tomtom_parser import (
    parse_flow_for_display,
    parse_flow_for_reroute,
    parse_routing_response,
    build_avoidance_params,
    extract_geojson,
    _compute_congestion_ratio,
    _speed_ratio_to_congestion_level,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_flow_response():
    return {
        "flowSegmentData": {
            "frc": "FRC0",
            "currentSpeed": 45,
            "freeFlowSpeed": 110,
            "currentTravelTime": 240,
            "freeFlowTravelTime": 120,
            "confidence": 0.9,
            "coordinates": {
                "coordinate": [
                    {"latitude": 53.302, "longitude": -6.361},
                    {"latitude": 53.312, "longitude": -6.358},
                ]
            },
        }
    }


@pytest.fixture
def sample_routing_response():
    def make_route(travel_time, length):
        return {
            "summary": {
                "lengthInMeters": length,
                "travelTimeInSeconds": travel_time,
                "trafficDelayInSeconds": travel_time // 5,
            },
            "legs": [
                {
                    "points": [
                        {"latitude": 53.302 + i * 0.01, "longitude": -6.361 + i * 0.005}
                        for i in range(5)
                    ]
                }
            ],
            "guidance": {"instructions": []},
        }

    return {
        "routes": [
            make_route(900, 12000),
            make_route(1100, 14500),
            make_route(1350, 17000),
        ]
    }


@pytest.fixture
def sample_blocked_roads():
    return [
        {
            "segment_id": "seg-m50-j6-j7",
            "start_lat": 53.302,
            "start_lng": -6.361,
            "end_lat": 53.312,
            "end_lng": -6.358,
        },
        {
            "segment_id": "seg-m50-j7-j8",
            "start_lat": 53.312,
            "start_lng": -6.358,
            "end_lat": 53.325,
            "end_lng": -6.354,
        },
    ]


# ---------------------------------------------------------------------------
# parse_flow_for_display tests
# ---------------------------------------------------------------------------

class TestParseFlowForDisplay:
    """Section 6.1 — TomTom Traffic Flow API response parsing for map display."""

    def test_parses_current_speed(self, sample_flow_response):
        result = parse_flow_for_display(sample_flow_response)
        assert result[0]["current_speed"] == 45

    def test_parses_free_flow_speed(self, sample_flow_response):
        result = parse_flow_for_display(sample_flow_response)
        assert result[0]["free_flow_speed"] == 110

    def test_parses_confidence(self, sample_flow_response):
        result = parse_flow_for_display(sample_flow_response)
        assert result[0]["confidence"] == 0.9

    def test_parses_coordinates_as_lat_lng_pairs(self, sample_flow_response):
        result = parse_flow_for_display(sample_flow_response)
        coords = result[0]["coordinates"]
        assert isinstance(coords, list)
        assert len(coords) == 2
        assert coords[0] == [53.302, -6.361]

    def test_assigns_congestion_level_heavy_for_low_speed_ratio(self, sample_flow_response):
        """45/110 ≈ 0.41 — below 0.5 threshold → heavy."""
        result = parse_flow_for_display(sample_flow_response)
        assert result[0]["congestion_level"] == "heavy"

    def test_congestion_level_light_for_high_speed_ratio(self):
        data = {"flowSegmentData": {
            "currentSpeed": 100, "freeFlowSpeed": 110, "confidence": 1.0,
            "coordinates": {"coordinate": []}
        }}
        result = parse_flow_for_display(data)
        assert result[0]["congestion_level"] == "light"

    def test_congestion_level_severe_for_very_low_speed(self):
        data = {"flowSegmentData": {
            "currentSpeed": 10, "freeFlowSpeed": 110, "confidence": 0.8,
            "coordinates": {"coordinate": []}
        }}
        result = parse_flow_for_display(data)
        assert result[0]["congestion_level"] == "severe"

    def test_returns_empty_list_for_missing_flow_segment(self):
        result = parse_flow_for_display({})
        assert result == []

    def test_handles_zero_free_flow_speed_gracefully(self):
        data = {"flowSegmentData": {
            "currentSpeed": 0, "freeFlowSpeed": 0, "confidence": 0.5,
            "coordinates": {"coordinate": []}
        }}
        result = parse_flow_for_display(data)
        assert result[0]["congestion_level"] == "unknown"

    def test_segment_id_is_deterministic_for_same_coords(self, sample_flow_response):
        r1 = parse_flow_for_display(sample_flow_response)
        r2 = parse_flow_for_display(sample_flow_response)
        assert r1[0]["segment_id"] == r2[0]["segment_id"]


# ---------------------------------------------------------------------------
# parse_flow_for_reroute tests
# ---------------------------------------------------------------------------

class TestParseFlowForReroute:
    """Section 11 — Flow data for Innovation 1 route scoring."""

    def test_includes_congestion_ratio(self, sample_flow_response):
        result = parse_flow_for_reroute(sample_flow_response)
        assert "congestion_ratio" in result[0]

    def test_congestion_ratio_between_zero_and_one(self, sample_flow_response):
        result = parse_flow_for_reroute(sample_flow_response)
        ratio = result[0]["congestion_ratio"]
        assert 0.0 <= ratio <= 1.0

    def test_congestion_ratio_correct_value(self, sample_flow_response):
        """congestion_ratio = 1 - (45/110) ≈ 0.59."""
        result = parse_flow_for_reroute(sample_flow_response)
        expected = round(1 - (45 / 110), 4)
        assert round(result[0]["congestion_ratio"], 4) == expected

    def test_includes_travel_times(self, sample_flow_response):
        result = parse_flow_for_reroute(sample_flow_response)
        assert "current_travel_time" in result[0]
        assert "free_flow_travel_time" in result[0]

    def test_includes_all_display_fields(self, sample_flow_response):
        result = parse_flow_for_reroute(sample_flow_response)
        for field in ["current_speed", "free_flow_speed", "congestion_level", "coordinates"]:
            assert field in result[0]

    def test_congestion_ratio_clamped_when_speed_exceeds_free_flow(self):
        data = {"flowSegmentData": {
            "currentSpeed": 120, "freeFlowSpeed": 100, "confidence": 0.7,
            "currentTravelTime": 50, "freeFlowTravelTime": 60,
            "coordinates": {"coordinate": []}
        }}
        result = parse_flow_for_reroute(data)
        assert result[0]["congestion_ratio"] == 0.0


# ---------------------------------------------------------------------------
# parse_routing_response tests
# ---------------------------------------------------------------------------

class TestParseRoutingResponse:
    """Section 6.1 — TomTom Routing API response parsing."""

    def test_returns_correct_number_of_routes(self, sample_routing_response):
        routes = parse_routing_response(sample_routing_response)
        assert len(routes) == 3

    def test_each_route_has_required_fields(self, sample_routing_response):
        routes = parse_routing_response(sample_routing_response)
        required = {"route_id", "travel_time_seconds", "length_meters", "points", "geojson"}
        for route in routes:
            assert required.issubset(route.keys())

    def test_assigns_unique_route_ids(self, sample_routing_response):
        routes = parse_routing_response(sample_routing_response)
        ids = [r["route_id"] for r in routes]
        assert len(ids) == len(set(ids))

    def test_travel_times_match_source(self, sample_routing_response):
        routes = parse_routing_response(sample_routing_response)
        assert routes[0]["travel_time_seconds"] == 900
        assert routes[1]["travel_time_seconds"] == 1100
        assert routes[2]["travel_time_seconds"] == 1350

    def test_points_are_lat_lng_lists(self, sample_routing_response):
        routes = parse_routing_response(sample_routing_response)
        for point in routes[0]["points"]:
            assert len(point) == 2  # [lat, lng]

    def test_returns_empty_list_when_no_routes(self):
        result = parse_routing_response({"routes": []})
        assert result == []

    def test_geojson_is_linestring_feature(self, sample_routing_response):
        routes = parse_routing_response(sample_routing_response)
        geojson = routes[0]["geojson"]
        assert geojson["type"] == "Feature"
        assert geojson["geometry"]["type"] == "LineString"

    def test_geojson_coordinates_are_lng_lat_order(self, sample_routing_response):
        """GeoJSON spec: [longitude, latitude]. Dublin longitude is negative."""
        routes = parse_routing_response(sample_routing_response)
        coord = routes[0]["geojson"]["geometry"]["coordinates"][0]
        assert coord[0] < 0, "First element should be longitude (negative for Ireland)"


# ---------------------------------------------------------------------------
# build_avoidance_params tests
# ---------------------------------------------------------------------------

class TestBuildAvoidanceParams:
    """Section 11 — Blocked roads → TomTom avoidance parameter format."""

    def test_builds_one_constraint_per_road(self, sample_blocked_roads):
        params = build_avoidance_params(sample_blocked_roads)
        assert len(params) == 2

    def test_constraint_has_bounding_box_structure(self, sample_blocked_roads):
        params = build_avoidance_params(sample_blocked_roads)
        for p in params:
            assert "avoidAreaRectangle" in p
            box = p["avoidAreaRectangle"]
            assert "southWestCorner" in box
            assert "northEastCorner" in box

    def test_bounding_box_contains_lat_lng(self, sample_blocked_roads):
        params = build_avoidance_params(sample_blocked_roads)
        sw = params[0]["avoidAreaRectangle"]["southWestCorner"]
        assert "latitude" in sw and "longitude" in sw

    def test_south_less_than_north(self, sample_blocked_roads):
        params = build_avoidance_params(sample_blocked_roads)
        box = params[0]["avoidAreaRectangle"]
        assert box["southWestCorner"]["latitude"] < box["northEastCorner"]["latitude"]

    def test_west_less_than_east(self, sample_blocked_roads):
        params = build_avoidance_params(sample_blocked_roads)
        box = params[0]["avoidAreaRectangle"]
        assert box["southWestCorner"]["longitude"] < box["northEastCorner"]["longitude"]

    def test_empty_roads_returns_empty(self):
        assert build_avoidance_params([]) == []

    def test_skips_road_with_missing_coordinates(self):
        roads = [{"segment_id": "bad", "start_lat": None, "start_lng": -6.3,
                  "end_lat": 53.3, "end_lng": -6.2}]
        params = build_avoidance_params(roads)
        assert params == []


# ---------------------------------------------------------------------------
# extract_geojson tests
# ---------------------------------------------------------------------------

class TestExtractGeoJSON:
    """Section 6.2 — Routes → GeoJSON FeatureCollection for Mapbox GL JS."""

    def test_produces_feature_collection(self, sample_routing_response):
        routes = parse_routing_response(sample_routing_response)
        geojson = extract_geojson(routes)
        assert geojson["type"] == "FeatureCollection"

    def test_one_feature_per_route(self, sample_routing_response):
        routes = parse_routing_response(sample_routing_response)
        geojson = extract_geojson(routes)
        assert len(geojson["features"]) == 3

    def test_each_feature_is_linestring(self, sample_routing_response):
        routes = parse_routing_response(sample_routing_response)
        geojson = extract_geojson(routes)
        for feat in geojson["features"]:
            assert feat["geometry"]["type"] == "LineString"

    def test_properties_include_route_metadata(self, sample_routing_response):
        routes = parse_routing_response(sample_routing_response)
        geojson = extract_geojson(routes)
        props = geojson["features"][0]["properties"]
        assert "route_id" in props
        assert "travel_time_seconds" in props

    def test_empty_routes_returns_empty_collection(self):
        geojson = extract_geojson([])
        assert geojson["type"] == "FeatureCollection"
        assert geojson["features"] == []


# ---------------------------------------------------------------------------
# Congestion helpers
# ---------------------------------------------------------------------------

class TestCongestionHelpers:
    """Internal helper functions for congestion calculations."""

    def test_high_congestion_ratio_for_slow_speed(self):
        ratio = _compute_congestion_ratio(current_speed=45, free_flow_speed=110)
        assert ratio > 0.5

    def test_zero_congestion_ratio_for_free_flow(self):
        ratio = _compute_congestion_ratio(current_speed=108, free_flow_speed=110)
        assert ratio < 0.05

    def test_congestion_ratio_non_negative(self):
        ratio = _compute_congestion_ratio(current_speed=120, free_flow_speed=100)
        assert ratio >= 0.0

    def test_zero_free_flow_speed_returns_zero(self):
        ratio = _compute_congestion_ratio(current_speed=0, free_flow_speed=0)
        assert ratio == 0.0

    def test_congestion_level_thresholds(self):
        assert _speed_ratio_to_congestion_level(90, 100) == "light"    # 0.90
        assert _speed_ratio_to_congestion_level(60, 100) == "moderate" # 0.60
        assert _speed_ratio_to_congestion_level(35, 100) == "heavy"    # 0.35
        assert _speed_ratio_to_congestion_level(10, 100) == "severe"   # 0.10
        assert _speed_ratio_to_congestion_level(50, 0)   == "unknown"