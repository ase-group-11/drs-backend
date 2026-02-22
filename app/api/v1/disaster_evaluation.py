"""
Disaster Evaluation API — POST /api/v1/disaster-evaluation/evaluate/{report_id}

Dependency factory wires all pieces together. Phase 2 uses XGBoostStrategy
loaded once at startup; the strategy singleton is injected into each request.

To swap strategy: implement BaseEvaluationStrategy, update set_evaluation_providers,
and change the `strategy=` line in get_evaluation_service_dependency.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.providers.traffic import TrafficProvider
from app.repositories.disaster_report_repository import DisasterReportRepository
from app.repositories.disaster_evaluation_repository import DisasterEvaluationRepository
from app.schemas.disaster_evaluation_schemas import EvaluationResponse
from app.services.evaluation.enrichment import EnrichmentPipeline, MockWeatherProvider
from app.services.evaluation.xgboost_strategy import XGBoostStrategy
from app.services.evaluation.service import DisasterEvaluationService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/disaster-evaluation",
    tags=["Disaster Evaluation"],
)

# ---------------------------------------------------------------------------
# Global providers — set at startup from main.py
# ---------------------------------------------------------------------------

_traffic_provider: Optional[TrafficProvider] = None
_xgboost_strategy: Optional[XGBoostStrategy] = None


def set_evaluation_providers(traffic_provider: TrafficProvider) -> None:
    """
    Initialise the traffic provider and load the XGBoost model.

    Called from main.py lifespan on application startup.

    Raises:
        FileNotFoundError: if the model artifact is missing (intentional —
            fail fast at startup rather than silently serving bad results).
    """
    global _traffic_provider, _xgboost_strategy
    _traffic_provider = traffic_provider

    strategy = XGBoostStrategy(model_path=settings.MODEL_PATH)
    strategy.load()   # raises FileNotFoundError if artifact missing — intentional
    _xgboost_strategy = strategy

    logger.info(
        "Evaluation providers initialised (traffic + XGBoost model from %s)",
        settings.MODEL_PATH,
    )


# ---------------------------------------------------------------------------
# Dependency factory
# ---------------------------------------------------------------------------


def get_evaluation_service_dependency(
    db: AsyncSession = Depends(get_db),
) -> DisasterEvaluationService:
    """
    Build and return a fully wired DisasterEvaluationService.

    503 guard: if startup failed and either provider is None, reject early.
    """
    if _traffic_provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Evaluation service unavailable: traffic provider not initialised",
        )

    if _xgboost_strategy is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Evaluation service unavailable: XGBoost model not loaded",
        )

    return DisasterEvaluationService(
        report_repo=DisasterReportRepository(db),
        evaluation_repo=DisasterEvaluationRepository(db),
        strategy=_xgboost_strategy,
        enrichment=EnrichmentPipeline(
            traffic_provider=_traffic_provider,
            weather_provider=MockWeatherProvider(),
        ),
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/evaluate/{report_id}",
    response_model=EvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate a disaster report",
    description=(
        "Runs the evaluation pipeline for the given report ID and returns "
        "a structured decision: severity, confidence, recommended services, "
        "and deployment triggers. The result is also persisted to the database."
    ),
)
async def evaluate_disaster_report(
    report_id: str,
    service: DisasterEvaluationService = Depends(get_evaluation_service_dependency),
) -> EvaluationResponse:
    """
    Evaluate a disaster report by ID.

    - **report_id**: UUID of the disaster report to evaluate
    - Returns a fixed-schema EvaluationResponse (never changes between strategies)
    - 404 if the report does not exist
    - 503 if a provider is not initialised
    """
    result_dict = await service.evaluate(report_id)
    return EvaluationResponse(**result_dict)
