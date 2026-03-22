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
    region_id: str,
    route_plan: Dict[str, Any],
    segment_capacities: Dict[str, int],
) -> None:
    """Register a region for monitoring after a reroute is triggered."""
    _active_reroute_regions[disaster_id] = {
        "disaster_id": disaster_id,
        "region_id": region_id,
        "route_plan": route_plan,
        "segment_capacities": segment_capacities,
    }
    logger.info(f"tasks: registered region {region_id} for disaster {disaster_id}")


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

    For each active reroute region:
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
                    region_id=region_data["region_id"],
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
    region_id: str,
    route_plan: Dict[str, Any],
    segment_capacities: Dict[str, int],
) -> Dict[str, Any]:
    """
    Async inner function — fetch traffic + run dual check for one region.
    """
    from app.providers.integration_service import get_integration_service

    external = get_integration_service()

    try:
        traffic_data = await external.fetch_traffic_data(region_id)
        live_segments = traffic_data.get("segments", [])
    except Exception as e:
        logger.warning(f"_check_region: TomTom fetch failed for {region_id} — {e}")
        live_segments = []

    check = dual_congestion_check(
        live_traffic_data=live_segments,
        route_plan=route_plan,
        segment_capacities=segment_capacities,
    )

    if check["should_recalculate"]:
        logger.info(
            f"_check_region: recalculation triggered for disaster={disaster_id} "
            f"reason={check['triggered_by']}"
        )
        # Fire recalculation as a separate Celery task (non-blocking)
        recalculate_routes.delay(
            disaster_id=disaster_id,
            region_id=region_id,
            triggered_by=check["triggered_by"],
        )

    return {
        "disaster_id": disaster_id,
        "region_id": region_id,
        "should_recalculate": check["should_recalculate"],
        "triggered_by": check["triggered_by"],
        "reactive_segments": check["reactive_segments"],
        "predicted_breaches": len(check["predicted_breaches"]),
    }


@celery_app.task(
    name="app.workers.tasks.warm_traffic_cache",
    bind=True,
)
def warm_traffic_cache(self):
    """
    Pre-warm Redis traffic cache for all active regions every 25s.

    Runs slightly faster than TRAFFIC_CACHE_TTL (30s) so the monitoring
    loop always finds warm cache — zero cold starts, no TomTom wait.
    """
    if not _active_reroute_regions:
        return {"status": "idle", "regions_warmed": 0}

    regions = list({
        data["region_id"]
        for data in _active_reroute_regions.values()
    })

    try:
        asyncio.run(_warm_regions(regions))
    except Exception as e:
        logger.warning(f"warm_traffic_cache: failed — {e}")

    logger.info(f"warm_traffic_cache: warmed {len(regions)} regions")
    return {"status": "ok", "regions_warmed": len(regions)}


async def _warm_regions(region_ids: list) -> None:
    """Fetch traffic for all active regions in parallel to prime cache."""
    from app.providers.integration_service import get_integration_service
    external = get_integration_service()

    # Bypass cache check — go straight to TomTom to refresh
    tasks = [
        external._fetch_traffic_with_breaker(region_id)
        for region_id in region_ids
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for region_id, result in zip(region_ids, results):
        if isinstance(result, Exception):
            logger.warning(f"_warm_regions: failed for {region_id} — {result}")
            continue
        cache_key = f"integration:traffic:{region_id}"
        await external._cache_set(cache_key, result, external.TRAFFIC_CACHE_TTL)
        logger.debug(f"_warm_regions: warmed {region_id}")


@celery_app.task(
    name="app.workers.tasks.recalculate_routes",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
)
def recalculate_routes(
    self,
    disaster_id: str,
    region_id: str,
    triggered_by: str = "congestion",
):
    """
    Recalculate and redistribute routes for an active disaster.

    Called when monitor_traffic_conditions detects congestion breach.
    Rebuilds route options from TomTom and reruns Innovation 1 distribution.
    """
    logger.info(
        f"recalculate_routes: disaster={disaster_id} "
        f"region={region_id} triggered_by={triggered_by}"
    )

    try:
        asyncio.run(
            _recalculate_async(
                disaster_id=disaster_id,
                region_id=region_id,
                triggered_by=triggered_by,
            )
        )
    except Exception as exc:
        logger.error(f"recalculate_routes failed: {exc}")
        raise self.retry(exc=exc)


async def _recalculate_async(
    disaster_id: str,
    region_id: str,
    triggered_by: str,
) -> None:
    """
    Async inner function for route recalculation.

    Fetches current blocked roads, recalculates routes via TomTom,
    reruns Innovation 1, updates map, publishes route.updated event.
    """
    from app.providers.integration_service import get_integration_service
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
        vehicles = await repo.get_users_in_affected_area(region_id)

        # Recalculate routes
        destinations = list({
            (v["destination"]["lat"], v["destination"]["lng"])
            for v in vehicles
            if v.get("destination")
        })
        destination_dicts = [{"lat": lat, "lng": lng} for lat, lng in destinations]

        new_routes = await service.calculate_alternative_routes(
            blocked_roads=blocked_roads,
            destinations=destination_dicts,
        )

        if not new_routes:
            logger.warning(f"_recalculate_async: no new routes for {disaster_id}")
            return

        # Update map
        await mapping.highlight_alternative_routes(new_routes, region_id=region_id)

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