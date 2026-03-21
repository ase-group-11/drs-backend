"""
Fetch real disaster events from the GDACS API and map them to the 24-feature format.

Usage:
    python scripts/fetch_gdacs_data.py

Output:
    data/gdacs_events.csv  — same 24-column + label schema as synthetic.csv

The script pulls events for each supported event type (FL, EQ, TC, WF, VO, DR),
maps GDACS alert levels to severity labels, infers boolean flags from event type,
builds the feature vector using the existing build_feature_vector() function, and
writes the result to CSV. Events with missing coordinates or alert level are skipped.

GDACS alert level → severity label mapping:
    Green  → LOW
    Orange → MEDIUM
    Red    → HIGH
    If deaths > 50 or total_affected > 10000 → upgrade to CRITICAL
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

try:
    from gdacs.api import GDACSAPIReader
except ImportError:
    print("ERROR: gdacs-api not installed.")
    print("Run:  pip install gdacs-api")
    sys.exit(1)

from app.db.models.enums import DisasterSeverity
from app.services.evaluation.base import EvaluationContext
from app.services.evaluation.features import build_feature_vector, FEATURE_NAMES

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# GDACS event type codes → our disaster_type strings
_GDACS_TYPE_MAP: dict[str, str] = {
    "FL": "flood",
    "EQ": "earthquake",
    "TC": "hurricane",   # Tropical Cyclone
    "WF": "fire",        # Wildfire
    "VO": "other",       # Volcano — no exact match, map to other
    "DR": "drought",
    "TS": "tsunami",
    "SS": "storm",
}

# GDACS alert level → base severity
_ALERT_LEVEL_MAP: dict[str, str] = {
    "Green":  "LOW",
    "Orange": "MEDIUM",
    "Red":    "HIGH",
}

# Event types where structural damage is likely
_STRUCTURAL_DAMAGE_TYPES = {"earthquake", "hurricane", "tsunami", "flood"}

# Event types where road blockage is likely
_ROAD_BLOCKED_TYPES = {"flood", "earthquake", "hurricane", "tsunami", "storm"}

OUTPUT_PATH = PROJECT_ROOT / "data" / "gdacs_events.csv"

# GDACS event types to fetch
_EVENT_TYPES = ["FL", "EQ", "TC", "WF", "VO", "DR"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_alert_to_severity(alert_level: str, deaths: int, total_affected: int) -> str:
    """Map GDACS alert level + impact numbers to a severity label."""
    base = _ALERT_LEVEL_MAP.get(alert_level, "LOW")

    # Upgrade to CRITICAL for mass-casualty / mass-displacement events
    if deaths > 50 or total_affected > 10000:
        return "CRITICAL"

    return base


def _infer_flags(disaster_type: str, severity: str) -> tuple[bool, bool, bool]:
    """
    Infer boolean flags from disaster type and severity.

    Returns (multiple_casualties, structural_damage, road_blocked).
    """
    multiple_casualties = severity in ("HIGH", "CRITICAL")
    structural_damage = disaster_type in _STRUCTURAL_DAMAGE_TYPES and severity in ("HIGH", "CRITICAL")
    road_blocked = disaster_type in _ROAD_BLOCKED_TYPES and severity in ("MEDIUM", "HIGH", "CRITICAL")
    return multiple_casualties, structural_damage, road_blocked


def _parse_hour(date_str: Any) -> int:
    """Extract UTC hour from a GDACS date string or datetime object."""
    if date_str is None:
        return 0
    if isinstance(date_str, datetime):
        return date_str.utctimetuple().tm_hour
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        return dt.hour
    except (ValueError, TypeError):
        return 0


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely coerce a value to int."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely coerce a value to float."""
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def fetch_events() -> list[dict]:
    """Fetch GDACS events for all supported event types and return raw dicts."""
    client = GDACSAPIReader()
    all_events: list[dict] = []

    for event_type in _EVENT_TYPES:
        print(f"  Fetching {event_type} events …", end=" ", flush=True)
        try:
            events = client.latest_events(event_type=event_type)
            count = len(events.features) if hasattr(events, "features") else 0
            all_events.extend(events.features if hasattr(events, "features") else [])
            print(f"{count} events")
        except Exception as exc:
            print(f"SKIPPED ({exc})")

    return all_events


def process_event(feature: Any) -> dict | None:
    """
    Convert a single GDACS GeoJSON feature to a flat row dict.

    Returns None if the event cannot be mapped (missing coordinates / alert level).
    """
    props = feature.get("properties", {}) if isinstance(feature, dict) else {}
    geometry = feature.get("geometry", {}) if isinstance(feature, dict) else {}

    # Coordinates
    coords = geometry.get("coordinates") if geometry else None
    if not coords or len(coords) < 2:
        return None
    lon, lat = _safe_float(coords[0]), _safe_float(coords[1])

    # Event type → disaster_type
    gdacs_type = str(props.get("eventtype", "")).upper()
    disaster_type = _GDACS_TYPE_MAP.get(gdacs_type, "other")

    # Alert level → severity
    alert_level = str(props.get("alertlevel", "")).strip()
    if alert_level not in _ALERT_LEVEL_MAP:
        return None  # skip events with unknown alert level

    deaths = _safe_int(props.get("deaths", 0))
    total_affected = _safe_int(props.get("population", {}).get("valueTo", 0) if isinstance(props.get("population"), dict) else props.get("total_affected", 0))
    people_affected = max(deaths, total_affected)

    severity_str = _map_alert_to_severity(alert_level, deaths, total_affected)

    # Parse hour from event start date
    hour_of_day = _parse_hour(props.get("fromdate"))

    # Infer flags
    multiple_casualties, structural_damage, road_blocked = _infer_flags(disaster_type, severity_str)

    # Build EvaluationContext (no traffic/weather for historical events)
    try:
        severity_enum = DisasterSeverity(severity_str.lower())
    except ValueError:
        severity_enum = DisasterSeverity.LOW

    context = EvaluationContext(
        report_id="gdacs",
        disaster_type=disaster_type,
        severity=severity_enum,
        description=str(props.get("htmldescription", "")),
        people_affected=people_affected,
        multiple_casualties=multiple_casualties,
        structural_damage=structural_damage,
        road_blocked=road_blocked,
        lat=lat,
        lon=lon,
        hour_of_day=hour_of_day,
        traffic_context=None,
        weather_context=None,
    )

    feature_vector = build_feature_vector(context)
    row = dict(zip(FEATURE_NAMES, feature_vector))
    row["label"] = severity_str
    return row


def main() -> None:
    print("Fetching GDACS disaster events …")
    raw_features = fetch_events()
    print(f"Total raw events fetched: {len(raw_features)}")

    rows: list[dict] = []
    skipped = 0
    for feature in raw_features:
        row = process_event(feature)
        if row is not None:
            rows.append(row)
        else:
            skipped += 1

    print(f"Processed: {len(rows)} events  |  Skipped (missing data): {skipped}")

    if not rows:
        print("WARNING: No events could be mapped. Output file not written.")
        return

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=FEATURE_NAMES + ["label"])

    # Label distribution
    print("\nLabel distribution:")
    for label, count in df["label"].value_counts().items():
        print(f"  {label:8s}: {count:5d}  ({100 * count / len(df):.1f}%)")

    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved {len(df)} rows → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
