# File: app/api/v1/disaster_evaluation.py
"""
Disaster Evaluation API — UC5

Runs the AI evaluation pipeline for citizen disaster reports:
  POST /disaster-evaluation/evaluate/{report_id}  → evaluate a report
  GET  /disaster-evaluation/result/{report_id}     → fetch stored result
  POST /disaster-evaluation/reassess/{disaster_id} → re-evaluate with all linked reports
  POST /disaster-evaluation/review/{disaster_id}   → ERT approve / reject PENDING_REVIEW
  GET  /disaster-evaluation/active-ranked          → all active disasters ranked by severity

Strategy (set at startup via set_evaluation_providers):
  - XGBoost + Rules Engine ensemble (60/40 split) when model artifact present
  - Falls back to RulesEngineStrategy if model file is missing
  - CLIP image analysis runs in parallel during enrichment when photos attached

Global singletons (_map_provider, _traffic_provider, _strategy) are initialised
once during main.py lifespan and injected into each request via
get_evaluation_service_dependency().
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.providers.infrastructure import InfrastructureProvider
from app.providers.map_provider import MapProvider
from app.providers.population_density import GeoNamesPopulationProvider, MockPopulationProvider
from app.providers.surveillance import SurveillanceProvider
from app.providers.traffic import TrafficProvider
from app.repositories.disaster_report_repository import DisasterReportRepository
from app.repositories.disaster_repository import DisasterRepository
from app.repositories.user_repository import UserRepository
from app.schemas.disaster_evaluation_schemas import (
    EvaluationResponse,
    RankedDisasterItem,
    ReviewRequest,
    ReviewResponse,
)
from app.services.evaluation.base import BaseEvaluationStrategy
from app.services.evaluation.downstream import NoopCoordinationClient, NoopRerouteClient, HttpRerouteClient
from app.services.evaluation.ensemble import EnsembleStrategy
from app.services.evaluation.enrichment import EnrichmentPipeline, OpenWeatherMapProvider
from app.services.evaluation.rules_engine import RulesEngineStrategy
from app.services.evaluation.service import DisasterEvaluationService
from app.services.evaluation.xgboost_strategy import XGBoostStrategy
from app.services.live_map_service import LiveMapService
from cache.redis_client import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/disaster-evaluation",
    tags=["Disaster Evaluation — UC5"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Global singletons — set once at startup from main.py lifespan
# ─────────────────────────────────────────────────────────────────────────────

_map_provider:     Optional[MapProvider]            = None
_traffic_provider: Optional[TrafficProvider]        = None
_strategy:         Optional[BaseEvaluationStrategy] = None


def set_evaluation_providers(
    map_provider: MapProvider,
    traffic_provider: TrafficProvider,
) -> None:
    """
    Initialise providers and load the evaluation strategy singleton.
    Called from main.py lifespan at startup — never call directly from endpoints.

    Tries to load XGBoost ensemble first. Falls back to RulesEngineStrategy
    if the model artifact is missing from settings.MODEL_PATH.
    """
    global _map_provider, _traffic_provider, _strategy
    _map_provider     = map_provider
    _traffic_provider = traffic_provider

    try:
        xgb = XGBoostStrategy(model_path=settings.MODEL_PATH)
        xgb.load()
        _strategy = EnsembleStrategy(rules=RulesEngineStrategy(), xgb=xgb)
        logger.info(
            "Evaluation: Ensemble strategy loaded (rules 60%% + XGBoost 40%%) from %s",
            settings.MODEL_PATH,
        )
    except FileNotFoundError:
        _strategy = RulesEngineStrategy()
        logger.warning(
            "Evaluation: XGBoost artifact not found at %s — using RulesEngineStrategy only",
            settings.MODEL_PATH,
        )

    logger.info("Evaluation: map + traffic providers initialised")


# ─────────────────────────────────────────────────────────────────────────────
# Dependency factory
# ─────────────────────────────────────────────────────────────────────────────

async def get_evaluation_service_dependency(
    db: AsyncSession = Depends(get_db),
) -> DisasterEvaluationService:
    """
    Build and return a fully wired DisasterEvaluationService.
    Returns 503 if startup failed and providers are None.
    """
    if _map_provider is None or _traffic_provider is None or _strategy is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Evaluation service not initialised — check startup logs.",
        )

    redis = await get_redis_client()

    enrichment_pipeline = EnrichmentPipeline(
        weather_provider=OpenWeatherMapProvider(api_key=settings.OPENWEATHER_API_KEY),
        traffic_provider=_traffic_provider,
        surveillance_provider=SurveillanceProvider(),
        population_provider=(
            GeoNamesPopulationProvider(username=settings.GEONAMES_USERNAME)
            if settings.GEONAMES_USERNAME
            else MockPopulationProvider()
        ),
        infrastructure_provider=InfrastructureProvider(),
    )

    live_map_service = LiveMapService(
        disaster_repo=DisasterRepository(db),
        disaster_report_repo=DisasterReportRepository(db),
        cache=redis,
        map_provider=_map_provider,
        traffic_provider=_traffic_provider,
    )

    return DisasterEvaluationService(
        strategy=_strategy,
        disaster_repo=DisasterRepository(db),
        report_repo=DisasterReportRepository(db),
        user_repo=UserRepository(db),
        enrichment_pipeline=enrichment_pipeline,
        coordination_client=NoopCoordinationClient(),
        reroute_client=HttpRerouteClient(),
        live_map_service=live_map_service,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/evaluate/{report_id}",
    response_model=EvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate a disaster report",
)
async def evaluate_report(
    report_id: str,
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> EvaluationResponse:
    """
    Runs the full evaluation pipeline for a citizen disaster report:
      1. Enrich with 6 data sources in parallel (weather, traffic, surveillance,
         population density, infrastructure, image analysis)
      2. Check for nearby duplicate / corroborating reports (15-min window)
      3. Run strategy (Rules Engine + XGBoost ensemble, or rules-only fallback)
      4. Persist result to disasters table with appropriate status
      5. Publish disaster.evaluated to RabbitMQ (triggers deploy + reroute)
      6. Trigger downstream services (deploy, reroute, evacuation) if warranted

    Returns the evaluation result including severity, confidence, flag,
    recommended services, and dispatch triggers.
    No auth required — called server-to-server from the report submission pipeline.
    """
    result_dict = await service.evaluate(report_id)
    return EvaluationResponse(**result_dict)


@router.get(
    "/active-ranked",
    response_model=list[RankedDisasterItem],
    status_code=status.HTTP_200_OK,
    summary="All active disasters ranked by severity and confidence",
)
async def get_active_ranked(
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> list[RankedDisasterItem]:
    """
    Returns all active disasters sorted by composite score (severity × confidence).
    Used by the ERT dashboard to prioritise which incidents need attention first.
    No auth required — read-only status endpoint.
    """
    ranked = await service.get_active_ranked()
    return [RankedDisasterItem(**item) for item in ranked]


@router.get(
    "/result/{report_id}",
    response_model=EvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get stored evaluation result for a report",
)
async def get_disaster_evaluation(
    report_id: str,
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> EvaluationResponse:
    """
    Returns the stored evaluation result for a report that has already been evaluated.
    Reads from the disasters table.
    Returns 404 if the report has not yet been evaluated, was rejected,
    or was marked as a duplicate.
    No auth required — read-only.
    """
    result_dict = await service.get_evaluation(report_id)
    return EvaluationResponse(**result_dict)


@router.post(
    "/review/{disaster_id}",
    response_model=ReviewResponse,
    status_code=status.HTTP_200_OK,
    summary="ERT approves or rejects a PENDING_REVIEW disaster",
)
async def review_disaster(
    disaster_id: str,
    body: ReviewRequest,
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> ReviewResponse:
    """
    Allows an ERT member to approve or reject a disaster that was flagged
    PENDING_REVIEW by the evaluation engine (low confidence or edge case).

    Approved → status becomes ACTIVE, dispatch is triggered.
    Rejected → status becomes ARCHIVED, no dispatch.

    Requires: reviewed_by_id (UUID of the ERT member making the decision).
    No auth token check — role enforcement is on the ERT member ID validation.
    """
    result_dict = await service.review(
        disaster_id=disaster_id,
        approved=body.approved,
        reviewed_by_id=body.reviewed_by_id,
        notes=body.notes,
    )
    return ReviewResponse(**result_dict)


@router.post(
    "/reassess/{disaster_id}",
    response_model=EvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Re-evaluate a disaster using all linked reports",
)
async def reassess_disaster(
    disaster_id: str,
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> EvaluationResponse:
    """
    Aggregates all reports linked to this disaster, re-runs the enrichment
    pipeline and evaluation strategy, and updates the stored metadata.

    Use this after multiple corroborating reports have been linked within
    the 15-minute consolidation window to get an updated severity, confidence,
    and deployment recommendation.

    Returns 404 if no reports are linked to the disaster.
    No auth required — called programmatically when new reports arrive.
    """
    result_dict = await service.reassess(disaster_id)
    return EvaluationResponse(**result_dict)