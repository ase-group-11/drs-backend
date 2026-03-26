# """
# Disaster Evaluation API — POST /api/v1/disaster-evaluation/evaluate/{report_id}

# Dependency factory wires all pieces together. XGBoostStrategy is loaded
# once at startup and reused as a singleton. If the model artifact is missing,
# falls back to RulesEngineStrategy automatically.
# """

# from __future__ import annotations

# import logging
# from typing import Optional

# from fastapi import APIRouter, Depends, HTTPException, status
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.core.config import settings
# from app.db.session import get_db
# from app.providers.map_provider import MapProvider
# from app.providers.traffic import TrafficProvider
# from app.providers.surveillance import SurveillanceProvider
# from app.repositories.disaster_report_repository import DisasterReportRepository
# from app.repositories.disaster_repository import DisasterRepository
# from app.repositories.user_repository import UserRepository
# from app.schemas.disaster_evaluation_schemas import EvaluationResponse, RankedDisasterItem, ReviewRequest, ReviewResponse
# from app.services.evaluation.base import BaseEvaluationStrategy
# from app.services.evaluation.downstream import NoopCoordinationClient, NoopRerouteClient
# from app.providers.population_density import GeoNamesPopulationProvider, MockPopulationProvider
# from app.providers.infrastructure import InfrastructureProvider
# from app.services.evaluation.enrichment import (
#     EnrichmentPipeline,
#     OpenWeatherMapProvider,
# )
# from app.services.evaluation.ensemble import EnsembleStrategy
# from app.services.evaluation.rules_engine import RulesEngineStrategy
# from app.services.evaluation.xgboost_strategy import XGBoostStrategy
# from app.services.evaluation.service import DisasterEvaluationService
# from app.services.live_map_service import LiveMapService
# from cache.redis_client import get_redis_client

# logger = logging.getLogger(__name__)

# router = APIRouter(
#     prefix="/disaster-evaluation",
#     tags=["Disaster Evaluation"],
# )

# # ---------------------------------------------------------------------------
# # Global singletons — set at startup from main.py
# # ---------------------------------------------------------------------------

# _map_provider: Optional[MapProvider] = None
# _traffic_provider: Optional[TrafficProvider] = None
# _strategy: Optional[BaseEvaluationStrategy] = None


# def set_evaluation_providers(
#     map_provider: MapProvider,
#     traffic_provider: TrafficProvider,
# ) -> None:
#     """
#     Initialise providers and load the XGBoost strategy singleton.

#     Called from main.py lifespan on application startup.
#     Falls back to RulesEngineStrategy if the model artifact is missing.
#     """
#     global _map_provider, _traffic_provider, _strategy
#     _map_provider = map_provider
#     _traffic_provider = traffic_provider

#     try:
#         xgb = XGBoostStrategy(model_path=settings.MODEL_PATH)
#         xgb.load()
#         _strategy = EnsembleStrategy(rules=RulesEngineStrategy(), xgb=xgb)
#         logger.info(
#             "Ensemble strategy loaded (rules 60%% + XGBoost 40%%) from %s",
#             settings.MODEL_PATH,
#         )
#     except FileNotFoundError:
#         _strategy = RulesEngineStrategy()
#         logger.warning(
#             "XGBoost artifact not found at %s — using RulesEngineStrategy only",
#             settings.MODEL_PATH,
#         )

#     logger.info("Evaluation providers initialised (map + traffic)")


# # ---------------------------------------------------------------------------
# # Dependency factory
# # ---------------------------------------------------------------------------


# async def get_evaluation_service_dependency(
#     db: AsyncSession = Depends(get_db),
# ) -> DisasterEvaluationService:
#     """
#     Build and return a fully wired DisasterEvaluationService.

#     503 guard: if startup failed and providers are None, reject early.
#     """
#     if _map_provider is None or _traffic_provider is None or _strategy is None:
#         raise HTTPException(
#             status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
#             detail="Evaluation service unavailable: providers not initialised",
#         )

#     redis_client = await get_redis_client()

#     live_map_service = LiveMapService(
#         disaster_repo=DisasterRepository(db_session=db),
#         disaster_report_repo=DisasterReportRepository(db_session=db),
#         cache=redis_client,
#         map_provider=_map_provider,
#         traffic_provider=_traffic_provider,
#     )

