"""
app/services/predictive_congestion.py

Innovation 2 — Predictive Congestion Modeling.

No ML — pure time-based simulation:
  "If N vehicles enter segment S over the next T minutes,
   and capacity is C, congestion exceeds threshold at time X."

Each monitoring cycle runs a dual check:
  1. Reactive  — TomTom real-time speed data (current state)
  2. Predictive — occupancy projection model (future state)
If either check triggers → recalculation is required.

Key functions:
  project_segment_occupancy()   — project vehicle counts at future time steps
  predict_congestion_breaches() — identify segments that will exceed threshold
  dual_congestion_check()       — combine reactive + predictive into one decision
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Time horizons checked during each prediction run (minutes)
PREDICTION_HORIZONS_MINUTES = [5, 10, 15, 20, 30]

# Default congestion speed threshold for reactive check (km/h)
DEFAULT_SPEED_THRESHOLD_KMH = 50

# Default occupancy threshold for predictive check (fraction of capacity)
DEFAULT_THRESHOLD_PCT = 0.8


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CongestionPrediction:
    """
    Represents a predicted congestion breach on a single segment.

    Acts as a dict-like object (supports [] and .get()) so tests can use
    both attribute access and dict access interchangeably.
    """
    segment_id: str
    predicted_breach_minutes: int         # First horizon where breach is predicted
    predicted_occupancy: int              # Projected vehicle count at breach time
    capacity: int                         # Segment capacity
    occupancy_pct: float                  # Projected occupancy as fraction (0.0–1.0)
    route_id: Optional[str] = None        # Route this segment belongs to

    # dict-like access
    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def __contains__(self, key: str):
        return hasattr(self, key)


# ---------------------------------------------------------------------------
# project_segment_occupancy
# ---------------------------------------------------------------------------

def project_segment_occupancy(
    route_plan: Dict[str, Dict[str, Any]],
    segment_capacities: Dict[str, int],
    horizon_minutes: int = 30,
) -> List[Dict[str, Any]]:
    """
    Project vehicle counts on each segment at future time checkpoints.

    Model:
      - Vehicles are spread uniformly across a route's segments
      - Travel time to reach a segment = segment_length_km / average_speed_kmh * 60
      - At each horizon t, a vehicle is "on" segment S if:
            arrival_time <= t < (arrival_time + dwell_time)
        where dwell_time = segment_length_km / average_speed_kmh * 60

    Args:
        route_plan:        {route_id: {vehicles_assigned, segments,
                            average_speed_kmh, segment_length_km}}
        segment_capacities: {segment_id: int}
        horizon_minutes:   Maximum look-ahead (inclusive)

    Returns:
        List of dicts, one per PREDICTION_HORIZONS_MINUTES checkpoint:
        [
            {
                "horizon_minutes": 5,
                "segment_occupancy": {"seg-A1": 120, "seg-A2": 80, ...}
            },
            ...
        ]
        Only horizons <= horizon_minutes are included.
    """
    # Initialise zero occupancy for all known segments
    all_segments = set(segment_capacities.keys())
    for route in route_plan.values():
        all_segments.update(route.get("segments", []))

    horizons = [h for h in PREDICTION_HORIZONS_MINUTES if h <= horizon_minutes]
    results = []

    for horizon in horizons:
        occupancy: Dict[str, int] = {seg: 0 for seg in all_segments}

        for route_id, route in route_plan.items():
            vehicles = route.get("vehicles_assigned", 0)
            if vehicles == 0:
                continue

            speed_kmh = route.get("average_speed_kmh", 60)
            if speed_kmh <= 0:
                speed_kmh = 60

            segments = route.get("segments", [])
            seg_lengths = route.get("segment_length_km", {})

            # Compute cumulative travel time to each segment
            cumulative_time = 0.0
            for seg in segments:
                length_km = seg_lengths.get(seg, 1.0)
                travel_time_min = (length_km / speed_kmh) * 60
                dwell_time_min = travel_time_min  # Time spent traversing the segment

                arrival_min = cumulative_time
                departure_min = cumulative_time + dwell_time_min

                # Vehicles are on this segment between arrival and departure
                if arrival_min <= horizon < departure_min:
                    # Fraction of vehicles that have arrived but not yet departed
                    if dwell_time_min > 0:
                        progress = (horizon - arrival_min) / dwell_time_min
                        on_segment = int(vehicles * min(1.0, progress))
                    else:
                        on_segment = vehicles
                    occupancy[seg] = occupancy.get(seg, 0) + on_segment

                cumulative_time += travel_time_min

        results.append({
            "horizon_minutes": horizon,
            "segment_occupancy": occupancy,
        })

    logger.debug(
        f"project_segment_occupancy: {len(route_plan)} routes, "
        f"{len(all_segments)} segments, horizons={horizons}"
    )
    return results


# ---------------------------------------------------------------------------
# predict_congestion_breaches
# ---------------------------------------------------------------------------

def predict_congestion_breaches(
    route_plan: Dict[str, Dict[str, Any]],
    segment_capacities: Dict[str, int],
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
) -> List[CongestionPrediction]:
    """
    Identify segments predicted to exceed capacity threshold at a future horizon.

    A segment is flagged if its projected occupancy >= capacity * threshold_pct
    at any prediction horizon.

    Args:
        route_plan:         Route assignment plan (same format as project_segment_occupancy)
        segment_capacities: {segment_id: int}
        threshold_pct:      Fraction of capacity that triggers a breach (0.0–1.0)

    Returns:
        List of CongestionPrediction objects for breached segments.
        Empty list if no breaches predicted.
    """
    if not route_plan:
        return []

    projections = project_segment_occupancy(
        route_plan,
        segment_capacities,
        horizon_minutes=max(PREDICTION_HORIZONS_MINUTES),
    )

    # Build segment → route_id mapping
    seg_to_route: Dict[str, str] = {}
    for route_id, route in route_plan.items():
        for seg in route.get("segments", []):
            seg_to_route[seg] = route_id

    # Track first breach per segment
    first_breach: Dict[str, CongestionPrediction] = {}

    for projection in projections:
        horizon = projection["horizon_minutes"]
        occupancy = projection["segment_occupancy"]

        for seg_id, count in occupancy.items():
            if seg_id in first_breach:
                continue  # Already flagged at an earlier horizon

            capacity = segment_capacities.get(seg_id, 0)
            if capacity <= 0:
                continue

            threshold_count = capacity * threshold_pct
            if count >= threshold_count:
                occupancy_pct = count / capacity
                first_breach[seg_id] = CongestionPrediction(
                    segment_id=seg_id,
                    predicted_breach_minutes=horizon,
                    predicted_occupancy=count,
                    capacity=capacity,
                    occupancy_pct=round(occupancy_pct, 3),
                    route_id=seg_to_route.get(seg_id),
                )

    breaches = list(first_breach.values())
    logger.info(
        f"predict_congestion_breaches: {len(breaches)} breaches predicted "
        f"(threshold={threshold_pct:.0%})"
    )
    return breaches


# ---------------------------------------------------------------------------
# dual_congestion_check
# ---------------------------------------------------------------------------

def dual_congestion_check(
    live_traffic_data: List[Dict[str, Any]],
    route_plan: Dict[str, Dict[str, Any]],
    segment_capacities: Dict[str, int],
    congestion_speed_threshold_kmh: float = DEFAULT_SPEED_THRESHOLD_KMH,
    predictive_threshold_pct: float = DEFAULT_THRESHOLD_PCT,
) -> Dict[str, Any]:
    """
    Run both reactive and predictive congestion checks.

    Section 8.2: Each monitoring cycle runs BOTH checks.
    If either triggers → recalculation is required.

    Reactive check:
        TomTom live traffic — flag any segment where current_speed < threshold.

    Predictive check:
        Occupancy model — flag any segment predicted to breach capacity.

    Args:
        live_traffic_data:            List of parsed flow segments with
                                      'current_speed' (or 'current_speed_kmh')
                                      and 'segment_id'
        route_plan:                   Current route assignment plan
        segment_capacities:           {segment_id: int}
        congestion_speed_threshold_kmh: Speed below which reactive check fires
        predictive_threshold_pct:     Capacity fraction for predictive check

    Returns:
        {
            "should_recalculate": bool,
            "triggered_by":       "reactive" | "predictive" | "both" | "none",
            "reactive_segments":  [segment_id, ...],
            "predicted_breaches": [CongestionPrediction, ...],
        }
    """
    # --- Reactive check ---
    reactive_segments = []
    for seg in live_traffic_data:
        # Support both key names
        speed = seg.get("current_speed") or seg.get("current_speed_kmh", 0)
        if speed < congestion_speed_threshold_kmh:
            seg_id = seg.get("segment_id")
            if seg_id:
                reactive_segments.append(seg_id)

    reactive_triggered = len(reactive_segments) > 0

    # --- Predictive check ---
    predicted_breaches = predict_congestion_breaches(
        route_plan=route_plan,
        segment_capacities=segment_capacities,
        threshold_pct=predictive_threshold_pct,
    )
    predictive_triggered = len(predicted_breaches) > 0

    # --- Combine ---
    should_recalculate = reactive_triggered or predictive_triggered

    if reactive_triggered and predictive_triggered:
        triggered_by = "both"
    elif reactive_triggered:
        triggered_by = "reactive"
    elif predictive_triggered:
        triggered_by = "predictive"
    else:
        triggered_by = "none"

    logger.info(
        f"dual_congestion_check: should_recalculate={should_recalculate} "
        f"triggered_by={triggered_by} "
        f"reactive_segments={len(reactive_segments)} "
        f"predicted_breaches={len(predicted_breaches)}"
    )

    return {
        "should_recalculate": should_recalculate,
        "triggered_by": triggered_by,
        "reactive_segments": reactive_segments,
        "predicted_breaches": predicted_breaches,
    }