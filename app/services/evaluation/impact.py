"""
Impact assessment functions for disaster evaluation.

determineImpactRadius       — impact radius in km from disaster type + severity
estimateAffectedPopulation  — estimated affected population from radius + GeoNames data
"""

from __future__ import annotations

import math
from typing import Optional

# Impact radius (km) keyed by disaster_type value × severity value
_IMPACT_RADIUS_KM: dict[str, dict[str, float]] = {
    "fire":       {"low": 0.5,  "medium": 1.0,  "high": 2.0,  "critical": 5.0},
    "flood":      {"low": 1.0,  "medium": 3.0,  "high": 8.0,  "critical": 15.0},
    "earthquake": {"low": 5.0,  "medium": 15.0, "high": 30.0, "critical": 60.0},
    "hurricane":  {"low": 10.0, "medium": 30.0, "high": 60.0, "critical": 100.0},
    "tornado":    {"low": 0.5,  "medium": 2.0,  "high": 5.0,  "critical": 10.0},
    "tsunami":    {"low": 5.0,  "medium": 15.0, "high": 30.0, "critical": 50.0},
    "drought":    {"low": 10.0, "medium": 30.0, "high": 80.0, "critical": 200.0},
    "heatwave":   {"low": 5.0,  "medium": 20.0, "high": 50.0, "critical": 100.0},
    "coldwave":   {"low": 5.0,  "medium": 20.0, "high": 50.0, "critical": 100.0},
    "storm":      {"low": 5.0,  "medium": 15.0, "high": 30.0, "critical": 60.0},
    "other":      {"low": 1.0,  "medium": 3.0,  "high": 8.0,  "critical": 20.0},
}

# Fallback urban population density (people per km²) — Irish urban context
_URBAN_DENSITY_PER_KM2 = 4500


def _density_from_population(population: int) -> int:
    """
    Derive a population density estimate from the nearest place's population.

    Tiers are based on typical urban/rural densities for an Irish context.
    Used when GeoNames data is available to replace the hardcoded fallback.
    """
    if population > 500_000:
        return 6000   # large city
    if population > 100_000:
        return 4500   # city
    if population > 50_000:
        return 2000   # large town
    if population > 10_000:
        return 800    # town
    return 200        # village / rural


def determine_impact_radius(disaster_type: str, severity: str) -> float:
    """
    Determine impact radius in km for a disaster type and severity level.

    Args:
        disaster_type: Lowercase DisasterType value (e.g. "fire")
        severity: Lowercase DisasterSeverity value (e.g. "high")

    Returns:
        Impact radius in kilometres.
    """
    type_map = _IMPACT_RADIUS_KM.get(disaster_type.lower(), _IMPACT_RADIUS_KM["other"])
    return type_map.get(severity.lower(), 1.0)


def estimate_affected_population(
    radius_km: float,
    reported_people_affected: int,
    population_ctx: Optional[dict] = None,
) -> int:
    """
    Estimate total affected population from impact radius.

    Uses the GeoNames nearest-place population (if available) to derive a
    location-appropriate density instead of the hardcoded fallback.

    The area-based estimate is divided by a penetration factor of 10 to avoid
    unrealistically large numbers for wide-radius events (earthquakes, hurricanes).
    The reporter-provided figure is used as the minimum floor.

    Args:
        radius_km: Impact radius in km (from determine_impact_radius)
        reported_people_affected: Figure from the disaster report (may be 0)
        population_ctx: Optional GeoNames enrichment dict (keys: population, distance_km)

    Returns:
        Estimated number of affected people (always >= 1).
    """
    density = _URBAN_DENSITY_PER_KM2
    if population_ctx:
        geonames_pop = population_ctx.get("population", 0)
        if geonames_pop > 0:
            density = _density_from_population(geonames_pop)

    area_km2 = math.pi * radius_km ** 2
    area_estimate = int(area_km2 * density / 10)
    return max(reported_people_affected, area_estimate, 1)
