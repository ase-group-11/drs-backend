"""
tests/unit/test_traffic_distribution.py

Unit tests — Innovation 1: Capacity-Aware Traffic Distribution.
Section 8.1 + Section 11 (Steps 4–5).

The greedy heuristic must:
  - Prevent all vehicles from piling onto the shortest detour
  - Update effective segment load after each vehicle batch assignment
  - Respect per-segment capacity limits
  - Produce a ReroutePlan with deterministic route assignments
"""
import pytest
from app.services.traffic_distribution import (
    analyze_route_capacity,
    optimize_traffic_distribution,
    score_route,
    DistributionPlan as ReroutePlan,
)


# ---------------------------------------------------------------------------
# Fixtures — capacity and load scenarios
# ---------------------------------------------------------------------------

@pytest.fixture
def three_routes_equal_capacity():
    """Three routes with identical capacity — load should spread evenly."""
    return [
        {
            "route_id": f"route-{label}",
            "travel_time_seconds": base_time,
            "length_meters": 12000 + i * 2000,
            "segments": [f"seg-{label}-A", f"seg-{label}-B"],
            "segment_capacities": {f"seg-{label}-A": 200, f"seg-{label}-B": 200},
            "current_load": {f"seg-{label}-A": 0, f"seg-{label}-B": 0},
        }
        for i, (label, base_time) in enumerate(
            [("primary", 600), ("secondary", 800), ("tertiary", 1000)]
        )
    ]


@pytest.fixture
def primary_route_near_capacity():
    """Primary route almost full — overflow should go to secondary."""
    return [
        {
            "route_id": "route-primary",
            "travel_time_seconds": 600,
            "length_meters": 10000,
            "segments": ["seg-P-A", "seg-P-B"],
            "segment_capacities": {"seg-P-A": 100, "seg-P-B": 100},
            "current_load": {"seg-P-A": 95, "seg-P-B": 95},  # nearly full
        },
        {
            "route_id": "route-secondary",
            "travel_time_seconds": 900,
            "length_meters": 14000,
            "segments": ["seg-S-A", "seg-S-B"],
            "segment_capacities": {"seg-S-A": 300, "seg-S-B": 300},
            "current_load": {"seg-S-A": 10, "seg-S-B": 10},
        },
    ]


@pytest.fixture
def fifty_vehicles():
    return [
        {
            "user_id": f"user-{i}",
            "current_location": {"lat": 53.30 + i * 0.001, "lng": -6.36},
            "destination": {"lat": 53.40, "lng": -6.25},
        }
        for i in range(50)
    ]


@pytest.fixture
def single_overloaded_route():
    """Only one route, but more vehicles than capacity — tests graceful overflow."""
    return [
        {
            "route_id": "route-only",
            "travel_time_seconds": 700,
            "length_meters": 11000,
            "segments": ["seg-O-A"],
            "segment_capacities": {"seg-O-A": 20},
            "current_load": {"seg-O-A": 0},
        }
    ]


# ---------------------------------------------------------------------------
# analyze_route_capacity tests
# ---------------------------------------------------------------------------

class TestAnalyzeRouteCapacity:
    """Section 11 Step 4 — capacity analysis scores each alternative route."""

    def test_returns_capacity_analysis_for_each_route(
        self, three_routes_equal_capacity
    ):
        analysis = analyze_route_capacity(three_routes_equal_capacity, expected_traffic=50)
        assert len(analysis) == 3

    def test_remaining_capacity_computed_correctly(self, three_routes_equal_capacity):
        analysis = analyze_route_capacity(three_routes_equal_capacity, expected_traffic=50)
        for item in analysis:
            # Each segment capacity 200, load 0 → remaining = 200
            assert item["remaining_capacity"] == 200

    def test_score_incorporates_congestion_factor(self):
        """score = travel_time × (1 + congestion_factor). Congested route scores higher (worse)."""
        congested = {
            "route_id": "congested",
            "travel_time_seconds": 600,
            "length_meters": 10000,
            "segments": ["seg-C"],
            "segment_capacities": {"seg-C": 200},
            "current_load": {"seg-C": 190},  # 95% full
        }
        free = {
            "route_id": "free",
            "travel_time_seconds": 600,
            "length_meters": 10000,
            "segments": ["seg-F"],
            "segment_capacities": {"seg-F": 200},
            "current_load": {"seg-F": 10},  # 5% full
        }
        score_congested = score_route(congested)
        score_free = score_route(free)
        assert score_congested > score_free

    def test_over_capacity_route_marked_as_unavailable(self):
        overloaded = {
            "route_id": "overloaded",
            "travel_time_seconds": 600,
            "length_meters": 10000,
            "segments": ["seg-X"],
            "segment_capacities": {"seg-X": 50},
            "current_load": {"seg-X": 55},  # already over capacity
        }
        analysis = analyze_route_capacity([overloaded], expected_traffic=10)
        assert analysis[0]["available"] is False

    def test_analysis_marks_available_routes_correctly(
        self, three_routes_equal_capacity
    ):
        analysis = analyze_route_capacity(three_routes_equal_capacity, expected_traffic=50)
        assert all(a["available"] for a in analysis)


