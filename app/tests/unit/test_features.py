"""
Unit tests for app/services/evaluation/features.py

Verifies that build_feature_vector() produces correct 24-element vectors
for every edge case documented in the plan.
"""

from __future__ import annotations

import math

import pytest

from app.db.models.enums import DisasterSeverity, DisasterType
from app.services.evaluation.base import EvaluationContext
from app.services.evaluation.features import FEATURE_NAMES, build_feature_vector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DISASTER_TYPE_ORDER = [
    "fire", "flood", "earthquake", "hurricane", "tornado",
    "tsunami", "drought", "heatwave", "coldwave", "storm", "other",
]


def make_context(**overrides) -> EvaluationContext:
    """Minimal EvaluationContext with Dublin city-centre defaults."""
    defaults = dict(
        report_id="test-id",
        disaster_type=DisasterType.FIRE.value,
        severity=DisasterSeverity.MEDIUM,
        description="test",
        people_affected=10,
        multiple_casualties=False,
        structural_damage=False,
        road_blocked=False,
        lat=53.35,
        lon=-6.26,
        hour_of_day=12,
        traffic_context=None,
        weather_context=None,
    )
    defaults.update(overrides)
    return EvaluationContext(**defaults)


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


def test_vector_length():
    """Feature vector must always have exactly 24 elements."""
    vec = build_feature_vector(make_context())
    assert len(vec) == 24


def test_feature_names_length():
    """FEATURE_NAMES must have exactly 24 entries."""
    assert len(FEATURE_NAMES) == 24


# ---------------------------------------------------------------------------
# One-hot encoding for disaster types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("disaster_type", _DISASTER_TYPE_ORDER)
def test_disaster_type_onehot_single_one(disaster_type: str):
    """Each disaster type must produce exactly one 1 in positions 0–10."""
    ctx = make_context(disaster_type=disaster_type)
    vec = build_feature_vector(ctx)
    onehot = vec[0:11]
    assert sum(onehot) == 1.0, f"Expected exactly one 1 for {disaster_type}"
    assert onehot[_DISASTER_TYPE_ORDER.index(disaster_type)] == 1.0


def test_disaster_type_onehot_unique_per_type():
    """Each of the 11 types produces a unique one-hot pattern."""
    patterns = set()
    for dt in _DISASTER_TYPE_ORDER:
        ctx = make_context(disaster_type=dt)
        vec = build_feature_vector(ctx)
        pattern = tuple(vec[0:11])
        patterns.add(pattern)
    assert len(patterns) == 11, "All 11 disaster types must have distinct one-hot vectors"


# ---------------------------------------------------------------------------
# Severity ordinal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("severity,expected", [
    (DisasterSeverity.LOW,      1.0),
    (DisasterSeverity.MEDIUM,   2.0),
    (DisasterSeverity.HIGH,     3.0),
    (DisasterSeverity.CRITICAL, 4.0),
])
def test_severity_ordinal(severity: DisasterSeverity, expected: float):
    ctx = make_context(severity=severity)
    vec = build_feature_vector(ctx)
    assert vec[11] == expected


# ---------------------------------------------------------------------------
# Boolean flags
# ---------------------------------------------------------------------------


def test_boolean_flags_off():
    ctx = make_context(multiple_casualties=False, structural_damage=False, road_blocked=False)
    vec = build_feature_vector(ctx)
    assert vec[12] == 0.0
    assert vec[13] == 0.0
    assert vec[14] == 0.0


def test_boolean_flags_on():
    ctx = make_context(multiple_casualties=True, structural_damage=True, road_blocked=True)
    vec = build_feature_vector(ctx)
    assert vec[12] == 1.0
    assert vec[13] == 1.0
    assert vec[14] == 1.0


# ---------------------------------------------------------------------------
# people_affected_log
# ---------------------------------------------------------------------------


def test_people_affected_zero():
    """log1p(0) == 0.0"""
    ctx = make_context(people_affected=0)
    vec = build_feature_vector(ctx)
    assert vec[15] == pytest.approx(0.0)


def test_people_affected_positive():
    """log1p(10) == math.log1p(10)"""
    ctx = make_context(people_affected=10)
    vec = build_feature_vector(ctx)
    assert vec[15] == pytest.approx(math.log1p(10))


# ---------------------------------------------------------------------------
# Cyclical hour encoding
# ---------------------------------------------------------------------------


def test_hour_zero_encoding():
    """hour=0 → sin=0, cos=1"""
    ctx = make_context(hour_of_day=0)
    vec = build_feature_vector(ctx)
    assert vec[16] == pytest.approx(0.0, abs=1e-9)   # sin(0)
    assert vec[17] == pytest.approx(1.0)              # cos(0)


def test_hour_noon_encoding():
    """hour=12 → sin(π)≈0, cos(π)≈-1"""
    ctx = make_context(hour_of_day=12)
    vec = build_feature_vector(ctx)
    assert vec[16] == pytest.approx(math.sin(math.pi), abs=1e-9)
    assert vec[17] == pytest.approx(-1.0)


