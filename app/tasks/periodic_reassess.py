"""
Periodic re-evaluation task — Scheduled Flow from the spec.

The spec requires active disasters to be automatically re-evaluated
periodically to pick up new corroborating reports and updated external
data (weather, traffic, surveillance). This task:

  1. Fetches all ACTIVE disasters from the database.
  2. For each one, calls DisasterEvaluationService.reassess() — the same
     logic used by the manual POST /reassess/{disaster_id} endpoint.
  3. Logs the outcome; any per-disaster failure is isolated so one bad
     record never blocks the rest of the batch.

Invoked every 15 minutes by the APScheduler instance wired in main.py.
The 15-minute cadence matches the consolidation window mentioned in the
spec (reports within 15 minutes of the same disaster are considered
related).
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.config import settings
from app.db.session import async_session_factory
from app.providers.map_provider import MapProvider
from app.providers.population_density import GeoNamesPopulationProvider, MockPopulationProvider
from app.providers.surveillance import SurveillanceProvider
from app.providers.infrastructure import InfrastructureProvider
from app.providers.traffic import TrafficProvider
from app.repositories.disaster_repository import DisasterRepository
from app.repositories.disaster_report_repository import DisasterReportRepository
from app.repositories.user_repository import UserRepository
from app.services.evaluation.downstream import NoopCoordinationClient, NoopRerouteClient
from app.services.evaluation.enrichment import EnrichmentPipeline, OpenWeatherMapProvider
from app.services.evaluation.service import DisasterEvaluationService
from app.services.live_map_service import LiveMapService
from cache.redis_client import get_redis_client

logger = logging.getLogger(__name__)


async def run_periodic_reassess(
    strategy,  # BaseEvaluationStrategy — passed in from the singleton set at startup
    map_provider: MapProvider,
    traffic_provider: TrafficProvider,
) -> None:
    """
    Fetch all active disasters and re-evaluate each one.

    Opens its own database session so this function can run outside
    the FastAPI request/response cycle (no Depends injection available).

    Args:
        strategy:          The evaluation strategy singleton (Ensemble or Rules).
        map_provider:      Shared MapProvider instance from startup.
        traffic_provider:  Shared TrafficProvider instance from startup.
    """
    logger.info("Periodic reassess: starting scheduled run")

    async with async_session_factory() as db:
        try:
            disaster_repo = DisasterRepository(db_session=db)
            active_disasters = await disaster_repo.get_all_active_disasters()
        except Exception:
            logger.exception("Periodic reassess: failed to fetch active disasters")
            return

    if not active_disasters:
        logger.info("Periodic reassess: no active disasters — nothing to do")
        return

    logger.info(
        "Periodic reassess: found %d active disaster(s) to re-evaluate",
        len(active_disasters),
    )

    redis_client = await get_redis_client()
    weather_provider = OpenWeatherMapProvider(api_key=settings.OPENWEATHER_API_KEY)
    population_provider = (
        GeoNamesPopulationProvider(username=settings.GEONAMES_USERNAME)
        if settings.GEONAMES_USERNAME
        else MockPopulationProvider()
    )

    success = 0
    failures = 0

    for disaster in active_disasters:
        disaster_id: Optional[str] = str(disaster.get("id", ""))
        if not disaster_id:
            continue

        try:
            async with async_session_factory() as db:
                live_map_service = LiveMapService(
                    disaster_repo=DisasterRepository(db_session=db),
                    disaster_report_repo=DisasterReportRepository(db_session=db),
                    cache=redis_client,
                    map_provider=map_provider,
                    traffic_provider=traffic_provider,
                )

                service = DisasterEvaluationService(
                    report_repo=DisasterReportRepository(db),
                    disaster_repo=DisasterRepository(db),
                    strategy=strategy,
                    enrichment=EnrichmentPipeline(
                        live_map_service=live_map_service,
                        weather_provider=weather_provider,
                        surveillance_provider=SurveillanceProvider(),
                        population_provider=population_provider,
                        infrastructure_provider=InfrastructureProvider(),
                    ),
                    user_repo=UserRepository(db),
                    coordination_client=NoopCoordinationClient(),
                    reroute_client=NoopRerouteClient(),
                )

                result = await service.reassess(disaster_id)
                logger.info(
                    "Periodic reassess: disaster %s → severity=%s confidence=%.2f flag=%s",
                    disaster_id,
                    result["severity"],
                    result["confidence"],
                    result["flag"],
                )
                success += 1

        except Exception:
            logger.exception(
                "Periodic reassess: failed to reassess disaster %s — skipping",
                disaster_id,
            )
            failures += 1

    logger.info(
        "Periodic reassess: completed — %d succeeded, %d failed",
        success,
        failures,
    )
