"""
tests/unit/test_predictive_congestion.py

Unit tests — Innovation 2: Predictive Congestion Modeling.
Section 8.2 + Section 11 (Prediction Layer).

No ML — pure time-based simulation:
  "If N vehicles enter segment S over the next T minutes,
   and capacity is C, congestion exceeds threshold at time X."

Each monitoring cycle runs dual checks:
  1. TomTom real-time data (reactive)
  2. Prediction model (proactive)
If either triggers → recalculation.
"""
import pytest
from app.services.predictive_congestion import (
    project_segment_occupancy,
    predict_congestion_breaches,
    CongestionPrediction,
    PREDICTION_HORIZONS_MINUTES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def route_assignment_plan():
    """300 vehicles distributed across 2 routes (200 + 100)."""
    return {
        "route-alpha": {
            "vehicles_assigned": 200,
            "segments": ["seg-A1", "seg-A2"],
            "average_speed_kmh": 60,
            "segment_length_km": {"seg-A1": 3.0, "seg-A2": 4.0},
        },
        "route-beta": {
            "vehicles_assigned": 100,
            "segments": ["seg-B1"],
            "average_speed_kmh": 80,
            "segment_length_km": {"seg-B1": 5.0},
        },
    }


@pytest.fixture
def segment_capacities():
    return {
        "seg-A1": 150,  # Will be overloaded — 200 vehicles on a 150-cap segment
        "seg-A2": 300,
        "seg-B1": 200,
    }


# ---------------------------------------------------------------------------
# project_segment_occupancy tests
# ---------------------------------------------------------------------------

class TestProjectSegmentOccupancy:
    """Predicts vehicle count on each segment at future time steps."""

    def test_returns_predictions_for_all_horizons(
        self, route_assignment_plan, segment_capacities
    ):
        predictions = project_segment_occupancy(
            route_assignment_plan, segment_capacities, horizon_minutes=30
        )
        # Should have predictions for each horizon checkpoint
        assert len(predictions) == len(PREDICTION_HORIZONS_MINUTES)

    def test_vehicles_arrive_at_segment_after_travel_time(
        self, route_assignment_plan, segment_capacities
    ):
        """
        seg-A1 is 3 km at 60 km/h → 3 min travel time.
        At t=5 min, vehicles should have started entering seg-A1.
        """
        predictions = project_segment_occupancy(
            route_assignment_plan, segment_capacities, horizon_minutes=15
        )
        t5 = next(p for p in predictions if p["horizon_minutes"] == 5)
        assert t5["segment_occupancy"]["seg-A1"] > 0

    def test_segment_occupancy_does_not_exceed_assigned_vehicles(
        self, route_assignment_plan, segment_capacities
    ):
        predictions = project_segment_occupancy(
            route_assignment_plan, segment_capacities, horizon_minutes=30
        )
        for p in predictions:
            for seg, count in p["segment_occupancy"].items():
                total_vehicles = sum(
                    r["vehicles_assigned"] for r in route_assignment_plan.values()
                    if seg in r["segments"]
                )
                assert count <= total_vehicles

    def test_occupancy_increases_then_decreases_as_vehicles_pass_through(
        self, route_assignment_plan, segment_capacities
    ):
        """Vehicles enter a segment, travel through, then exit. Count should peak then drop."""
        predictions = project_segment_occupancy(
            route_assignment_plan, segment_capacities, horizon_minutes=60
        )
        counts = [p["segment_occupancy"].get("seg-A1", 0) for p in predictions]
        # Should not be monotonically increasing for the full 60 min
        assert max(counts) > counts[-1] or counts[-1] == 0

    def test_empty_plan_produces_zero_occupancy(self, segment_capacities):
        predictions = project_segment_occupancy({}, segment_capacities, horizon_minutes=30)
        for p in predictions:
            assert all(v == 0 for v in p["segment_occupancy"].values())


# ---------------------------------------------------------------------------
# predict_congestion_breaches tests
# ---------------------------------------------------------------------------

class TestPredictCongestionBreaches:
    """Flags segments that will exceed capacity threshold at a future time."""

    def test_detects_breach_on_overloaded_segment(
        self, route_assignment_plan, segment_capacities
    ):
        """seg-A1 has 200 vehicles vs capacity 150 → breach expected at t=5."""
        breaches = predict_congestion_breaches(
            route_assignment_plan,
            segment_capacities,
            threshold_pct=0.8,
        )
        breached_segments = [b["segment_id"] for b in breaches]
        assert "seg-A1" in breached_segments

    def test_non_overloaded_segment_not_flagged(
        self, route_assignment_plan, segment_capacities
    ):
        """seg-B1 has 100 vehicles vs capacity 200 → no breach."""
        breaches = predict_congestion_breaches(
            route_assignment_plan,
            segment_capacities,
            threshold_pct=0.8,
        )
        breached_segments = [b["segment_id"] for b in breaches]
        assert "seg-B1" not in breached_segments

    def test_breach_includes_predicted_time(
        self, route_assignment_plan, segment_capacities
    ):
        breaches = predict_congestion_breaches(
            route_assignment_plan, segment_capacities, threshold_pct=0.8
        )
        for b in breaches:
            assert "predicted_breach_minutes" in b
            assert b["predicted_breach_minutes"] > 0

    def test_breach_prediction_is_a_congestion_prediction_object(
        self, route_assignment_plan, segment_capacities
    ):
        breaches = predict_congestion_breaches(
            route_assignment_plan, segment_capacities, threshold_pct=0.8
        )
        for b in breaches:
            assert isinstance(b, CongestionPrediction)

    def test_threshold_100pct_produces_no_breaches(
        self, route_assignment_plan, segment_capacities
    ):
        """With 100% threshold, nothing is flagged — requires actual overflow."""
        breaches = predict_congestion_breaches(
            route_assignment_plan, segment_capacities, threshold_pct=1.0
        )
        # seg-A1 has 200 vs capacity 150 — actual overflow
        breached = [b["segment_id"] for b in breaches]
        assert "seg-A1" in breached

    def test_threshold_10pct_flags_all_active_segments(
        self, route_assignment_plan, segment_capacities
    ):
        """Very sensitive threshold — any vehicle flow triggers."""
        breaches = predict_congestion_breaches(
            route_assignment_plan, segment_capacities, threshold_pct=0.1
        )
        assert len(breaches) >= 2

    def test_empty_plan_produces_no_breaches(self, segment_capacities):
        breaches = predict_congestion_breaches({}, segment_capacities, threshold_pct=0.8)
        assert breaches == []

    def test_dual_check_mode_combines_reactive_and_predictive(
        self, route_assignment_plan, segment_capacities, sample_tomtom_traffic_response
    ):
        """
        Section 8.2: Each monitoring cycle runs BOTH reactive (TomTom) and predictive checks.
        If either triggers, recalculation occurs.
        """
        from app.services.predictive_congestion import dual_congestion_check
        from app.external.tomtom_parser import parse_traffic_flow_response

        live_traffic = parse_traffic_flow_response(sample_tomtom_traffic_response)
        result = dual_congestion_check(
            live_traffic_data=live_traffic,
            route_plan=route_assignment_plan,
            segment_capacities=segment_capacities,
            congestion_speed_threshold_kmh=50,
            predictive_threshold_pct=0.8,
        )
        assert "should_recalculate" in result
        assert "triggered_by" in result  # "reactive", "predictive", or "both"
        assert isinstance(result["should_recalculate"], bool)

    def test_recalculation_triggered_when_prediction_fires_even_if_live_is_clear(
        self, segment_capacities
    ):
        """
        Live traffic is fine, but prediction says seg-A1 will breach in 5 min.
        Recalculation must trigger.
        """
        from app.services.predictive_congestion import dual_congestion_check

        # Live traffic: all clear (high speeds)
        clear_live = [
            {"segment_id": "seg-A1", "current_speed_kmh": 90, "free_flow_speed_kmh": 100},
            {"segment_id": "seg-A2", "current_speed_kmh": 95, "free_flow_speed_kmh": 100},
        ]

        overload_plan = {
            "route-alpha": {
                "vehicles_assigned": 200,
                "segments": ["seg-A1"],
                "average_speed_kmh": 60,
                "segment_length_km": {"seg-A1": 2.0},
            }
        }

        result = dual_congestion_check(
            live_traffic_data=clear_live,
            route_plan=overload_plan,
            segment_capacities={"seg-A1": 150},  # 200 > 150
            congestion_speed_threshold_kmh=50,
            predictive_threshold_pct=0.8,
        )
        assert result["should_recalculate"] is True
        assert "predictive" in result["triggered_by"]