#     weather_provider = OpenWeatherMapProvider(api_key=settings.OPENWEATHER_API_KEY)

#     population_provider = (
#         GeoNamesPopulationProvider(username=settings.GEONAMES_USERNAME)
#         if settings.GEONAMES_USERNAME
#         else MockPopulationProvider()
#     )

#     return DisasterEvaluationService(
#         report_repo=DisasterReportRepository(db),
#         disaster_repo=DisasterRepository(db),
#         strategy=_strategy,
#         enrichment=EnrichmentPipeline(
#             live_map_service=live_map_service,
#             weather_provider=weather_provider,
#             surveillance_provider=SurveillanceProvider(),
#             population_provider=population_provider,
#             infrastructure_provider=InfrastructureProvider(),
#         ),
#         user_repo=UserRepository(db),
#         coordination_client=NoopCoordinationClient(),
#         reroute_client=NoopRerouteClient(),
#     )


# # ---------------------------------------------------------------------------
# # Endpoint
# # ---------------------------------------------------------------------------


# @router.get(
#     "/active-ranked",
#     response_model=list[RankedDisasterItem],
#     status_code=status.HTTP_200_OK,
#     summary="List all active disasters ranked by severity",
#     description=(
#         "Returns every active disaster ordered by severity (CRITICAL first) then by age "
#         "(oldest first within the same severity). Each entry includes a resource_note "
#         "when multiple HIGH/CRITICAL incidents are active simultaneously, flagging "
#         "potential contention for emergency resources (Alternative Flow 1)."
#     ),
# )
# async def get_active_ranked(
#     service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
# ) -> list[RankedDisasterItem]:
#     ranked = await service.get_active_ranked()
#     return [RankedDisasterItem(**item) for item in ranked]


# @router.get(
#     "/result/{report_id}",
#     response_model=EvaluationResponse,
#     status_code=status.HTTP_200_OK,
#     summary="Get evaluation result for a disaster report",
#     description=(
#         "Returns the stored evaluation result for a report that has already "
#         "been evaluated. Reads from the disasters table — 404 if the report "
#         "has not been evaluated, was rejected, or marked as a duplicate."
#     ),
# )
# async def get_disaster_evaluation(
#     report_id: str,
#     service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
# ) -> EvaluationResponse:
#     result_dict = await service.get_evaluation(report_id)
#     return EvaluationResponse(**result_dict)


# @router.post(
#     "/review/{disaster_id}",
#     response_model=ReviewResponse,
#     status_code=status.HTTP_200_OK,
#     summary="ERT approval or rejection of a PENDING_REVIEW disaster",
#     description=(
#         "Allows an Emergency Response Team member to approve or reject a disaster "
#         "that was flagged PENDING_REVIEW by the evaluation engine. "
#         "Approved disasters trigger deployment; rejected disasters are archived."
#     ),
# )
# async def review_disaster(
#     disaster_id: str,
#     body: ReviewRequest,
#     service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
# ) -> ReviewResponse:
#     result_dict = await service.review(
#         disaster_id=disaster_id,
#         approved=body.approved,
#         reviewed_by_id=body.reviewed_by_id,
#         notes=body.notes,
#     )
#     return ReviewResponse(**result_dict)


# @router.post(
#     "/reassess/{disaster_id}",
#     response_model=EvaluationResponse,
#     status_code=status.HTTP_200_OK,
#     summary="Re-evaluate a disaster using all linked reports",
#     description=(
#         "Aggregates all reports linked to this disaster, re-runs the enrichment "
#         "pipeline and evaluation strategy, and updates the stored evaluation metadata. "
#         "Use this after multiple corroborating reports have been linked within the "
#         "15-minute consolidation window to get an updated severity and confidence score."
#     ),
# )
# async def reassess_disaster(
#     disaster_id: str,
#     service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
# ) -> EvaluationResponse:
#     result_dict = await service.reassess(disaster_id)
#     return EvaluationResponse(**result_dict)


# @router.post(
#     "/evaluate/{report_id}",
#     response_model=EvaluationResponse,
#     status_code=status.HTTP_200_OK,
#     summary="Evaluate a disaster report",
#     description=(
#         "Runs the evaluation pipeline for the given report ID and returns "
#         "a structured decision: severity, confidence, recommended services, "
#         "and deployment triggers. The result is also persisted to the database."
#     ),
# )
# async def evaluate_disaster_report(
#     report_id: str,
#     service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
# ) -> EvaluationResponse:
#     """
#     Evaluate a disaster report by ID.

