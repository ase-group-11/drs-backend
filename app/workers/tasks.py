"""
app/workers/tasks.py

Celery task definitions.

Tasks:
  monitor_traffic_conditions — runs every 30s via Celery beat.
    Polls TomTom for all active reroute regions, runs dual congestion
    check (reactive + predictive), triggers recalculation if needed.
"""

import asyncio
import logging
from typing import List, Dict, Any

from app.workers.celery_app import celery_app
from app.services.predictive_congestion import dual_congestion_check
from app.providers.integration_service import get_integration_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory active regions registry
# Populated when RerouteService.trigger_reroute_traffic completes.
# Cleared when restore_normal_flow is called.
# ---------------------------------------------------------------------------
# { disaster_id: { region_id, route_plan, segment_capacities } }
_active_reroute_regions: Dict[str, Dict[str, Any]] = {}


def register_active_region(
    disaster_id: str,
    lat: float,          
    lon: float,
    radius_km: float,
    route_plan: dict,
    segment_capacities: dict,
) -> None:
    _active_reroute_regions[disaster_id] = {
        "disaster_id": disaster_id,
        "lat": lat,
        "lon": lon,
        "radius_km": radius_km,
        "route_plan": route_plan,
        "segment_capacities": segment_capacities,
    }


def deregister_active_region(disaster_id: str) -> None:
    """Remove a region from monitoring after disaster is cleared."""
    _active_reroute_regions.pop(disaster_id, None)
    logger.info(f"tasks: deregistered disaster {disaster_id}")


def get_active_regions() -> Dict[str, Dict[str, Any]]:
    return dict(_active_reroute_regions)


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
    Periodic monitoring loop — runs every 30s via Celery beat.
 
    For each active reroute disaster:
      1. Fetch live traffic data from TomTom (via IntegrationService)
      2. Run dual_congestion_check (reactive + predictive)
      3. If recalculation needed → trigger async recalculation task
 
    Celery tasks are synchronous — async calls use asyncio.run().
    """
    if not _active_reroute_regions:
        logger.debug("monitor_traffic_conditions: no active regions — skipping")
        return {"status": "idle", "regions_checked": 0}
 
    logger.info(
        f"monitor_traffic_conditions: checking {len(_active_reroute_regions)} regions"
    )
 
    results = []
    for disaster_id, region_data in list(_active_reroute_regions.items()):
        try:
            result = asyncio.run(
                _check_region(
                    disaster_id=disaster_id,
                    lat=region_data["lat"],
                    lon=region_data["lon"],
                    radius_km=region_data["radius_km"],
                    route_plan=region_data["route_plan"],
                    segment_capacities=region_data["segment_capacities"],
                )
            )
            results.append(result)
        except Exception as e:
            logger.error(
                f"monitor_traffic_conditions: error checking "
                f"disaster={disaster_id} — {e}"
            )
 
    return {
        "status": "ok",
        "regions_checked": len(results),
        "results": results,
    }

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
    Pre-warm Redis traffic cache for all active disasters every 25s.
 
    Runs slightly faster than TRAFFIC_CACHE_TTL (30s) so the monitoring
    loop always finds warm cache — zero cold starts, no TomTom wait.
    """
    if not _active_reroute_regions:
        return {"status": "idle", "regions_warmed": 0}
 
    coords = list({
        (data["lat"], data["lon"], data["radius_km"])
        for data in _active_reroute_regions.values()
    })
 
    try:
        asyncio.run(_warm_regions(coords))
    except Exception as e:
        logger.warning(f"warm_traffic_cache: failed — {e}")
 
    logger.info(f"warm_traffic_cache: warmed {len(coords)} regions")
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
# Evaluation tasks — delegate to app/tasks/ functions
# ---------------------------------------------------------------------------

@celery_app.task(
    name="app.workers.tasks.auto_evaluate_pending_reports",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def auto_evaluate_pending_reports(self):
    """
    Runs every 60s — picks up PENDING disaster reports and evaluates them.
    Delegates to app/tasks/process_pending_reports.py.
    """
    try:
        asyncio.run(_run_process_pending())
        return {"status": "ok"}
    except Exception as exc:
        logger.error(f"auto_evaluate_pending_reports failed: {exc}")
        raise self.retry(exc=exc)


async def _run_process_pending():
    # Dispose any pooled connections that are bound to a previously closed
    # event loop.  Each asyncio.run() call creates a new loop; asyncpg
    # futures from the old loop raise "Future attached to a different loop"
    # if we let the pool reuse them.  dispose() closes all idle connections
    # immediately and invalidates checked-out ones so they are discarded on
    # return — the pool will open fresh connections on this new loop.
    from app.db.session import engine as _engine
    await _engine.dispose()

    from app.tasks.process_pending_reports import process_pending_reports
    from app.providers.map_provider import MapProvider
    from app.providers.traffic import TrafficProvider
    from app.services.evaluation.rules_engine import RulesEngineStrategy
    from app.services.evaluation.ensemble import EnsembleStrategy
    from app.core.config import settings

    map_provider = MapProvider(api_key=settings.MAPBOX_API_KEY)
    traffic_provider = TrafficProvider(api_key=settings.TRAFFIC_API_KEY)

    try:
        from app.services.evaluation.xgboost_strategy import XGBoostStrategy
        xgb = XGBoostStrategy(model_path=settings.MODEL_PATH)
        xgb.load()
        strategy = EnsembleStrategy(rules=RulesEngineStrategy(), xgb=xgb)
    except Exception:
        strategy = RulesEngineStrategy()

    await process_pending_reports(
        strategy=strategy,
        map_provider=map_provider,
        traffic_provider=traffic_provider,
    )


@celery_app.task(
    name="app.workers.tasks.periodic_reassess_disasters",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def periodic_reassess_disasters(self):
    """
    Runs every 15 minutes — re-evaluates all active disasters.
    Picks up new corroborating reports and updated conditions.
    Delegates to app/tasks/periodic_reassess.py.
    """
    try:
        asyncio.run(_run_periodic_reassess())
        return {"status": "ok"}
    except Exception as exc:
        logger.error(f"periodic_reassess_disasters failed: {exc}")
        raise self.retry(exc=exc)


async def _run_periodic_reassess():
    # Same dispose() guard as _run_process_pending — prevents stale asyncpg
    # connections from a previous asyncio.run() loop from poisoning this task.
    from app.db.session import engine as _engine
    await _engine.dispose()

    from app.tasks.periodic_reassess import run_periodic_reassess
    from app.providers.map_provider import MapProvider
    from app.providers.traffic import TrafficProvider
    from app.services.evaluation.rules_engine import RulesEngineStrategy
    from app.services.evaluation.ensemble import EnsembleStrategy
    from app.core.config import settings

    map_provider = MapProvider()
    traffic_provider = TrafficProvider(api_key=settings.TRAFFIC_API_KEY)

    try:
        from app.services.evaluation.xgboost_strategy import XGBoostStrategy
        xgb = XGBoostStrategy(model_path=settings.MODEL_PATH)
        xgb.load()
        strategy = EnsembleStrategy(rules=RulesEngineStrategy(), xgb=xgb)
    except Exception:
        strategy = RulesEngineStrategy()

    await run_periodic_reassess(
        strategy=strategy,
        map_provider=map_provider,
        traffic_provider=traffic_provider,
    )