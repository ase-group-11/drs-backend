"""
app/providers/integration_service.py

External Integration Service — TomTom facade for the ReRoute Service.

Responsibilities:
  - Single entry point for ALL TomTom calls from RerouteService
  - Mock mode (ENVIRONMENT=testing or no API key) vs live mode
  - Tenacity retry: 3 attempts, exponential backoff on 5xx / timeout
  - PyBreaker circuit breaker: open after 3 failures, 30s recovery
  - Degraded mode: returns cached/stub data when circuit is open
  - Reuses TrafficProvider for flow data (no duplication)
  - Adds Routing API calls (alternative routes) on top

Architecture note (Section 3.1):
  All TomTom calls go through this service — RerouteService never
  calls TrafficProvider or the Routing API directly.
"""

import json
import hashlib
import logging
import asyncio
import aiohttp
from typing import Dict, Any, List, Optional
from datetime import datetime

import pybreaker
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from app.core.config import settings
from app.providers.traffic import TrafficProvider
from app.providers.tomtom_parser import (
    parse_flow_for_reroute,
    parse_routing_response,
    build_avoidance_params,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit Breaker — shared across the service lifetime
# ---------------------------------------------------------------------------

_circuit_breaker = pybreaker.CircuitBreaker(
    fail_max=3,           # open after 3 consecutive failures
    reset_timeout=30,     # attempt recovery after 30 seconds
    name="tomtom_circuit_breaker",
)

class _AsyncTokenBucket:
    """
    Async token bucket rate limiter for TomTom API calls.

    rate:  tokens refilled per second  (= sustained throughput)
    burst: maximum token accumulation  (= allowed burst size)

    How it works:
      - First `burst` callers proceed immediately (no wait).
      - Subsequent callers are spaced 1/rate seconds apart.
      - Tokens go negative to track backpressure: each queued caller
        pre-reserves its future slot, so callers are serialised correctly
        even when all arrive at the same instant.

    Thread/loop safety:
      - The internal Lock is created lazily and is re-created whenever the
        active event loop changes (important for tests and any code that
        creates a fresh loop).
    """

    __slots__ = ("_rate", "_burst", "_tokens", "_last_refill", "_lock", "_loop")

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens: float = float(burst)
        self._last_refill: float = 0.0
        self._lock: Optional[asyncio.Lock] = None
        self._loop = None                          # loop the lock belongs to

    def _get_lock(self) -> asyncio.Lock:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if self._lock is None or self._loop is not current:
            self._lock = asyncio.Lock()
            self._loop = current
        return self._lock

    async def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        async with self._get_lock():
            now = asyncio.get_event_loop().time()
            if self._last_refill == 0.0:
                self._last_refill = now
            elapsed = now - self._last_refill
            self._tokens = min(float(self._burst), self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                wait = 0.0
            else:
                # Pre-reserve a future slot — tokens can go negative to track
                # N queued callers, each getting a 1/rate-second later slot.
                wait = (1.0 - self._tokens) / self._rate
                self._tokens -= 1.0

        # Sleep OUTSIDE the lock so the next caller can compute its own wait
        # immediately rather than queueing behind our sleep time.
        if wait > 0.0:
            await asyncio.sleep(wait)


# Module-level singleton — 2.5 req/s sustained, burst of 2 immediate calls.
# Tuned for TomTom's free-tier routing limit (~5 QPS; we stay at half that
# to leave headroom for traffic/geometry calls sharing the same key).
# All callers in this process (reroute, evacuation, monitoring) share it.
_tomtom_rate_limiter = _AsyncTokenBucket(rate=2.5, burst=2)


# ---------------------------------------------------------------------------
# Mock TomTom responses for testing / degraded mode
# ---------------------------------------------------------------------------

MOCK_TRAFFIC_RESPONSE = {
    "flowSegmentData": {
        "frc": "FRC0",
        "currentSpeed": 45,
        "freeFlowSpeed": 110,
        "currentTravelTime": 240,
        "freeFlowTravelTime": 120,
        "confidence": 0.9,
        "coordinates": {
            "coordinate": [
                {"latitude": 53.302, "longitude": -6.361},
                {"latitude": 53.312, "longitude": -6.358},
            ]
        },
    }
}

MOCK_ROUTING_RESPONSE = {
    "routes": [
        {
            "summary": {
                "lengthInMeters": 12000,
                "travelTimeInSeconds": 900,
                "trafficDelayInSeconds": 120,
                "departureTime": datetime.utcnow().isoformat(),
                "arrivalTime": datetime.utcnow().isoformat(),
            },
            "legs": [
                {
                    "points": [
                        {"latitude": 53.302 + i * 0.01, "longitude": -6.361 + i * 0.005}
                        for i in range(5)
                    ]
                }
            ],
            "guidance": {"instructions": []},
        },
        {
            "summary": {
                "lengthInMeters": 14500,
                "travelTimeInSeconds": 1100,
                "trafficDelayInSeconds": 180,
                "departureTime": datetime.utcnow().isoformat(),
                "arrivalTime": datetime.utcnow().isoformat(),
            },
            "legs": [
                {
                    "points": [
                        {"latitude": 53.305 + i * 0.008, "longitude": -6.355 + i * 0.006}
                        for i in range(5)
                    ]
                }
            ],
            "guidance": {"instructions": []},
        },
        {
            "summary": {
                "lengthInMeters": 17000,
                "travelTimeInSeconds": 1350,
                "trafficDelayInSeconds": 200,
                "departureTime": datetime.utcnow().isoformat(),
                "arrivalTime": datetime.utcnow().isoformat(),
            },
            "legs": [
                {
                    "points": [
                        {"latitude": 53.308 + i * 0.007, "longitude": -6.350 + i * 0.007}
                        for i in range(5)
                    ]
                }
            ],
            "guidance": {"instructions": []},
        },
    ]
}


# ---------------------------------------------------------------------------
# IntegrationService
# ---------------------------------------------------------------------------

class IntegrationService:
    """
    Facade for all TomTom API interactions in the reroute pipeline.

    Modes:
        live  — real TomTom API calls with retry + circuit breaker
        mock  — returns deterministic stub data (testing / no API key)

    Injected into RerouteService via constructor.
    """

    ROUTING_BASE_URL = "https://api.tomtom.com/routing/1/calculateRoute"

    # Cache TTLs (seconds)
    TRAFFIC_CACHE_TTL = 30   # traffic flow changes slowly — serve from cache
    ROUTING_CACHE_TTL = 300   # routes rarely change within a minute

    def __init__(
        self,
        api_key: Optional[str] = None,
        mode: str = "live",
        timeout: int = 10,
    ):
        """
        Args:
            api_key: TomTom API key (defaults to settings.TRAFFIC_API_KEY)
            mode:    'live' | 'mock'
            timeout: HTTP request timeout in seconds
        """
        self.api_key = api_key or settings.TRAFFIC_API_KEY
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

        # Auto-switch to mock if no API key or testing environment
        if not self.api_key or settings.ENVIRONMENT == "testing":
            self.mode = "mock"
            logger.info("IntegrationService: mock mode (no API key or testing environment)")
        else:
            self.mode = mode
            logger.info(f"IntegrationService: {self.mode} mode")

        # Reuse TrafficProvider for flow data
        self._traffic_provider = TrafficProvider(
            api_key=self.api_key,
            timeout=timeout,
        )

    # -------------------------------------------------------------------------
    # Redis cache helpers — silent fail, never block pipeline
    # -------------------------------------------------------------------------

    async def _get_redis(self):
        """Return the shared app-level Redis client (from cache/redis_client.py).

        Previously this created its own connection pool with a 5s socket_timeout,
        which caused 'Timeout reading from ...:7001' errors when 5 concurrent
        cache reads queued up on the same pool under load.
        The shared client uses a larger pool and is already proven stable.
        """
        try:
            from cache.redis_client import get_redis_client
            return await get_redis_client()
        except Exception as e:
            logger.warning(f"IntegrationService: Redis unavailable — caching disabled ({e})")
            return None

    async def _cache_get(self, key: str) -> Optional[Dict]:
        """Cache read. Returns None on miss or error."""
        try:
            redis = await self._get_redis()
            if redis is None:
                return None
            raw = await redis.get(key)
            if raw:
                logger.debug(f"Cache HIT: {key}")
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"Cache GET failed for {key}: {e}")
        return None

    async def _cache_set(self, key: str, value: Dict, ttl: int) -> None:
        """Cache write. Silently ignores errors."""
        try:
            redis = await self._get_redis()
            if redis is None:
                return
            await redis.setex(key, ttl, json.dumps(value))
            logger.debug(f"Cache SET: {key} (TTL={ttl}s)")
        except Exception as e:
            logger.warning(f"Cache SET failed for {key}: {e}")

    @staticmethod
    def _routing_cache_key(
        origin: Dict[str, float],
        destination: Dict[str, float],
        avoid: List,
    ) -> str:
        """Stable cache key for a routing request."""
        payload = (
            f"{origin['lat']:.4f},{origin['lng']:.4f}:"
            f"{destination['lat']:.4f},{destination['lng']:.4f}:"
            f"{len(avoid)}"
        )
        return f"integration:routing:{hashlib.md5(payload.encode()).hexdigest()[:12]}"

    async def _get_session(self) -> aiohttp.ClientSession:
        # Recreate the session if it belongs to a stale event loop.
        # asyncio.run() (used by Celery per-task) creates a brand-new loop each
        # time. A session (and its connector) created in a previous loop will
        # raise "Future attached to a different loop" even if that old loop is
        # still technically open. We must compare against the *running* loop.
        running_loop = asyncio.get_running_loop()
        if self._session is not None and not self._session.closed:
            try:
                connector = self._session.connector
                connector_loop = getattr(connector, "_loop", None)
                if connector_loop is not None and connector_loop is not running_loop:
                    await self._session.close()
                    self._session = None
            except Exception:
                self._session = None
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Close HTTP sessions."""
        await self._traffic_provider.close()
        if self._session and not self._session.closed:
            await self._session.close()

    # -------------------------------------------------------------------------
    # Mode toggle
    # -------------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """Switch between 'live' and 'mock' mode at runtime."""
        if mode not in ("live", "mock"):
            raise ValueError(f"Invalid mode '{mode}'. Must be 'live' or 'mock'.")
        self.mode = mode
        logger.info(f"IntegrationService: switched to {mode} mode")

    @property
    def is_mock(self) -> bool:
        return self.mode == "mock"

    @property
    def circuit_breaker(self) -> pybreaker.CircuitBreaker:
        return _circuit_breaker

    # -------------------------------------------------------------------------
    # Traffic Flow — fetch + parse for reroute scoring
    # -------------------------------------------------------------------------

    async def fetch_traffic_data(
        self,
        lat: float,
        lon: float,
        radius_km: float = 3.0,
    ) -> dict:
        """
        Fetch traffic flow data for a circular area around (lat, lon).
    
        radius_km comes from disaster_metadata["evaluation"]["impact_radius_km"]
        so the bounding box is proportional to the actual disaster scale:
            flood HIGH      → 8 km
            flood CRITICAL  → 15 km
            fire MEDIUM     → 1 km
            fire HIGH       → 2 km
    
        Cache key is rounded to 3dp (~100m precision) so nearby queries reuse
        the same cached response — avoids hammering TomTom on every monitoring cycle.
        """
        if self.is_mock:
            return {"segments": parse_flow_for_reroute(MOCK_TRAFFIC_RESPONSE), "mode": "mock"}
    
        cache_key = f"integration:traffic:{lat:.3f}:{lon:.3f}:{radius_km:.1f}"
        cached = await self._cache_get(cache_key)
        if cached:
            return cached
    
        try:
            result = await self._fetch_traffic_with_breaker(lat, lon, radius_km)
            await self._cache_set(cache_key, result, self.TRAFFIC_CACHE_TTL)
            return result
        except pybreaker.CircuitBreakerError:
            logger.warning("fetch_traffic_data: circuit breaker open — degraded mode")
            return self._degraded_traffic_response()
        except Exception as e:
            logger.error(f"fetch_traffic_data failed: {e}")
            return self._degraded_traffic_response()
    
    @_circuit_breaker
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )

    async def _fetch_traffic_with_breaker(
        self,
        lat: float,
        lon: float,
        radius_km: float,
    ) -> dict:
        lat_offset = radius_km / 111.0
        lon_offset = radius_km / 73.0
        bounds = (
            f"{lat - lat_offset:.6f},"
            f"{lon - lon_offset:.6f},"
            f"{lat + lat_offset:.6f},"
            f"{lon + lon_offset:.6f}"
        )
        raw = await self._traffic_provider.get_traffic(bounds)
        segments = []
        for seg in raw.get("flow", []):
            mock_flow = {"flowSegmentData": {
                "currentSpeed": seg.get("current_speed", 0),
                "freeFlowSpeed": seg.get("free_flow_speed", 0),
                "confidence": seg.get("confidence", 0.0),
                "coordinates": {"coordinate": [
                    {"latitude": c[0], "longitude": c[1]}
                    for c in seg.get("coordinates", [])
                ]},
            }}
            segments.extend(parse_flow_for_reroute(mock_flow))
        return {"segments": segments, "mode": "live"}
    
    def _degraded_traffic_response(self) -> Dict[str, Any]:
        """Return a degraded mode response using cached/stub data."""
        return {
            "segments": parse_flow_for_reroute(MOCK_TRAFFIC_RESPONSE),
            "mode": "degraded",
            "cached_graph": True,
        }

    # -------------------------------------------------------------------------
    # Routing API — alternative routes
    # -------------------------------------------------------------------------

    async def get_directions(
        self,
        origin: Dict[str, float],
        destination: Dict[str, float],
        avoid: Optional[List[Dict[str, Any]]] = None,
        alternatives: bool = True,
        max_alternatives: int = 3,
    ) -> Dict[str, Any]:
        """
        Get alternative routes from origin to destination avoiding blocked roads.

        Args:
            origin:          {"lat": float, "lng": float}
            destination:     {"lat": float, "lng": float}
            avoid:           List of blocked road segment dicts
            alternatives:    Whether to request alternative routes
            max_alternatives: Max number of alternatives (up to 3 for TomTom)

        Returns:
            Parsed routing response with route_id, travel_time, geometry, GeoJSON
        """
        if self.is_mock:
            logger.debug("get_directions: mock mode")
            return {"routes": parse_routing_response(MOCK_ROUTING_RESPONSE)}

        # Cache-first — routes rarely change within 60s
        cache_key = self._routing_cache_key(origin, destination, avoid or [])
        cached = await self._cache_get(cache_key)
        if cached:
            return cached
        
        try:
            result = await self._get_directions_with_breaker(
                origin, destination, avoid or [], alternatives, max_alternatives
            )
            await self._cache_set(cache_key, result, self.ROUTING_CACHE_TTL)
            return result
        except pybreaker.CircuitBreakerError:
            logger.warning("get_directions: circuit breaker open — skipping (no mock routes)")
            return {"routes": [], "mode": "degraded"}
        except Exception as e:
            logger.error(f"get_directions failed: {e}")
            return {"routes": [], "mode": "degraded"}


    async def fetch_segment_geometry(
        self,
        start_lat: float,
        start_lng: float,
        end_lat: float,
        end_lng: float,
    ) -> Dict[str, Any]:
        """
        Call TomTom calculateRoute between segment start and end points
        to get the actual road-following geometry.

        Called once at trigger time — result is stored in road_segments
        table so subsequent reads are free from DB.

        Returns:
            {
                "points": [[lat, lng], ...],
                "geojson": GeoJSON LineString Feature
            }
        Falls back to straight line if TomTom is unavailable.
        """
        # Straight-line fallback used in mock mode or on error
        def _straight_line():
            points = [[start_lat, start_lng], [end_lat, end_lng]]
            return {
                "points": points,
                "geojson": {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [start_lng, start_lat],
                            [end_lng, end_lat],
                        ],
                    },
                    "properties": {},
                },
            }

        if self.is_mock:
            logger.debug("fetch_segment_geometry: mock mode — returning straight line")
            return _straight_line()

        origin = {"lat": start_lat, "lng": start_lng}
        destination = {"lat": end_lat, "lng": end_lng}

        logger.info(
            f"[TomTom] fetch_segment_geometry → "
            f"({start_lat:.5f},{start_lng:.5f}) → ({end_lat:.5f},{end_lng:.5f})"
        )

        try:
            result = await self.get_directions(
                origin=origin,
                destination=destination,
                avoid=[],
                alternatives=False,   # only need one route for the segment path
                max_alternatives=1,
            )
            routes = result.get("routes", [])
            if routes:
                pts = len(routes[0].get("points", []))
                logger.info(
                    f"[TomTom] fetch_segment_geometry ✓ — {pts} geometry points returned"
                )
                return {
                    "points": routes[0]["points"],
                    "geojson": routes[0]["geojson"],
                }
            logger.warning("[TomTom] fetch_segment_geometry — 0 routes returned, using straight line")
        except Exception as exc:
            logger.warning(
                f"[TomTom] fetch_segment_geometry FAILED "
                f"({start_lat},{start_lng})→({end_lat},{end_lng}): "
                f"{exc} — falling back to straight line"
            )

        return _straight_line()

    @_circuit_breaker
    @retry(
            stop=stop_after_attempt(1),   # no retries for routing — fallback geometry is fine
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
    async def _get_directions_with_breaker(
        self,
        origin: Dict[str, float],
        destination: Dict[str, float],
        avoid: List[Dict[str, Any]],
        alternatives: bool,
        max_alternatives: int,
    ) -> Dict[str, Any]:
        """Internal: routing call with retry + circuit breaker.

        _tomtom_rate_limiter (module-level token bucket, 2.5 req/s / burst=2)
        paces outbound routing requests across ALL callers in this process —
        reroute, evacuation, monitoring — so concurrent asyncio.gather calls
        never flood TomTom with more requests than the API key allows.
        """
        await _tomtom_rate_limiter.acquire()
        session = await self._get_session()

        origin_str = f"{origin['lat']},{origin['lng']}"
        dest_str = f"{destination['lat']},{destination['lng']}"
        url = f"{self.ROUTING_BASE_URL}/{origin_str}:{dest_str}/json"

        avoid_params = build_avoidance_params(avoid) if avoid else []

        params: Dict[str, Any] = {
            "key": self.api_key,
            "traffic": "true",
            "travelMode": "car",
            "routeType": "fastest",
        }

        if alternatives:
            params["maxAlternatives"] = min(max_alternatives, 3)

        payload = {}
        if avoid_params:
            payload["avoidAreas"] = {"rectangles": [
                a["avoidAreaRectangle"] for a in avoid_params
            ]}

        method = "POST" if payload else "GET"
        logger.info(
            f"[TomTom] Routing {method} {origin_str} → {dest_str} "
            f"alts={alternatives} avoid={len(avoid_params)}"
        )

        if payload:
            async with session.post(
                url,
                params=params,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                logger.info(f"[TomTom] Routing response HTTP {response.status}")
                response.raise_for_status()
                data = await response.json()
        else:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                logger.info(f"[TomTom] Routing response HTTP {response.status}")
                response.raise_for_status()
                data = await response.json()

        routes = parse_routing_response(data)
        logger.info(f"[TomTom] get_directions: {len(routes)} route(s) parsed")
        return {"routes": routes}

    # -------------------------------------------------------------------------
    # Recomputation helpers (for overrides + multi-incident)
    # -------------------------------------------------------------------------

    async def recompute_with_overrides(
        self,
        origin: Dict[str, float],
        destination: Dict[str, float],
        blocked_roads: List[Dict[str, Any]],
        active_overrides: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Recompute routes factoring in operator overrides.

        Override types handled:
        close_lane        — adds the route/segment to avoid list (all traffic blocked)
        corridor_priority — same as close_lane for general traffic; TomTom avoids
                            that corridor so only emergency vehicles use it
        pin_detour        — no avoidance change, just preference hint
        open_lane         — removes a previously closed segment from avoid list
        """
        combined_avoid = list(blocked_roads)

        # Track segments/routes to remove from avoid (open_lane override)
        routes_to_open = set()

        for override in active_overrides:
            otype = override.get("type", "")
            route_id = override.get("route_id")
            segment_id = override.get("segment_id")

            if otype in ("close_lane", "corridor_priority"):
                # Add segment_id if provided
                if segment_id:
                    combined_avoid.append({"segment_id": segment_id})
                # Add route geometry as avoidance area if route_id maps to coords
                if route_id:
                    # Mark route as avoided — TomTom will route around it
                    # We encode as a segment hint; real implementation would use
                    # TomTom's avoidVignettes or avoidAreas with the route bbox
                    combined_avoid.append({
                        "segment_id": f"operator-closed-{route_id[:8]}",
                        "route_id": route_id,
                        "reason": otype,
                    })
                    logger.info(
                        f"recompute_with_overrides: {otype} applied to route {route_id[:8]}"
                    )

            elif otype == "open_lane":
                if segment_id:
                    routes_to_open.add(segment_id)

        # Remove any open_lane overrides from combined_avoid
        if routes_to_open:
            combined_avoid = [
                seg for seg in combined_avoid
                if seg.get("segment_id") not in routes_to_open
            ]

        # Bypass cache for overrides — we need fresh routes from TomTom
        # not the cached pre-override routes
        cache_key = self._routing_cache_key(origin, destination, combined_avoid)
        await self._cache_set(cache_key + ':bypass', {'bypass': True}, 1)  # invalidate

        # Call TomTom directly, skipping cache check.
        # _get_directions_with_breaker acquires the rate limiter internally —
        # no outer semaphore/lock needed here.
        try:
            result = await self._get_directions_with_breaker(
                origin, destination, combined_avoid, True, 3
            )
            await self._cache_set(cache_key, result, self.ROUTING_CACHE_TTL)
            return result
        except Exception as e:
            logger.warning(f"recompute_with_overrides TomTom call failed: {e} — falling back to get_directions")
            return await self.get_directions(
                origin=origin,
                destination=destination,
                avoid=combined_avoid,
                alternatives=True,
            )

    async def recompute_multi_incident_detours(
        self,
        incidents: List[Dict[str, Any]],
        vehicles: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Compute routes for a multi-incident scenario.

        Aggregates all blocked roads from all active incidents and
        computes a combined routing solution.

        Phase 5 implementation — stub for Phase 1.
        """
        all_blocked = []
        for incident in incidents:
            all_blocked.extend(incident.get("blocked_roads", []))

        if not vehicles:
            return {"routes": [], "mode": "multi_incident_stub"}

        # Use first vehicle's destination as representative
        sample_vehicle = vehicles[0]
        origin = sample_vehicle.get("current_location", {"lat": 53.3498, "lng": -6.2603})
        destination = sample_vehicle.get("destination", {"lat": 53.4000, "lng": -6.2000})

        return await self.get_directions(
            origin=origin,
            destination=destination,
            avoid=all_blocked,
            alternatives=True,
        )

    # -------------------------------------------------------------------------
    # Health check
    # -------------------------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        """Return current status of the integration service."""
        return {
            "mode": self.mode,
            "circuit_breaker_state": str(_circuit_breaker.current_state),
            "api_key_configured": bool(self.api_key),
        }

# ---------------------------------------------------------------------------
# Module-level singleton (FastAPI lifespan will init this properly)
# ---------------------------------------------------------------------------

_integration_service: Optional[IntegrationService] = None


def get_integration_service() -> IntegrationService:
    """Get or create the module-level IntegrationService singleton."""
    global _integration_service
    if _integration_service is None:
        _integration_service = IntegrationService()
    return _integration_service