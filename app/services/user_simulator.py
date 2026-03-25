"""
app/services/user_simulator.py

UserSimulator — simulated vehicle pool for dev, testing, and load testing.

Section 10 (Mocking Strategy — Simulating Concurrent Users).

Each simulated user has:
  - userId
  - currentLocation (lat/lng)
  - destination
  - currentRoute
  - complianceRate  (prepared for Innovation 5)

Supports:
  - Bulk registration of N users in a region
  - Region-based queries (which users are in the affected area)
  - Batch route assignment updates
  - Thread-safe async dictionary storage
"""

import uuid
import logging
import random
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# Dublin bounding box — simulated users are placed within this area by default
DUBLIN_BOUNDS = {
    "lat_min": 53.2800,
    "lat_max": 53.4100,
    "lng_min": -6.4500,
    "lng_max": -6.1000,
}


class UserSimulator:
    """
    In-memory simulated user pool.

    Used in:
    - Unit tests (10–50 users via factory methods)
    - Integration tests (in-memory pool)
    - Load tests (200–500 users, replaced by Locust Socket.IO agents)
    """

    def __init__(self):
        # user_id → user dict
        self._users: Dict[str, Dict[str, Any]] = {}

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    def register_user(
        self,
        user_id: Optional[str] = None,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        dest_lat: Optional[float] = None,
        dest_lng: Optional[float] = None,
        vehicle_type: str = "general",
        compliance_rate: float = 0.85,
    ) -> Dict[str, Any]:
        """Register a single simulated user."""
        uid = user_id or f"sim-{uuid.uuid4().hex[:8]}"
        user = {
            "user_id": uid,
            "current_location": {
                "lat": lat if lat is not None else random.uniform(
                    DUBLIN_BOUNDS["lat_min"], DUBLIN_BOUNDS["lat_max"]
                ),
                "lng": lng if lng is not None else random.uniform(
                    DUBLIN_BOUNDS["lng_min"], DUBLIN_BOUNDS["lng_max"]
                ),
            },
            "destination": {
                "lat": dest_lat if dest_lat is not None else random.uniform(
                    DUBLIN_BOUNDS["lat_min"], DUBLIN_BOUNDS["lat_max"]
                ),
                "lng": dest_lng if dest_lng is not None else random.uniform(
                    DUBLIN_BOUNDS["lng_min"], DUBLIN_BOUNDS["lng_max"]
                ),
            },
            "current_route": None,
            "type": vehicle_type,
            "compliance_rate": compliance_rate,
            "status": "active",
        }
        self._users[uid] = user
        return user

    def bulk_register(
        self,
        n: int,
        vehicle_type_distribution: Optional[Dict[str, float]] = None,
        region_bounds: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Register N simulated users.

        Args:
            n: Number of users to register
            vehicle_type_distribution: e.g. {"general": 0.85, "public_transport": 0.10, "emergency": 0.05}
            region_bounds: Override the default Dublin bounds
        """
        distribution = vehicle_type_distribution or {
            "general": 0.85,
            "public_transport": 0.10,
            "emergency": 0.05,
        }
        bounds = region_bounds or DUBLIN_BOUNDS

        # Build weighted type list
        types = []
        for vtype, weight in distribution.items():
            types.extend([vtype] * int(weight * 1000))

        # Exactly 3 fixed destinations — round-robin ensures even split.
        # No jitter — exact coordinates so set deduplication gives exactly 3 clusters
        # which means exactly 3 TomTom routing calls (avoids 429 rate limits).
        COMMON_DESTINATIONS = [
            {"lat": 53.3498, "lng": -6.2603},  # Dublin City Centre      (~33%)
            {"lat": 53.3800, "lng": -6.4400},  # Blanchardstown (NW)     (~33%)
            {"lat": 53.4200, "lng": -6.2700},  # Dublin Airport (N)      (~33%)
        ]

        registered = []
        for i in range(n):
            # Round-robin: vehicle 0→City, 1→Blanchardstown, 2→Airport, 3→City ...
            dest = COMMON_DESTINATIONS[i % len(COMMON_DESTINATIONS)]

            user = self.register_user(
                lat=random.uniform(53.275, 53.310),   # Southwest Dublin / Tallaght
                lng=random.uniform(-6.415, -6.340),   # West of M50
                dest_lat=dest["lat"],
                dest_lng=dest["lng"],
                vehicle_type=random.choice(types) if types else "general",
                compliance_rate=random.uniform(0.70, 1.0),
            )
            registered.append(user)

        logger.info(f"UserSimulator: registered {n} users")
        return registered

    # -------------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------------

    def get_users_in_region(
        self, region_bounds: Optional[Dict[str, float]] = None
    ) -> List[Dict[str, Any]]:
        """
        Return all users whose current location falls within the given bounds.
        Defaults to all registered users if no bounds provided.
        """
        if not region_bounds:
            return list(self._users.values())

        return [
            u for u in self._users.values()
            if (
                region_bounds["lat_min"] <= u["current_location"]["lat"] <= region_bounds["lat_max"]
                and region_bounds["lng_min"] <= u["current_location"]["lng"] <= region_bounds["lng_max"]
            )
        ]

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Return all registered users."""
        return list(self._users.values())

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Return a single user by ID."""
        return self._users.get(user_id)

    def count(self) -> int:
        return len(self._users)

    # -------------------------------------------------------------------------
    # Route assignment updates
    # -------------------------------------------------------------------------

    def assign_routes(self, route_assignments: Dict[str, str]) -> int:
        """
        Batch-assign routes to users.

        Args:
            route_assignments: {user_id: route_id}

        Returns:
            Number of users updated.
        """
        updated = 0
        for user_id, route_id in route_assignments.items():
            if user_id in self._users:
                self._users[user_id]["current_route"] = route_id
                updated += 1
        logger.info(f"UserSimulator: assigned routes to {updated} users")
        return updated

    def clear_routes(self) -> None:
        """Clear all route assignments (e.g. on disaster clearance)."""
        for user in self._users.values():
            user["current_route"] = None

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all registered users. Used between test runs."""
        self._users.clear()
        logger.info("UserSimulator: reset — all users cleared")

    def summary(self) -> Dict[str, Any]:
        """Return a summary of the current simulator state."""
        types: Dict[str, int] = {}
        routed = 0
        for u in self._users.values():
            types[u["type"]] = types.get(u["type"], 0) + 1
            if u["current_route"]:
                routed += 1
        return {
            "total_users": len(self._users),
            "routed": routed,
            "unrouted": len(self._users) - routed,
            "by_type": types,
        }


# ---------------------------------------------------------------------------
# Module-level singleton for use in tests and Scenario Engine
# ---------------------------------------------------------------------------

user_simulator = UserSimulator()