def test_hour_cyclical_wraparound():
    """hour=23 and hour=1 should be closer to each other than hour=23 and hour=12.

    Cyclical proximity is measured in 2D (sin, cos) space — both components
    are needed since hours on opposite sides of midnight have similar sin values
    but their cos values reveal they are close in the cycle.
    """
    def euclidean(v1, v2):
        return math.sqrt((v1[16] - v2[16]) ** 2 + (v1[17] - v2[17]) ** 2)

    vec23 = build_feature_vector(make_context(hour_of_day=23))
    vec01 = build_feature_vector(make_context(hour_of_day=1))
    vec12 = build_feature_vector(make_context(hour_of_day=12))

    dist_23_to_01 = euclidean(vec23, vec01)   # 2 hours apart across midnight
    dist_23_to_12 = euclidean(vec23, vec12)   # 11 hours apart

    assert dist_23_to_01 < dist_23_to_12, (
        f"hour=23 should be closer to hour=1 ({dist_23_to_01:.4f}) "
        f"than to hour=12 ({dist_23_to_12:.4f}) in cyclical space"
    )


# ---------------------------------------------------------------------------
# Traffic congestion score
# ---------------------------------------------------------------------------


def test_none_traffic_context():
    """None traffic_context → traffic_congestion_score == 0.0"""
    ctx = make_context(traffic_context=None)
    vec = build_feature_vector(ctx)
    assert vec[18] == 0.0


def test_empty_flow_traffic_context():
    """Empty flow list → 0.0"""
    ctx = make_context(traffic_context={"flow": [], "source": "test"})
    vec = build_feature_vector(ctx)
    assert vec[18] == 0.0


@pytest.mark.parametrize("level,expected_score", [
    ("light",    1.0),
    ("moderate", 2.0),
    ("heavy",    3.0),
    ("severe",   4.0),
    ("unknown",  0.0),
])
def test_traffic_congestion_levels(level: str, expected_score: float):
    traffic_ctx = {"flow": [{"congestion_level": level}], "source": "test"}
    ctx = make_context(traffic_context=traffic_ctx)
    vec = build_feature_vector(ctx)
    assert vec[18] == expected_score


# ---------------------------------------------------------------------------
# Weather features
# ---------------------------------------------------------------------------


def test_none_weather_context():
    """None weather_context → temperature=0.0, wind=0.0, condition=0.0"""
    ctx = make_context(weather_context=None)
    vec = build_feature_vector(ctx)
    assert vec[19] == 0.0   # temperature_c
    assert vec[20] == 0.0   # wind_speed_kmh
    assert vec[21] == 0.0   # weather_condition_score


def test_weather_values_propagated():
    weather_ctx = {
        "temperature_c": 18.5,
        "wind_speed_kmh": 45.0,
        "condition": "storm",
        "source": "test",
    }
    ctx = make_context(weather_context=weather_ctx)
    vec = build_feature_vector(ctx)
    assert vec[19] == pytest.approx(18.5)
    assert vec[20] == pytest.approx(45.0)
    assert vec[21] == 3.0   # storm → 3


@pytest.mark.parametrize("condition,expected", [
    ("clear",    0.0),
    ("cloudy",   1.0),
    ("overcast", 1.0),
    ("rain",     2.0),
    ("storm",    3.0),
])
def test_weather_condition_scores(condition: str, expected: float):
    weather_ctx = {"temperature_c": 10.0, "wind_speed_kmh": 10.0, "condition": condition}
    ctx = make_context(weather_context=weather_ctx)
    vec = build_feature_vector(ctx)
    assert vec[21] == expected


# ---------------------------------------------------------------------------
# Population density tier
# ---------------------------------------------------------------------------


def test_none_lat_lon():
    """None lat/lon → population_density_tier == 0"""
    ctx = make_context(lat=None, lon=None)
    vec = build_feature_vector(ctx)
    assert vec[22] == 0.0


def test_city_centre_tier_3():
    """Dublin city centre → tier 3"""
    ctx = make_context(lat=53.35, lon=-6.26)
    vec = build_feature_vector(ctx)
    assert vec[22] == 3.0


def test_inner_suburb_tier_2():
    """Inner suburb (outside city centre, within inner bounds) → tier 2"""
    ctx = make_context(lat=53.30, lon=-6.38)   # south-west inner suburb
    vec = build_feature_vector(ctx)
    assert vec[22] == 2.0


def test_outer_dublin_tier_1():
    """Outer Dublin → tier 1"""
    ctx = make_context(lat=53.22, lon=-6.50)
    vec = build_feature_vector(ctx)
    assert vec[22] == 1.0


def test_outside_dublin_tier_0():
    """Far outside Dublin → tier 0"""
    ctx = make_context(lat=52.00, lon=-8.00)   # Cork area
    vec = build_feature_vector(ctx)
    assert vec[22] == 0.0


# ---------------------------------------------------------------------------
# reporter_credibility (always 1.0 in Phase 2)
# ---------------------------------------------------------------------------


def test_reporter_credibility():
    ctx = make_context()
    vec = build_feature_vector(ctx)
    assert vec[23] == 1.0
