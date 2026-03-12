# """
# tests/unit/test_tomtom_parser.py

# Unit tests — TomTom API response parsing.
# Validates that the External Integration Service correctly interprets
# TomTom Traffic Flow and Routing API responses.
# No external I/O. Pure function tests.
# """
# import pytest
# from app.external.tomtom_parser import (
#     parse_traffic_flow_response,
#     parse_routing_response,
#     build_avoidance_constraints,
#     extract_congestion_factor,
#     extract_geojson_from_route,
# )


# class TestTrafficFlowParsing:
#     """Section 6.1 — TomTom Traffic Flow API response parsing."""

#     def test_parses_current_speed_per_segment(self, sample_tomtom_traffic_response):
#         result = parse_traffic_flow_response(sample_tomtom_traffic_response)
#         assert result[0]["current_speed_kmh"] == 45

#     def test_parses_free_flow_speed_per_segment(self, sample_tomtom_traffic_response):
#         result = parse_traffic_flow_response(sample_tomtom_traffic_response)
#         assert result[0]["free_flow_speed_kmh"] == 110

#     def test_computes_congestion_ratio(self, sample_tomtom_traffic_response):
#         """Congestion ratio = current_speed / free_flow_speed. Lower = more congested."""
#         result = parse_traffic_flow_response(sample_tomtom_traffic_response)
#         ratio = result[0]["congestion_ratio"]
#         assert round(ratio, 2) == round(45 / 110, 2)

#     def test_parses_coordinates_as_list_of_points(self, sample_tomtom_traffic_response):
#         result = parse_traffic_flow_response(sample_tomtom_traffic_response)
#         assert isinstance(result[0]["coordinates"], list)
#         assert len(result[0]["coordinates"]) == 2

#     def test_handles_empty_flow_segment_list(self):
#         result = parse_traffic_flow_response({"flowSegmentData": []})
#         assert result == []

#     def test_handles_missing_confidence_field_gracefully(self):
#         response = {
#             "flowSegmentData": [
#                 {
#                     "frc": "FRC1",
#                     "currentSpeed": 60,
#                     "freeFlowSpeed": 80,
#                     "currentTravelTime": 100,
#                     "freeFlowTravelTime": 80,
#                     # no "confidence" key
#                     "coordinates": {"coordinate": []},
#                 }
#             ]
#         }
#         result = parse_traffic_flow_response(response)
#         assert result[0].get("confidence") is None or result[0].get("confidence") == 1.0

#     def test_congestion_ratio_clamped_to_one_when_speed_exceeds_free_flow(self):
#         """TomTom can occasionally report current > free flow on quiet roads."""
#         response = {
#             "flowSegmentData": [
#                 {
#                     "frc": "FRC2",
#                     "currentSpeed": 120,
#                     "freeFlowSpeed": 100,
#                     "currentTravelTime": 50,
#                     "freeFlowTravelTime": 60,
#                     "confidence": 0.7,
#                     "coordinates": {"coordinate": []},
#                 }
#             ]
#         }
#         result = parse_traffic_flow_response(response)
#         assert result[0]["congestion_ratio"] <= 1.0


# class TestRoutingResponseParsing:
#     """Section 6.1 — TomTom Routing API response parsing."""

#     def test_returns_correct_number_of_alternative_routes(
#         self, sample_tomtom_routing_response
#     ):
#         routes = parse_routing_response(sample_tomtom_routing_response)
#         assert len(routes) == 3

#     def test_primary_route_has_shortest_travel_time(self, sample_tomtom_routing_response):
#         routes = parse_routing_response(sample_tomtom_routing_response)
#         times = [r["travel_time_seconds"] for r in routes]
#         assert times == sorted(times)

#     def test_each_route_contains_required_fields(self, sample_tomtom_routing_response):
#         routes = parse_routing_response(sample_tomtom_routing_response)
#         required = {"route_id", "travel_time_seconds", "length_meters", "points"}
#         for route in routes:
#             assert required.issubset(route.keys()), f"Missing keys in route: {route.keys()}"

