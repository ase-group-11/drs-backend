"""
app/services/traffic_distribution.py

Innovation 1 — Capacity-Aware Greedy Traffic Distribution.

Core algorithm:
  1. Score each alternative route: score = travel_time × (1 + congestion_factor)
  2. Sort routes by score ascending (best first)
  3. Assign vehicles greedily: each vehicle gets the best route that still
     has remaining capacity on all its segments
  4. If all routes are at capacity, overflow to the least-loaded route
  5. Priority ordering: emergency → public_transport → general

All functions are pure (no I/O, no side effects) for easy unit testing.

API convention (matches test spec):
  optimize_traffic_distribution(routes, vehicles, ...)
  analyze_route_capacity(routes, expected_traffic=None)
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRIORITY_ORDER = {
    "emergency": 0,
    "public_transport": 1,
    "general": 2,
}

DEFAULT_SEGMENT_CAPACITY = 300  # vehicles / hour if not specified


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_route(
    route: Dict[str, Any],
    traffic_segments: Optional[List[Dict[str, Any]]] = None,
) -> float:
    """
    Compute a composite score for a route.

    score = travel_time_seconds × (1 + congestion_factor)

    congestion_factor is derived from:
      - traffic_segments (if provided) — avg congestion_ratio from flow data
      - segment load ratio (current_load / segment_capacity) — fallback
      - traffic_delay_seconds / travel_time_seconds — last resort

    Lower score = better route.

    Args:
        route:            Parsed route dict
        traffic_segments: Optional list of flow segments with congestion_ratio

    Returns:
        Float score — lower is better
    """
    travel_time = route.get("travel_time_seconds", 0)
    if travel_time <= 0:
        return float("inf")

    if traffic_segments:
        congestion_factor = _avg_congestion_ratio(traffic_segments)
    else:
        # Derive from segment load ratio if available
        capacities = route.get("segment_capacities", {})
        loads = route.get("current_load", {})
        if capacities:
            ratios = [
                loads.get(seg, 0) / cap
                for seg, cap in capacities.items()
                if cap > 0
            ]
            congestion_factor = sum(ratios) / len(ratios) if ratios else 0.0
        else:
            traffic_delay = route.get("traffic_delay_seconds", 0)
            congestion_factor = traffic_delay / travel_time if travel_time > 0 else 0.0

    score = travel_time * (1 + congestion_factor)
    logger.debug(
        f"score_route: route_id={route.get('route_id')} "
        f"travel_time={travel_time}s congestion_factor={congestion_factor:.3f} "
        f"score={score:.1f}"
    )
    return score


def analyze_route_capacity(
    routes: List[Dict[str, Any]],
    expected_traffic: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Compute remaining capacity and availability for each route.

    Remaining capacity = min(segment_capacity - current_load) across all
    segments — the bottleneck capacity.

    A route is marked available=True if remaining_capacity > 0.
    If expected_traffic is provided, a route is available only if it can
    absorb at least that many additional vehicles.

    Args:
        routes:           List of route dicts with segment_capacities, current_load
        expected_traffic: Optional number of vehicles to test capacity against

    Returns:
        Same list with added fields:
            - remaining_capacity: int
            - utilization_pct:    float (0–100)
            - available:          bool
    """
    enriched = []
    threshold = expected_traffic or 1

    for route in routes:
        capacities = route.get("segment_capacities", {})
        loads = route.get("current_load", {})

        if not capacities:
            remaining = DEFAULT_SEGMENT_CAPACITY
            utilization = 0.0
        else:
            remaining_per_segment = [
                max(0, capacities.get(seg_id, DEFAULT_SEGMENT_CAPACITY) - loads.get(seg_id, 0))
                for seg_id in capacities
            ]
            remaining = min(remaining_per_segment) if remaining_per_segment else DEFAULT_SEGMENT_CAPACITY
            total_capacity = sum(capacities.values())
            total_load = sum(loads.values())
            utilization = (total_load / total_capacity * 100) if total_capacity > 0 else 0.0

        available = remaining >= threshold

        enriched.append({
            **route,
            "remaining_capacity": remaining,
            "utilization_pct": round(utilization, 1),
            "available": available,
        })

    return enriched


