"""
Synthetic training data generator for the XGBoost severity classifier.

Usage:
    python scripts/generate_training_data.py

Output:
    data/synthetic.csv  — 10,000 rows, 30 feature columns + label

This script is standalone (no FastAPI imports). It constructs
EvaluationContext objects, runs them through RulesEngineStrategy to get
ground-truth labels, applies 15% label noise, then writes the CSV.

Run this before train_model.py.
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so app imports work without installing
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
import csv
import math

from app.db.models.enums import DisasterSeverity, DisasterType
from app.services.evaluation.base import EvaluationContext
from app.services.evaluation.features import FEATURE_NAMES, build_feature_vector
from app.services.evaluation.rules_engine import RulesEngineStrategy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RANDOM_SEED = 42
N_ROWS = 10_000
NOISE_RATE = 0.15   # 15% of labels get randomly shifted by ±1 tier

OUTPUT_PATH = PROJECT_ROOT / "data" / "synthetic.csv"

DISASTER_TYPES = [dt.value for dt in DisasterType]

# Weighted severity distribution — realistic: mostly LOW/MEDIUM
_SEVERITY_WEIGHTS = {
    DisasterSeverity.LOW:      0.40,
    DisasterSeverity.MEDIUM:   0.35,
    DisasterSeverity.HIGH:     0.17,
    DisasterSeverity.CRITICAL: 0.08,   # CRITICAL weighted 2× relative to raw 4%
}
_SEVERITY_POPULATION = list(_SEVERITY_WEIGHTS.keys())
_SEVERITY_WEIGHTS_LIST = list(_SEVERITY_WEIGHTS.values())

_SEVERITY_ORDER = [
    DisasterSeverity.LOW,
    DisasterSeverity.MEDIUM,
    DisasterSeverity.HIGH,
    DisasterSeverity.CRITICAL,
]

CONGESTION_LEVELS = [None, "light", "moderate", "heavy", "severe"]
WEATHER_CONDITIONS = ["clear", "cloudy", "overcast", "rain", "storm"]

# Dublin bounding box for lat/lon sampling
LAT_MIN, LAT_MAX = 53.28, 53.42
LON_MIN, LON_MAX = -6.40, -6.15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _random_people_affected(rng: random.Random) -> int:
    """Right-skewed: most events affect few people, some affect many."""
    # Use exponential distribution approximated with random.expovariate
    raw = rng.expovariate(1 / 20)  # mean ~20
    return min(200, int(raw))


def _build_context(rng: random.Random, row_id: int) -> EvaluationContext:
    disaster_type = rng.choice(DISASTER_TYPES)
    severity = rng.choices(_SEVERITY_POPULATION, weights=_SEVERITY_WEIGHTS_LIST, k=1)[0]

    lat = rng.uniform(LAT_MIN, LAT_MAX)
    lon = rng.uniform(LON_MIN, LON_MAX)

    # Build traffic context ~70% of the time
    traffic_ctx = None
    if rng.random() < 0.70:
        congestion = rng.choice(CONGESTION_LEVELS)
        if congestion is not None:
            traffic_ctx = {"flow": [{"congestion_level": congestion}], "source": "synthetic"}

    # Build weather context ~70% of the time
    weather_ctx = None
    if rng.random() < 0.70:
        weather_ctx = {
            "temperature_c": rng.uniform(5, 25),
            "wind_speed_kmh": rng.uniform(0, 60),
            "condition": rng.choice(WEATHER_CONDITIONS),
            "source": "synthetic",
        }

    # [24] nearby_report_count — 0 most of the time, occasionally corroborated
    nearby_report_count = 0
    if rng.random() < 0.25:
        nearby_report_count = rng.randint(1, 6)   # features.py caps at 5

    # [25] historical_false_alarm_rate — area credibility, mostly low
    false_alarm_rate = round(rng.betavariate(1.5, 8.0), 3)  # skewed toward 0

    # [26] camera_count_nearby — surveillance coverage
    # [29] surveillance_quality_score — composite camera quality
    camera_count = 0
    surveillance_quality = 0.0
    if rng.random() < 0.60:
        camera_count = rng.randint(0, 7)  # features.py caps at 5
        if camera_count > 0:
            # Quality correlated with count but with variance
            base_quality = min(1.0, camera_count / 5.0) * 0.4 + 0.3
            surveillance_quality = round(min(1.0, base_quality + rng.uniform(-0.15, 0.20)), 4)

    # [27] photo_count — evidence quality, ~40% of reports have photos
    photo_count = 0
    if rng.random() < 0.40:
        photo_count = rng.randint(1, 6)   # features.py caps at 5

    # [28] description_length — 0–400 chars, realistic reporter effort distribution
    description_length = int(rng.expovariate(1 / 120))  # mean ~120 chars
    description_length = min(500, description_length)

    return EvaluationContext(
        report_id=f"syn-{row_id:06d}",
        disaster_type=disaster_type,
        severity=severity,
        description="synthetic",
        people_affected=_random_people_affected(rng),
        multiple_casualties=rng.random() < 0.30,
        structural_damage=rng.random() < 0.30,
        road_blocked=rng.random() < 0.30,
        lat=lat,
        lon=lon,
        hour_of_day=rng.randint(0, 23),
        traffic_context=traffic_ctx,
        weather_context=weather_ctx,
        nearby_report_count=nearby_report_count,
        historical_context={"false_alarm_rate": false_alarm_rate},
        surveillance_context={"camera_count": camera_count, "surveillance_quality_score": surveillance_quality},
        photo_count=photo_count,
        description_length=description_length,
    )


def _apply_label_noise(severity_label: str, rng: random.Random) -> str:
    """Shift label one tier up or down at random (clipped to valid range)."""
    idx = [s.name for s in _SEVERITY_ORDER].index(severity_label)
    direction = rng.choice([-1, 1])
    new_idx = max(0, min(len(_SEVERITY_ORDER) - 1, idx + direction))
    return _SEVERITY_ORDER[new_idx].name


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def generate() -> None:
    rng = random.Random(RANDOM_SEED)
    strategy = RulesEngineStrategy()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    header = FEATURE_NAMES + ["label"]
    rows = []

    print(f"Generating {N_ROWS} synthetic training rows …")

    for i in range(N_ROWS):
        ctx = _build_context(rng, i)
        result = await strategy.evaluate(ctx)
        label = result.severity   # already uppercase

        # 15% label noise
        if rng.random() < NOISE_RATE:
            label = _apply_label_noise(label, rng)

        vec = build_feature_vector(ctx)
        rows.append(vec + [label])

        if (i + 1) % 1000 == 0:
            print(f"  {i + 1}/{N_ROWS} rows done …")

    with OUTPUT_PATH.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)

    print(f"\nDone. Wrote {len(rows)} rows to {OUTPUT_PATH}")

    # Quick label distribution summary
    from collections import Counter
    label_counts = Counter(row[-1] for row in rows)
    print("Label distribution:")
    for sev in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]:
        count = label_counts.get(sev, 0)
        pct = 100 * count / len(rows)
        print(f"  {sev:8s}: {count:5d}  ({pct:.1f}%)")


if __name__ == "__main__":
    asyncio.run(generate())
