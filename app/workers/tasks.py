"""
app/workers/tasks.py

Celery task definitions.

Tasks:
  monitor_traffic_conditions — runs every 300s via Celery beat.
    Polls TomTom for all active reroute regions, runs dual congestion
    check (reactive + predictive), triggers recalculation if needed.

Active region registry is stored in Redis so it is visible to both the
FastAPI process (which triggers reroutes) and the Celery worker (which
runs the monitoring loop). Previously this was an in-memory dict, which
meant the monitoring loop could never see regions registered by FastAPI.
"""

import asyncio
import json
import logging
from typing import List, Dict, Any

from app.workers.celery_app import celery_app
from app.services.predictive_congestion import dual_congestion_check
from app.providers.integration_service import get_integration_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Active regions registry — Redis-backed so FastAPI and Celery share state
# ---------------------------------------------------------------------------

_REGION_PREFIX = "active_region:"
_REGION_TTL    = 86400  # 24 hours — covers the longest realistic disaster


async def register_active_region(
    disaster_id: str,
    lat: float,
    lon: float,
    radius_km: float,
    route_plan: dict,
    segment_capacities: dict,
) -> None:
    from cache.redis_client import get_redis_client
    redis = await get_redis_client()
    data = {
        "disaster_id":       disaster_id,
        "lat":               lat,
        "lon":               lon,
        "radius_km":         radius_km,
        "route_plan":        route_plan,
        "segment_capacities": segment_capacities,
    }
    await redis.set(f"{_REGION_PREFIX}{disaster_id}", json.dumps(data), ex=_REGION_TTL)
    logger.info("tasks: registered active region for disaster %s", disaster_id)


async def deregister_active_region(disaster_id: str) -> None:
    from cache.redis_client import get_redis_client
    redis = await get_redis_client()
    await redis.delete(f"{_REGION_PREFIX}{disaster_id}")
    logger.info("tasks: deregistered disaster %s", disaster_id)