#     def test_route_points_are_lat_lng_tuples(self, sample_tomtom_routing_response):
#         routes = parse_routing_response(sample_tomtom_routing_response)
#         for point in routes[0]["points"]:
#             assert "lat" in point and "lng" in point

#     def test_returns_empty_list_when_no_routes(self):
#         result = parse_routing_response({"routes": []})
#         assert result == []

#     def test_assigns_unique_route_ids(self, sample_tomtom_routing_response):
#         routes = parse_routing_response(sample_tomtom_routing_response)
#         ids = [r["route_id"] for r in routes]
#         assert len(ids) == len(set(ids))


# class TestAvoidanceConstraintBuilding:
#     """Section 11 — Blocked roads → TomTom avoidance parameter format."""

#     def test_builds_avoid_param_from_blocked_segments(self, sample_blocked_roads):
#         constraints = build_avoidance_constraints(sample_blocked_roads)
#         assert len(constraints) == 2

#     def test_avoid_constraint_contains_bounding_box(self, sample_blocked_roads):
#         constraints = build_avoidance_constraints(sample_blocked_roads)
#         for c in constraints:
#             assert "south_west" in c and "north_east" in c

#     def test_empty_blocked_roads_returns_empty_constraints(self):
#         constraints = build_avoidance_constraints([])
#         assert constraints == []

#     def test_duplicate_segments_are_deduplicated(self, sample_blocked_roads):
#         doubled = sample_blocked_roads + sample_blocked_roads
#         constraints = build_avoidance_constraints(doubled)
#         # Should produce same count as original, not double
#         assert len(constraints) == len(sample_blocked_roads)


# class TestCongestionFactorExtraction:
#     """Section 11 — Score routes: travel_time × (1 + congestion_factor)."""

#     def test_high_congestion_produces_factor_above_one(self):
#         # 45 km/h on a 110 km/h free-flow road — very congested
#         factor = extract_congestion_factor(current_speed=45, free_flow_speed=110)
#         assert factor > 1.0

#     def test_free_flowing_traffic_produces_factor_near_zero(self):
#         factor = extract_congestion_factor(current_speed=108, free_flow_speed=110)
#         assert factor < 0.1

#     def test_factor_is_non_negative(self):
#         factor = extract_congestion_factor(current_speed=120, free_flow_speed=100)
#         assert factor >= 0.0

#     def test_zero_speed_does_not_raise(self):
#         """Gridlock edge case."""
#         factor = extract_congestion_factor(current_speed=0, free_flow_speed=100)
#         assert factor > 0


# class TestGeoJSONExtraction:
#     """Section 6.2 — Route → GeoJSON for Mapbox GL JS rendering."""

#     def test_produces_valid_geojson_feature_collection(
#         self, sample_tomtom_routing_response
#     ):
#         routes = parse_routing_response(sample_tomtom_routing_response)
#         geojson = extract_geojson_from_route(routes[0])
#         assert geojson["type"] == "Feature"
#         assert geojson["geometry"]["type"] == "LineString"

#     def test_geojson_coordinates_are_lng_lat_order(
#         self, sample_tomtom_routing_response
#     ):
#         """GeoJSON spec: [longitude, latitude]. TomTom returns lat/lng — must be flipped."""
#         routes = parse_routing_response(sample_tomtom_routing_response)
#         geojson = extract_geojson_from_route(routes[0])
#         coord = geojson["geometry"]["coordinates"][0]
#         # Longitude for Dublin is roughly -6.x (negative)
#         assert coord[0] < 0, "First coord element should be longitude (negative for Ireland)"

#     def test_geojson_includes_route_metadata_in_properties(
#         self, sample_tomtom_routing_response
#     ):
#         routes = parse_routing_response(sample_tomtom_routing_response)
#         geojson = extract_geojson_from_route(routes[0])
#         assert "route_id" in geojson["properties"]
#         assert "travel_time_seconds" in geojson["properties"]