#     - **report_id**: UUID of the disaster report to evaluate
#     - Returns a fixed-schema EvaluationResponse (never changes between strategies)
#     - 404 if the report does not exist
#     - 503 if a provider is not initialised
#     """
#     result_dict = await service.evaluate(report_id)
#     return EvaluationResponse(**result_dict)













"""
Disaster Evaluation API — POST /api/v1/disaster-evaluation/evaluate/{report_id}

Dependency factory wires all pieces together. XGBoostStrategy is loaded
once at startup and reused as a singleton. If the model artifact is missing,
falls back to RulesEngineStrategy automatically.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.providers.map_provider import MapProvider
from app.providers.traffic import TrafficProvider
from app.providers.surveillance import SurveillanceProvider
from app.repositories.disaster_report_repository import DisasterReportRepository
from app.repositories.disaster_repository import DisasterRepository
from app.repositories.user_repository import UserRepository
from app.schemas.disaster_evaluation_schemas import EvaluationResponse, RankedDisasterItem, ReviewRequest, ReviewResponse
from app.services.evaluation.base import BaseEvaluationStrategy
from app.services.evaluation.downstream import NoopCoordinationClient, NoopRerouteClient, HttpRerouteClient
from app.providers.population_density import GeoNamesPopulationProvider, MockPopulationProvider
from app.providers.infrastructure import InfrastructureProvider
from app.services.evaluation.enrichment import (
    EnrichmentPipeline,
    OpenWeatherMapProvider,
)
from app.services.evaluation.ensemble import EnsembleStrategy
from app.services.evaluation.rules_engine import RulesEngineStrategy
from app.services.evaluation.xgboost_strategy import XGBoostStrategy
from app.services.evaluation.service import DisasterEvaluationService
from app.services.live_map_service import LiveMapService
from cache.redis_client import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/disaster-evaluation",
    tags=["Disaster Evaluation"],
)

# ---------------------------------------------------------------------------
# Global singletons — set at startup from main.py
# ---------------------------------------------------------------------------

_map_provider: Optional[MapProvider] = None
_traffic_provider: Optional[TrafficProvider] = None
_strategy: Optional[BaseEvaluationStrategy] = None


def set_evaluation_providers(
    map_provider: MapProvider,
    traffic_provider: TrafficProvider,
) -> None:
    """
    Initialise providers and load the XGBoost strategy singleton.

    Called from main.py lifespan on application startup.
    Falls back to RulesEngineStrategy if the model artifact is missing.
    """
    global _map_provider, _traffic_provider, _strategy
    _map_provider = map_provider
    _traffic_provider = traffic_provider

    try:
        xgb = XGBoostStrategy(model_path=settings.MODEL_PATH)
        xgb.load()
        _strategy = EnsembleStrategy(rules=RulesEngineStrategy(), xgb=xgb)
        logger.info(
            "Ensemble strategy loaded (rules 60%% + XGBoost 40%%) from %s",
            settings.MODEL_PATH,
        )
    except FileNotFoundError:
        _strategy = RulesEngineStrategy()
        logger.warning(
            "XGBoost artifact not found at %s — using RulesEngineStrategy only",
            settings.MODEL_PATH,
        )

    logger.info("Evaluation providers initialised (map + traffic)")


# ---------------------------------------------------------------------------
# Dependency factory
# ---------------------------------------------------------------------------


async def get_evaluation_service_dependency(
    db: AsyncSession = Depends(get_db),
) -> DisasterEvaluationService:
    """
    Build and return a fully wired DisasterEvaluationService.

    503 guard: if startup failed and providers are None, reject early.
    """
    if _map_provider is None or _traffic_provider is None or _strategy is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Evaluation service unavailable: providers not initialised",
        )

    redis_client = await get_redis_client()

    live_map_service = LiveMapService(
        disaster_repo=DisasterRepository(db_session=db),
        disaster_report_repo=DisasterReportRepository(db_session=db),
        cache=redis_client,
        map_provider=_map_provider,
        traffic_provider=_traffic_provider,
    )

    weather_provider = OpenWeatherMapProvider(api_key=settings.OPENWEATHER_API_KEY)

    population_provider = (
        GeoNamesPopulationProvider(username=settings.GEONAMES_USERNAME)
        if settings.GEONAMES_USERNAME
        else MockPopulationProvider()
    )

    return DisasterEvaluationService(
        report_repo=DisasterReportRepository(db),
        disaster_repo=DisasterRepository(db),
        strategy=_strategy,
        enrichment=EnrichmentPipeline(
            live_map_service=live_map_service,
            weather_provider=weather_provider,
            surveillance_provider=SurveillanceProvider(),
            population_provider=population_provider,
            infrastructure_provider=InfrastructureProvider(),
            tomtom_api_key=settings.TRAFFIC_API_KEY,
        ),
        user_repo=UserRepository(db),
        coordination_client=NoopCoordinationClient(),
        reroute_client=HttpRerouteClient(
            base_url=getattr(settings, "REROUTE_SERVICE_URL", "http://localhost:8000")
        ),
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/active-ranked",
    response_model=list[RankedDisasterItem],
    status_code=status.HTTP_200_OK,
    summary="List all active disasters ranked by severity",
    description=(
        "Returns every active disaster ordered by severity (CRITICAL first) then by age "
        "(oldest first within the same severity). Each entry includes a resource_note "
        "when multiple HIGH/CRITICAL incidents are active simultaneously, flagging "
        "potential contention for emergency resources (Alternative Flow 1)."
    ),
)
async def get_active_ranked(
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> list[RankedDisasterItem]:
    ranked = await service.get_active_ranked()
    return [RankedDisasterItem(**item) for item in ranked]


@router.get(
    "/result/{report_id}",
    response_model=EvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get evaluation result for a disaster report",
    description=(
        "Returns the stored evaluation result for a report that has already "
        "been evaluated. Reads from the disasters table — 404 if the report "
        "has not been evaluated, was rejected, or marked as a duplicate."
    ),
)
async def get_disaster_evaluation(
    report_id: str,
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> EvaluationResponse:
    result_dict = await service.get_evaluation(report_id)
    return EvaluationResponse(**result_dict)


@router.post(
    "/review/{disaster_id}",
    response_model=ReviewResponse,
    status_code=status.HTTP_200_OK,
    summary="ERT approval or rejection of a PENDING_REVIEW disaster",
    description=(
        "Allows an Emergency Response Team member to approve or reject a disaster "
        "that was flagged PENDING_REVIEW by the evaluation engine. "
        "Approved disasters trigger deployment; rejected disasters are archived."
    ),
)
async def review_disaster(
    disaster_id: str,
    body: ReviewRequest,
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> ReviewResponse:
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
    description=(
        "Aggregates all reports linked to this disaster, re-runs the enrichment "
        "pipeline and evaluation strategy, and updates the stored evaluation metadata. "
        "Use this after multiple corroborating reports have been linked within the "
        "15-minute consolidation window to get an updated severity and confidence score."
    ),
)
async def reassess_disaster(
    disaster_id: str,
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> EvaluationResponse:
    result_dict = await service.reassess(disaster_id)
    return EvaluationResponse(**result_dict)


@router.post(
    "/evaluate/{report_id}",
    response_model=EvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate a disaster report",
    description=(
        "Runs the evaluation pipeline for the given report ID and returns "
        "a structured decision: severity, confidence, recommended services, "
        "and deployment triggers. The result is also persisted to the database. "
        "Set trigger_reroute=false to evaluate without auto-triggering the reroute service."
    ),
)
async def evaluate_disaster_report(
    report_id: str,
    trigger_reroute: Optional[bool] = None,
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> EvaluationResponse:
    """
    Evaluate a disaster report by ID.

    - **report_id**: UUID of the disaster report to evaluate
    - **trigger_reroute**: Override whether reroute fires after evaluation.
      - `true`  — always trigger reroute regardless of model decision
      - `false` — never trigger reroute (evaluate only)
      - omitted — use model's own decision (default behaviour)
    - Returns a fixed-schema EvaluationResponse (never changes between strategies)
    - 404 if the report does not exist
    - 503 if a provider is not initialised
    """
    result_dict = await service.evaluate(report_id, trigger_reroute_override=trigger_reroute)
    return EvaluationResponse(**result_dict)