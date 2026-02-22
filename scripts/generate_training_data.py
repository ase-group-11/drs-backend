"""
Synthetic training data generator for the XGBoost severity classifier.

Usage:
    python scripts/generate_training_data.py

Output:
    data/synthetic.csv  — 10,000 rows, 24 feature columns + label

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
