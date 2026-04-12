"""
Pending report processor — automatic evaluation trigger.

Polls the disaster_reports table every 30 seconds for reports with
status=PENDING and runs the full evaluation pipeline on each one.

This replaces the need to call POST /evaluate/{report_id} manually.
When the Disaster Reporting service creates a report, it lands in the
DB as PENDING. This task picks it up and drives it through evaluation
automatically — the equivalent of what a RabbitMQ consumer would do.

Once evaluate() completes, the report status moves to VERIFIED /
REJECTED / DUPLICATE, so subsequent polls naturally skip it.
Processing is sequential within each run to prevent the same report
being evaluated twice if the pipeline is slower than the poll interval.
"""

from __future__ import annotations

import logging
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.config import settings
from app.db.session import async_session_factory
from app.providers.map_provider import MapProvider
from app.providers.population_density import (
    GeoNamesPopulationProvider,
    MockPopulationProvider,
)
from app.providers.surveillance import SurveillanceProvider
from app.providers.infrastructure import InfrastructureProvider
from app.providers.traffic import TrafficProvider
from app.repositories.disaster_repository import DisasterRepository
from app.repositories.disaster_report_repository import DisasterReportRepository
from app.repositories.user_repository import UserRepository
from app.services.evaluation.downstream import (
    DirectCoordinationClient,
    HttpRerouteClient,
    NoopCoordinationClient,
    NoopRerouteClient,
)
from app.services.evaluation.enrichment import EnrichmentPipeline, OpenWeatherMapProvider
from app.services.evaluation.service import DisasterEvaluationService
from app.services.live_map_service import LiveMapService
from cache.redis_client import get_redis_client

logger = logging.getLogger(__name__)


async def process_pending_reports(
    strategy,
    map_provider: MapProvider,
    traffic_provider: TrafficProvider,
) -> None:
    """
    Fetch all PENDING disaster reports and evaluate each one.

    Opens its own DB session per report so a single failure is isolated
    and never rolls back the work already done on earlier reports in the
    same batch.

    Args:
        strategy:         The evaluation strategy singleton (Ensemble or Rules).
        map_provider:     Shared MapProvider instance from startup.
        traffic_provider: Shared TrafficProvider instance from startup.
    """
    # --- 1. Fetch pending report IDs in a short-lived session ---
    async with async_session_factory() as db:
        try:
            report_repo = DisasterReportRepository(db_session=db)
            pending = await report_repo.get_pending_reports(limit=50)
        except Exception:
            logger.exception("process_pending_reports: failed to query pending reports")
            return

    if not pending:
        return  # nothing to do — suppress noise in logs

    logger.info(
        "process_pending_reports: found %d pending report(s)",
        len(pending),
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

    # --- 2. Evaluate each report in its own session ---
    for report in pending:
        report_id: Optional[str] = report.get("id")
        if not report_id:
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
                        tomtom_api_key=settings.TRAFFIC_API_KEY,
                    ),
                    user_repo=UserRepository(db),
                    # DirectCoordinationClient: auto-dispatches nearest units and triggers
                    # evacuation for CRITICAL events. Uses the same DB session so it can
                    # read the committed disaster record (service.evaluate() commits before
                    # calling _dispatch_downstream).
                    coordination_client=DirectCoordinationClient(db),
                    # HttpRerouteClient: calls POST /api/v1/reroute/trigger for events
                    # where road_blocked=True or severity >= HIGH. Fire-and-forget.
                    reroute_client=HttpRerouteClient(base_url=settings.REROUTE_SERVICE_URL),
                )

                result = await service.evaluate(report_id)
                logger.info(
                    "process_pending_reports: report %s → severity=%s confidence=%.2f flag=%s",
                    report_id,
                    result["severity"],
                    result["confidence"],
                    result["flag"],
                )
                success += 1

        except Exception:
            logger.exception(
                "process_pending_reports: failed on report %s — skipping",
                report_id,
            )
            failures += 1

    logger.info(
        "process_pending_reports: batch done — %d evaluated, %d failed",
        success,
        failures,
    )