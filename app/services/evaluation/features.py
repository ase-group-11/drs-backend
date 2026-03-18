"""
Feature engineering for the XGBoost severity classifier.

Single public API:
    build_feature_vector(context: EvaluationContext) -> list[float]

Also exports FEATURE_NAMES: list[str] — 29 human-readable names in the
same fixed order as the feature vector. Consumed by train_model.py and
SHAP analysis.

Feature vector layout (29 features, order is fixed — changing it
invalidates any trained artifact):

  [0..10]   disaster_type one-hot (11 types, alphabetical per plan)
  [11]      severity_ordinal         LOW=1, MEDIUM=2, HIGH=3, CRITICAL=4
  [12]      multiple_casualties      0/1
  [13]      structural_damage        0/1
  [14]      road_blocked             0/1
  [15]      people_affected_log      log1p(people_affected)
  [16]      hour_sin                 sin(2π * hour / 24)
  [17]      hour_cos                 cos(2π * hour / 24)
  [18]      traffic_congestion_score
  [19]      temperature_c
  [20]      wind_speed_kmh
  [21]      weather_condition_score
  [22]      population_density_tier
  [23]      reporter_credibility     (hardcoded 1.0)
  [24]      nearby_report_count      capped at 5 — sliding window signal
  [25]      historical_false_alarm_rate  0.0–1.0 — area credibility
  [26]      camera_count_nearby      capped at 5 — surveillance signal
  [27]      photo_count              capped at 5 — evidence quality
  [28]      description_length_tier  0 (<20 chars), 1 (<100), 2 (<300), 3 (300+)
"""

from __future__ import annotations

import math
from typing import List

from app.db.models.enums import DisasterSeverity
from app.services.evaluation.base import EvaluationContext

# ---------------------------------------------------------------------------
# Ordered disaster types for one-hot encoding (positions 0–10)
# ---------------------------------------------------------------------------

_DISASTER_TYPE_ORDER: List[str] = [
    "fire",
    "flood",
    "earthquake",
    "hurricane",
    "tornado",
    "tsunami",
    "drought",
    "heatwave",
    "coldwave",
    "storm",
    "other",
]

# ---------------------------------------------------------------------------
# Severity ordinal mapping
# ---------------------------------------------------------------------------

_SEVERITY_ORDINAL: dict[DisasterSeverity, float] = {
    DisasterSeverity.LOW:      1.0,
    DisasterSeverity.MEDIUM:   2.0,
    DisasterSeverity.HIGH:     3.0,
    DisasterSeverity.CRITICAL: 4.0,
}

# ---------------------------------------------------------------------------
# Traffic congestion → numeric score
# ---------------------------------------------------------------------------

_CONGESTION_SCORE: dict[str, float] = {
    "unknown":  0.0,
    "light":    1.0,
    "moderate": 2.0,
    "heavy":    3.0,
    "severe":   4.0,
}

# ---------------------------------------------------------------------------
# Weather condition → numeric score
# ---------------------------------------------------------------------------

_WEATHER_CONDITION_SCORE: dict[str, float] = {
    "clear":    0.0,
    "cloudy":   1.0,
    "overcast": 1.0,
    "rain":     2.0,
    "storm":    3.0,
}

# ---------------------------------------------------------------------------
# Human-readable feature names (same order as the vector)
# ---------------------------------------------------------------------------

FEATURE_NAMES: List[str] = [
    "disaster_type_fire",
    "disaster_type_flood",
    "disaster_type_earthquake",
    "disaster_type_hurricane",
    "disaster_type_tornado",
    "disaster_type_tsunami",
    "disaster_type_drought",
    "disaster_type_heatwave",
    "disaster_type_coldwave",
    "disaster_type_storm",
    "disaster_type_other",
    "severity_ordinal",
    "multiple_casualties",
    "structural_damage",
    "road_blocked",
    "people_affected_log",
    "hour_sin",
    "hour_cos",
    "traffic_congestion_score",
    "temperature_c",
    "wind_speed_kmh",
    "weather_condition_score",
    "population_density_tier",
    "reporter_credibility",
    "nearby_report_count",
    "historical_false_alarm_rate",
    "camera_count_nearby",
    "photo_count",
    "description_length_tier",
]