def optimize_traffic_distribution(
    routes: List[Dict[str, Any]],
    vehicles: List[Dict[str, Any]],
    traffic_segments: Optional[List[Dict[str, Any]]] = None,
) -> "DistributionPlan":
    """
    Assign vehicles to routes using capacity-aware greedy distribution.

    Algorithm:
      1. Enrich routes with remaining_capacity + available flag
      2. Score and sort routes (best first)
      3. Sort vehicles by priority (emergency first)
      4. For each vehicle: assign to best available route
         If no route has capacity: overflow to least-loaded route
      5. Update in-memory loads after each assignment

    Args:
        routes:           Alternative route dicts from parse_routing_response
        vehicles:         List of vehicle dicts with 'user_id', 'type'
        traffic_segments: Optional flow data for congestion scoring

    Returns:
        DistributionPlan with route_assignments, capacity_usage, estimated_times
    """
    if not routes:
        logger.warning("optimize_traffic_distribution: no routes provided")
        return DistributionPlan(
            route_assignments={},
            route_stats={},
            overflow_count=0,
        )

    # Step 1 — enrich with capacity
    enriched_routes = analyze_route_capacity(routes)

    # Step 2 — score and sort
    scored = sorted(
        enriched_routes,
        key=lambda r: score_route(r, traffic_segments),
    )

    # Step 3 — sort vehicles by priority
    sorted_vehicles = sorted(
        vehicles,
        key=lambda v: PRIORITY_ORDER.get(v.get("type", "general"), 2),
    )

    # Step 4 — greedy assignment with live load tracking
    live_remaining: Dict[str, int] = {
        r["route_id"]: r["remaining_capacity"]
        for r in enriched_routes
    }

    route_assignments: Dict[str, str] = {}
    overflow_count = 0

    for vehicle in sorted_vehicles:
        user_id = vehicle.get("user_id")
        if not user_id:
            continue
        assigned = False

        for route in scored:
            route_id = route["route_id"]
            if live_remaining.get(route_id, 0) > 0:
                route_assignments[user_id] = route_id
                live_remaining[route_id] = max(0, live_remaining[route_id] - 1)
                assigned = True
                break

        if not assigned:
            # Overflow: assign to least loaded route by utilization
            overflow_route = min(
                enriched_routes,
                key=lambda r: score_route(r, traffic_segments),
            )
            route_assignments[user_id] = overflow_route["route_id"]
            overflow_count += 1

    # Step 5 — build stats
    route_stats = _compute_route_stats(enriched_routes, route_assignments, live_remaining)

    logger.info(
        f"optimize_traffic_distribution: {len(vehicles)} vehicles → "
        f"{len(routes)} routes, overflow={overflow_count}"
    )

    return DistributionPlan(
        route_assignments=route_assignments,
        route_stats=route_stats,
        overflow_count=overflow_count,
    )


# ---------------------------------------------------------------------------
# DistributionPlan result object
# ---------------------------------------------------------------------------

class DistributionPlan:
    """
    Result of optimize_traffic_distribution.

    Attributes:
        route_assignments: {user_id: route_id}
        route_stats:       {route_id: {assigned, remaining_capacity, utilization_pct, travel_time_seconds}}
        overflow_count:    Number of vehicles assigned despite full capacity
        capacity_usage:    Alias for route_stats (test spec compat)
        estimated_times:   {route_id: travel_time_seconds}
    """

    def __init__(
        self,
        route_assignments: Dict[str, str],
        route_stats: Dict[str, Any],
        overflow_count: int,
    ):
        self.route_assignments = route_assignments
        self.route_stats = route_stats
        self.overflow_count = overflow_count

    @property
    def capacity_usage(self) -> Dict[str, Any]:
        """
        Alias for route_stats in test-spec format.

        Returns {route_id: {"vehicles_assigned": int, "capacity": int}}
        """
        usage = {}
        for route_id, stats in self.route_stats.items():
            usage[route_id] = {
                "vehicles_assigned": stats.get("assigned", 0),
                "capacity": stats.get("remaining_capacity", DEFAULT_SEGMENT_CAPACITY)
                + stats.get("assigned", 0),
            }
        return usage

    @property
    def estimated_times(self) -> Dict[str, int]:
        """Return {route_id: travel_time_seconds} for each route."""
        return {
            route_id: stats.get("travel_time_seconds", 0)
            for route_id, stats in self.route_stats.items()
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route_assignments": self.route_assignments,
            "route_stats": self.route_stats,
            "overflow_count": self.overflow_count,
            "total_assigned": len(self.route_assignments),
        }

    def __repr__(self) -> str:
        return (
            f"<DistributionPlan vehicles={len(self.route_assignments)} "
            f"overflow={self.overflow_count}>"
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _avg_congestion_ratio(segments: List[Dict[str, Any]]) -> float:
    ratios = [s.get("congestion_ratio", 0.0) for s in segments if "congestion_ratio" in s]
    return sum(ratios) / len(ratios) if ratios else 0.0


def _compute_route_stats(
    routes: List[Dict[str, Any]],
    assignments: Dict[str, str],
    live_remaining: Dict[str, int],
) -> Dict[str, Any]:
    assigned_count: Dict[str, int] = {}
    for route_id in assignments.values():
        assigned_count[route_id] = assigned_count.get(route_id, 0) + 1

    stats = {}
    for route in routes:
        route_id = route["route_id"]
        stats[route_id] = {
            "assigned": assigned_count.get(route_id, 0),
            "remaining_capacity": live_remaining.get(route_id, route.get("remaining_capacity", 0)),
            "utilization_pct": route.get("utilization_pct", 0.0),
            "travel_time_seconds": route.get("travel_time_seconds", 0),
        }
    return stats