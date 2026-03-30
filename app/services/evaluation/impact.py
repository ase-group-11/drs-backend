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
    # Fire: highly localised — even a large structural fire affects a few blocks
    "fire":       {"low": 0.3,  "medium": 0.5,  "high": 1.0,  "critical": 2.0},

    # Flood: road-level impact, not regional — Churchtown flood blocks nearby streets
    "flood":      {"low": 0.5,  "medium": 1.0,  "high": 2.0,  "critical": 3.0},

    # Earthquake: Ireland has minor seismic activity only — city-scale at most
    "earthquake": {"low": 1.0,  "medium": 3.0,  "high": 5.0,  "critical": 8.0},

    # Hurricane: Ireland gets severe storms, not true hurricanes — affects wider area
    "hurricane":  {"low": 3.0,  "medium": 5.0,  "high": 8.0,  "critical": 12.0},

    # Tornado: very rare in Ireland, tight damage path
    "tornado":    {"low": 0.3,  "medium": 0.8,  "high": 2.0,  "critical": 4.0},

    # Tsunami: coastal only — inland penetration limited in Dublin Bay area
    "tsunami":    {"low": 1.0,  "medium": 2.0,  "high": 4.0,  "critical": 6.0},

    # Drought/heatwave/coldwave: regional phenomena — not useful as point-source
    # radii for city response, keep small for notification targeting only
    "drought":    {"low": 1.0,  "medium": 2.0,  "high": 3.0,  "critical": 5.0},
    "heatwave":   {"low": 1.0,  "medium": 2.0,  "high": 3.0,  "critical": 5.0},
    "coldwave":   {"low": 1.0,  "medium": 2.0,  "high": 3.0,  "critical": 5.0},

    # Storm: affects roads and infrastructure across a wider city area
    "storm":      {"low": 1.0,  "medium": 2.0,  "high": 4.0,  "critical": 6.0},

    # Other: default to flood-like values
    "other":      {"low": 0.5,  "medium": 1.0,  "high": 2.0,  "critical": 3.0},
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