assert len(FEATURE_NAMES) == 29, "FEATURE_NAMES must have exactly 29 entries"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_feature_vector(context: EvaluationContext) -> List[float]:
    """
    Convert an EvaluationContext into a fixed-length numeric feature vector.

    The vector always has exactly 24 elements in the order documented above.
    This function is pure (no I/O, no side effects) so it can be unit-tested
    in isolation.
    """
    vec: List[float] = []

    # [0..10] disaster_type one-hot
    dtype = (context.disaster_type or "other").lower()
    for t in _DISASTER_TYPE_ORDER:
        vec.append(1.0 if dtype == t else 0.0)

    # [11] severity_ordinal
    vec.append(_SEVERITY_ORDINAL.get(context.severity, 1.0))

    # [12..14] boolean flags
    vec.append(1.0 if context.multiple_casualties else 0.0)
    vec.append(1.0 if context.structural_damage else 0.0)
    vec.append(1.0 if context.road_blocked else 0.0)

    # [15] people_affected_log
    vec.append(math.log1p(max(0, context.people_affected or 0)))

    # [16..17] cyclical hour encoding
    hour = context.hour_of_day or 0
    vec.append(math.sin(2 * math.pi * hour / 24))
    vec.append(math.cos(2 * math.pi * hour / 24))

    # [18] traffic_congestion_score
    vec.append(_traffic_congestion_score(context.traffic_context))

    # [19..21] weather features
    temp_c, wind_kmh, weather_score = _weather_features(context.weather_context)
    vec.append(temp_c)
    vec.append(wind_kmh)
    vec.append(weather_score)

    # [22] population_density_tier
    vec.append(_population_density_tier(context.lat, context.lon))

    # [23] reporter_credibility
    vec.append(1.0)

    # [24] nearby_report_count — sliding window signal, capped at 5
    vec.append(min(float(context.nearby_report_count or 0), 5.0))

    # [25] historical_false_alarm_rate — area credibility signal
    hist = context.historical_context or {}
    vec.append(float(hist.get("false_alarm_rate", 0.0)))

    # [26] camera_count_nearby — surveillance signal, capped at 5
    surv = context.surveillance_context or {}
    vec.append(min(float(surv.get("camera_count", 0)), 5.0))

    # [27] photo_count — evidence quality, capped at 5
    vec.append(min(float(context.photo_count or 0), 5.0))

    # [28] description_length_tier
    dl = context.description_length or 0
    if dl >= 300:
        tier = 3.0
    elif dl >= 100:
        tier = 2.0
    elif dl >= 20:
        tier = 1.0
    else:
        tier = 0.0
    vec.append(tier)

    assert len(vec) == 29, f"Feature vector has {len(vec)} elements, expected 29"
    return vec


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _traffic_congestion_score(traffic_ctx: dict | None) -> float:
    """Extract numeric congestion score from traffic context."""
    if not traffic_ctx:
        return 0.0
    flow = traffic_ctx.get("flow", [])
    if not flow:
        return 0.0
    congestion_level = flow[0].get("congestion_level", "unknown")
    return _CONGESTION_SCORE.get((congestion_level or "unknown").lower(), 0.0)


def _weather_features(weather_ctx: dict | None) -> tuple[float, float, float]:
    """Extract (temperature_c, wind_speed_kmh, condition_score) from weather context."""
    if not weather_ctx:
        return 0.0, 0.0, 0.0
    temp = float(weather_ctx.get("temperature_c") or 0.0)
    wind = float(weather_ctx.get("wind_speed_kmh") or 0.0)
    condition = (weather_ctx.get("condition") or "clear").lower()
    condition_score = _WEATHER_CONDITION_SCORE.get(condition, 0.0)
    return temp, wind, condition_score


def _population_density_tier(lat: float | None, lon: float | None) -> float:
    """
    Return a population density tier for Dublin-area coordinates.

    Tiers:
        3 — Dublin city centre    (53.32–53.38°N, -6.30– -6.22°W)
        2 — Inner suburbs         (53.28–53.42°N, -6.40– -6.15°W)
        1 — Outer Dublin          (53.20–53.55°N, -6.55– -6.00°W)
        0 — Outside / unknown
    """
    if lat is None or lon is None:
        return 0.0

    # City centre
    if 53.32 <= lat <= 53.38 and -6.30 <= lon <= -6.22:
        return 3.0

    # Inner suburbs
    if 53.28 <= lat <= 53.42 and -6.40 <= lon <= -6.15:
        return 2.0

    # Outer Dublin
    if 53.20 <= lat <= 53.55 and -6.55 <= lon <= -6.00:
        return 1.0

    return 0.0