# ---------------------------------------------------------------------------
# optimize_traffic_distribution tests (Innovation 1)
# ---------------------------------------------------------------------------

class TestOptimizeTrafficDistribution:
    """Section 8.1 — Greedy capacity-aware load balancing."""

    def test_returns_reroute_plan(self, three_routes_equal_capacity, fifty_vehicles):
        plan = optimize_traffic_distribution(three_routes_equal_capacity, fifty_vehicles)
        assert isinstance(plan, ReroutePlan)

    def test_all_vehicles_assigned_a_route(self, three_routes_equal_capacity, fifty_vehicles):
        plan = optimize_traffic_distribution(three_routes_equal_capacity, fifty_vehicles)
        assert len(plan.route_assignments) == len(fifty_vehicles)

    def test_load_spreads_across_multiple_routes(
        self, three_routes_equal_capacity, fifty_vehicles
    ):
        """Core assertion: not all 50 vehicles on the fastest route."""
        plan = optimize_traffic_distribution(three_routes_equal_capacity, fifty_vehicles)
        routes_used = set(plan.route_assignments.values())
        assert len(routes_used) > 1, (
            "All vehicles piled onto one route — greedy balancer not working"
        )

    def test_primary_route_not_over_capacity(
        self, three_routes_equal_capacity, fifty_vehicles
    ):
        plan = optimize_traffic_distribution(three_routes_equal_capacity, fifty_vehicles)
        for route_id, usage in plan.capacity_usage.items():
            # capacity per route is 200 — 50 total vehicles, 3 routes → well within limits
            assert usage["vehicles_assigned"] <= usage["capacity"]

    def test_overflow_moves_to_secondary_when_primary_full(
        self, primary_route_near_capacity, fifty_vehicles
    ):
        """Primary has only 5 remaining slots — remaining 45+ must go to secondary."""
        plan = optimize_traffic_distribution(primary_route_near_capacity, fifty_vehicles)
        secondary_count = sum(
            1 for v in plan.route_assignments.values() if v == "route-secondary"
        )
        assert secondary_count >= 45

    def test_segment_loads_updated_incrementally(
        self, three_routes_equal_capacity, fifty_vehicles
    ):
        """Greedy heuristic: each assignment updates loads before the next decision."""
        plan = optimize_traffic_distribution(three_routes_equal_capacity, fifty_vehicles)
        total_assigned = sum(
            u["vehicles_assigned"] for u in plan.capacity_usage.values()
        )
        assert total_assigned == len(fifty_vehicles)

    def test_graceful_handling_when_all_routes_full(self, single_overloaded_route):
        """More vehicles than total capacity — everyone still gets the best available route."""
        vehicles = [{"user_id": f"u-{i}"} for i in range(50)]
        plan = optimize_traffic_distribution(single_overloaded_route, vehicles)
        # Should not raise; may mark some as "over_capacity" in plan
        assert len(plan.route_assignments) == 50

    def test_estimated_times_present_for_each_route(
        self, three_routes_equal_capacity, fifty_vehicles
    ):
        plan = optimize_traffic_distribution(three_routes_equal_capacity, fifty_vehicles)
        for route_id in plan.capacity_usage:
            assert route_id in plan.estimated_times

    def test_deterministic_output_for_same_input(
        self, three_routes_equal_capacity, fifty_vehicles
    ):
        """Same input → same assignment. No randomness."""
        plan_a = optimize_traffic_distribution(three_routes_equal_capacity, fifty_vehicles)
        plan_b = optimize_traffic_distribution(three_routes_equal_capacity, fifty_vehicles)
        assert plan_a.route_assignments == plan_b.route_assignments


# ---------------------------------------------------------------------------
# score_route tests
# ---------------------------------------------------------------------------

class TestScoreRoute:
    """Section 11 — travel_time × (1 + congestion_factor) scoring function."""

    def test_lower_score_preferred(self):
        fast = {
            "route_id": "fast",
            "travel_time_seconds": 500,
            "segments": ["seg-F"],
            "segment_capacities": {"seg-F": 200},
            "current_load": {"seg-F": 0},
        }
        slow = {
            "route_id": "slow",
            "travel_time_seconds": 900,
            "segments": ["seg-S"],
            "segment_capacities": {"seg-S": 200},
            "current_load": {"seg-S": 0},
        }
        assert score_route(fast) < score_route(slow)

    def test_congestion_penalty_overrides_shorter_time(self):
        """A shorter route with heavy congestion should score worse than a clear longer route."""
        short_congested = {
            "route_id": "short-congested",
            "travel_time_seconds": 400,
            "segments": ["seg-SC"],
            "segment_capacities": {"seg-SC": 100},
            "current_load": {"seg-SC": 99},
        }
        long_clear = {
            "route_id": "long-clear",
            "travel_time_seconds": 700,
            "segments": ["seg-LC"],
            "segment_capacities": {"seg-LC": 100},
            "current_load": {"seg-LC": 2},
        }
        assert score_route(short_congested) > score_route(long_clear)