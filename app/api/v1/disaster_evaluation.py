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
from app.services.evaluation.downstream import (
    NoopCoordinationClient, NoopRerouteClient,
    HttpRerouteClient, DirectCoordinationClient,
)
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


async def evaluate_report_background(report_id: str) -> None:
    """
    Run the full evaluation pipeline for a single report in the background.

    Opens its own DB session so it is safe to fire with asyncio.create_task
    immediately after a report is saved — the caller's session is not shared.
    Any failure is logged and swallowed so it never propagates to the HTTP response.
    """
    if _map_provider is None or _traffic_provider is None or _strategy is None:
        logger.warning(
            "evaluate_report_background: providers not initialised — skipping report %s",
            report_id,
        )
        return

    from app.db.session import async_session_factory

    try:
        redis_client = await get_redis_client()
        weather_provider = OpenWeatherMapProvider(api_key=settings.OPENWEATHER_API_KEY)
        population_provider = (
            GeoNamesPopulationProvider(username=settings.GEONAMES_USERNAME)
            if settings.GEONAMES_USERNAME
            else MockPopulationProvider()
        )

        async with async_session_factory() as db:
            live_map_service = LiveMapService(
                disaster_repo=DisasterRepository(db_session=db),
                disaster_report_repo=DisasterReportRepository(db_session=db),
                cache=redis_client,
                map_provider=_map_provider,
                traffic_provider=_traffic_provider,
            )

            service = DisasterEvaluationService(
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
                coordination_client=DirectCoordinationClient(db=db),
                reroute_client=HttpRerouteClient(
                    base_url=getattr(settings, "REROUTE_SERVICE_URL", "http://localhost:8000")
                ),
            )

            result = await service.evaluate(report_id)
            logger.info(
                "evaluate_report_background: report %s → severity=%s confidence=%.2f flag=%s",
                report_id,
                result["severity"],
                result["confidence"],
                result["flag"],
            )

    except Exception:
        logger.exception(
            "evaluate_report_background: failed to evaluate report %s — skipping",
            report_id,
        )


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
        coordination_client=DirectCoordinationClient(db=db),
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


# ---------------------------------------------------------------------------
# Pipeline probe — end-to-end smoke test
# ---------------------------------------------------------------------------

@router.post(
    "/pipeline-probe",
    status_code=status.HTTP_200_OK,
    summary="End-to-end pipeline smoke test",
    description=(
        "Creates a synthetic disaster report, runs the full evaluation pipeline "
        "synchronously, and returns the status of every step: report creation, "
        "evaluation, disaster record persistence, and service trigger flags. "
        "Use this to verify the report→evaluate→deploy workflow is wired correctly."
    ),
)
async def pipeline_probe(
    db: AsyncSession = Depends(get_db),
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> dict:
    """
    Smoke-test the full pipeline end-to-end:
      1. Insert a synthetic PENDING disaster report
      2. Run evaluation (synchronous — same logic as background task)
      3. Query DB to confirm disaster record was persisted
      4. Return step-by-step status

    The synthetic report is a low-severity FIRE at Dublin city centre.
    It is left in the database so you can inspect it afterwards.
    """
    import uuid as _uuid
    from datetime import datetime
    from sqlalchemy import text

    steps: dict = {}

    # ── Step 1: create a synthetic report ────────────────────────────────────
    report_id = str(_uuid.uuid4())
    try:
        await db.execute(
            text("""
                INSERT INTO disaster_reports (
                    id, created_at, updated_at,
                    user_id, location_address, disaster_type, severity,
                    description, location, people_affected,
                    multiple_casualties, structural_damage, road_blocked,
                    report_status, disaster_id, reviewed_by_id, reviewed_at, rejection_reason
                ) VALUES (
                    :id, :now, :now,
                    'pipeline-probe', 'O''Connell Street, Dublin 1',
                    CAST('FIRE' AS disaster_type),
                    CAST('HIGH' AS disaster_severity),
                    'Pipeline probe — synthetic report for smoke testing',
                    ST_SetSRID(ST_MakePoint(-6.2603, 53.3498), 4326)::geography,
                    5, false, false, false,
                    CAST('PENDING' AS disaster_report_status),
                    NULL, NULL, NULL, NULL
                )
            """),
            {"id": report_id, "now": datetime.utcnow()},
        )
        await db.flush()
        steps["report_created"] = {"ok": True, "report_id": report_id, "status": "PENDING"}
    except Exception as exc:
        steps["report_created"] = {"ok": False, "error": str(exc)}
        return {"pipeline_ok": False, "steps": steps}

    # ── Step 2: run evaluation ────────────────────────────────────────────────
    try:
        result = await service.evaluate(report_id)
        steps["evaluation_ran"] = {
            "ok": True,
            "severity": result.get("severity"),
            "confidence": result.get("confidence"),
            "flag": result.get("flag"),
            "strategy_used": result.get("strategy_used"),
            "trigger_deploy": result.get("trigger_deploy"),
            "trigger_reroute": result.get("trigger_reroute"),
            "trigger_evacuation": result.get("trigger_evacuation"),
            "recommended_services": result.get("recommended_services"),
        }
        disaster_id = result.get("disaster_id")
    except Exception as exc:
        steps["evaluation_ran"] = {"ok": False, "error": str(exc)}
        return {"pipeline_ok": False, "steps": steps}

    # ── Step 3: confirm disaster record in DB ─────────────────────────────────
    try:
        row = await db.execute(
            text("SELECT id, status FROM disasters WHERE id = :id"),
            {"id": disaster_id},
        )
        disaster_row = row.mappings().first()
        if disaster_row:
            steps["disaster_persisted"] = {
                "ok": True,
                "disaster_id": str(disaster_row["id"]),
                "status": str(disaster_row["status"]),
            }
        else:
            steps["disaster_persisted"] = {"ok": False, "error": "disaster row not found in DB"}
    except Exception as exc:
        steps["disaster_persisted"] = {"ok": False, "error": str(exc)}

    # ── Step 4: confirm report status was updated ─────────────────────────────
    try:
        row = await db.execute(
            text("SELECT report_status FROM disaster_reports WHERE id = :id"),
            {"id": report_id},
        )
        report_row = row.mappings().first()
        if report_row:
            steps["report_status_updated"] = {
                "ok": True,
                "report_status": str(report_row["report_status"]),
            }
        else:
            steps["report_status_updated"] = {"ok": False, "error": "report row not found"}
    except Exception as exc:
        steps["report_status_updated"] = {"ok": False, "error": str(exc)}

    pipeline_ok = all(s.get("ok") for s in steps.values())
    return {"pipeline_ok": pipeline_ok, "steps": steps}