async def get_active_regions() -> Dict[str, Dict[str, Any]]:
    from cache.redis_client import get_redis_client
    redis = await get_redis_client()
    keys = await redis.keys(f"{_REGION_PREFIX}*")
    regions: Dict[str, Dict[str, Any]] = {}
    for key in keys:
        raw = await redis.get(key)
        if raw:
            try:
                entry = json.loads(raw)
                regions[entry["disaster_id"]] = entry
            except Exception:
                pass
    return regions


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery_app.task(
    name="app.workers.tasks.monitor_traffic_conditions",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def monitor_traffic_conditions(self):
    """
    Periodic monitoring loop — runs every 300s via Celery beat.

    Fetches active regions from Redis (shared with FastAPI process), then
    for each active reroute disaster:
      1. Fetch live traffic data from TomTom (via IntegrationService)
      2. Run dual_congestion_check (reactive + predictive)
      3. If recalculation needed → trigger recalculate_routes task
    """
    try:
        return asyncio.run(_monitor_all())
    except Exception as exc:
        logger.error("monitor_traffic_conditions failed: %s", exc)
        return {"status": "error", "regions_checked": 0}


async def _monitor_all() -> dict:
    from app.db.session import engine as _engine
    await _engine.dispose()

    regions = await get_active_regions()
    if not regions:
        logger.debug("monitor_traffic_conditions: no active regions — skipping")
        return {"status": "idle", "regions_checked": 0}

    logger.info("monitor_traffic_conditions: checking %d regions", len(regions))

    results = await asyncio.gather(
        *[
            _check_region(
                disaster_id=did,
                lat=data["lat"],
                lon=data["lon"],
                radius_km=data["radius_km"],
                route_plan=data["route_plan"],
                segment_capacities=data["segment_capacities"],
            )
            for did, data in regions.items()
        ],
        return_exceptions=True,
    )

    checked = []
    for did, res in zip(regions.keys(), results):
        if isinstance(res, Exception):
            logger.error("monitor_traffic_conditions: error for disaster=%s — %s", did, res)
        else:
            checked.append(res)

    return {"status": "ok", "regions_checked": len(checked), "results": checked}

async def _check_region(
    disaster_id: str,
    lat: float,         # replaces region_id
    lon: float,
    radius_km: float,
    route_plan: dict,
    segment_capacities: dict,
) -> dict:
    external = get_integration_service()
 
    try:
        traffic_data = await external.fetch_traffic_data(lat, lon, radius_km)
        live_segments = traffic_data.get("segments", [])
    except Exception as e:
        live_segments = []
 
    check = dual_congestion_check(
        live_traffic_data=live_segments,
        route_plan=route_plan,
        segment_capacities=segment_capacities,
    )
 
    if check["should_recalculate"]:
        recalculate_routes.delay(
            disaster_id=disaster_id,
            lat=lat,
            lon=lon,
            radius_km=radius_km,
            triggered_by=check["triggered_by"],
        )
 
    return {"disaster_id": disaster_id, **check}

@celery_app.task(
    name="app.workers.tasks.warm_traffic_cache",
    bind=True,
)
def warm_traffic_cache(self):
    """
    Pre-warm Redis traffic cache for all active disasters.

    Runs slightly ahead of monitor_traffic_conditions so the cache is
    always warm when the monitoring loop fires.
    """
    try:
        return asyncio.run(_warm_all())
    except Exception as exc:
        logger.warning("warm_traffic_cache: failed — %s", exc)
        return {"status": "error", "regions_warmed": 0}


async def _warm_all() -> dict:
    from app.db.session import engine as _engine
    await _engine.dispose()

    regions = await get_active_regions()
    if not regions:
        return {"status": "idle", "regions_warmed": 0}

    coords = list({
        (data["lat"], data["lon"], data["radius_km"])
        for data in regions.values()
    })
    await _warm_regions(coords)
    logger.info("warm_traffic_cache: warmed %d regions", len(coords))
    return {"status": "ok", "regions_warmed": len(coords)}
 
 
async def _warm_regions(coords: list) -> None:
    """Fetch traffic for all active disaster areas in parallel to prime cache."""
    from app.providers.integration_service import get_integration_service
    external = get_integration_service()
 
    tasks = [
        external._fetch_traffic_with_breaker(lat, lon, radius_km)
        for lat, lon, radius_km in coords
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
 
    for (lat, lon, radius_km), result in zip(coords, results):
        if isinstance(result, Exception):
            logger.warning(f"_warm_regions: failed for ({lat:.3f},{lon:.3f}) — {result}")
            continue
        cache_key = f"integration:traffic:{lat:.3f}:{lon:.3f}:{radius_km:.1f}"
        await external._cache_set(cache_key, result, external.TRAFFIC_CACHE_TTL)
        logger.debug(f"_warm_regions: warmed ({lat:.3f},{lon:.3f})")


@celery_app.task(
    name="app.workers.tasks.recalculate_routes",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
)
def recalculate_routes(
    self,
    disaster_id: str,
    lat, lon, radius_km,
    triggered_by: str = "congestion",
):
    """
    Recalculate and redistribute routes for an active disaster.

    Called when monitor_traffic_conditions detects congestion breach.
    Rebuilds route options from TomTom and reruns Innovation 1 distribution.
    """
    logger.info(
        f"recalculate_routes: disaster={disaster_id} "
        f"triggered_by={triggered_by}"
    )

    try:
        asyncio.run(
            _recalculate_async(
                disaster_id,
                lat, lon, radius_km,
                triggered_by=triggered_by,
            )
        )
    except Exception as exc:
        logger.error(f"recalculate_routes failed: {exc}")
        raise self.retry(exc=exc)


async def _recalculate_async(
    disaster_id: str,
    lat,
    lon,
    radius_km,
    triggered_by: str,
) -> None:
    """
    Async inner function for route recalculation.

    Fetches current blocked roads, recalculates routes via TomTom,
    reruns Innovation 1, updates map, publishes route.updated event.
    """
    # Dispose stale pool connections before opening a DB session on this loop.
    from app.db.session import engine as _engine
    await _engine.dispose()

    from app.repositories.reroute_repository import RerouteRepository
    from app.services.reroute_service import RerouteService
    from app.services.instant_map_updates import MappingService
    from app.workers.reroute_publisher import get_publisher
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        repo = RerouteRepository(db)
        external = get_integration_service()
        publisher = get_publisher()
        if not publisher.is_connected:
            await publisher.connect()
        mapping = MappingService()

        service = RerouteService(
            db=repo,
            external=external,
            mapping=mapping,
            publisher=publisher,
        )

        # Fetch current blocked roads
        blocked_roads = await repo.get_blocked_roads(disaster_id)
        if not blocked_roads:
            logger.warning(f"_recalculate_async: no blocked roads for {disaster_id}")
            return

        # Fetch affected vehicles
        vehicles = await repo.get_users_in_affected_area(lat, lon, radius_km)

        # Recalculate routes
        destinations = list({
            (v["destination"]["lat"], v["destination"]["lng"])
            for v in vehicles
            if v.get("destination")
        })
        destination_dicts = [{"lat": la, "lng": lo} for la, lo in destinations]

        new_routes = await service.calculate_alternative_routes(
            blocked_roads=blocked_roads,
            destinations=destination_dicts,
        )

        if not new_routes:
            logger.warning(f"_recalculate_async: no new routes for {disaster_id}")
            return

        # Update map
        await mapping.highlight_alternative_routes(new_routes, disaster_id=disaster_id)

        # Publish route.updated event
        await publisher.publish_route_updated(
            disaster_id=disaster_id,
            reason=triggered_by,
            vehicles=vehicles,
            route_assignments={},
            routes=new_routes,
        )

        logger.info(
            f"_recalculate_async: recalculation complete "
            f"disaster={disaster_id} routes={len(new_routes)}"
        )

# ---------------------------------------------------------------------------
# Evaluation tasks have been moved into FastAPI's asyncio lifespan (main.py).
# They run as asyncio.create_task loops on the same event loop as FastAPI,
# eliminating all "Future attached to a different loop" errors that arose
# from Celery's asyncio.run()-per-task model.
# ---------------------------------------------------------------------------