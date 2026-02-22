"""
DisasterEvaluationService — orchestrator for the evaluation pipeline.

Responsibilities:
1. Load the disaster report (dict) from DisasterReportRepository
2. Run the enrichment pipeline (traffic + weather)
3. Build an EvaluationContext
4. Call the injected strategy
5. Persist the result via DisasterEvaluationRepository
6. Return a dict matching EvaluationResponse schema
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from app.db.models.enums import DisasterSeverity
from app.repositories.disaster_report_repository import DisasterReportRepository
from app.repositories.disaster_evaluation_repository import DisasterEvaluationRepository
from app.services.evaluation.base import BaseEvaluationStrategy, EvaluationContext
from app.services.evaluation.enrichment import EnrichmentPipeline

logger = logging.getLogger(__name__)


class DisasterEvaluationService:
    """
    Orchestrates disaster report evaluation end-to-end.

    All dependencies are injected — swapping strategy or enrichment
    requires no changes here.
    """

    def __init__(
        self,
        report_repo: DisasterReportRepository,
        evaluation_repo: DisasterEvaluationRepository,
        strategy: BaseEvaluationStrategy,
        enrichment: EnrichmentPipeline,
    ) -> None:
        self._report_repo = report_repo
        self._eval_repo = evaluation_repo
        self._strategy = strategy
        self._enrichment = enrichment

    async def evaluate(self, report_id: str) -> Dict[str, Any]:
        """
        Evaluate a disaster report and return the fixed-schema result dict.

        Args:
            report_id: UUID string of the disaster report

        Returns:
            Dict matching EvaluationResponse schema

        Raises:
            HTTPException 404 if the report does not exist
        """
        # 1. Load report (returns dict, not ORM object)
        report = await self._report_repo.get_report_by_id(report_id)
        if report is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Disaster report '{report_id}' not found",
            )

        # 2. Extract coordinates (may be None)
        lat: Optional[float] = report.get("location", {}).get("lat")
        lon: Optional[float] = report.get("location", {}).get("lon")

        # 3. Enrichment — skipped gracefully if no coordinates
        traffic_ctx: Optional[dict] = None
        weather_ctx: Optional[dict] = None

        if lat is not None and lon is not None:
            traffic_ctx, weather_ctx = await self._enrichment.enrich(lat, lon)
        else:
            logger.info(
                "Report %s has no coordinates; skipping enrichment", report_id
            )

        # 4. Build EvaluationContext
        context = self._build_context(report, traffic_ctx, weather_ctx)

        # 5. Run strategy
        result = await self._strategy.evaluate(context)

        # 6. Persist evaluation
        evaluated_at = datetime.now(timezone.utc)
        await self._eval_repo.create_evaluation(
            report_id=report_id,
            result=result,
            enrichment_context={
                "traffic": traffic_ctx,
                "weather": weather_ctx,
            },
            evaluated_at=evaluated_at,
        )

        # 7. Return response dict (matches EvaluationResponse schema)
        return {
            "disaster_id": result.disaster_id,
            "severity": result.severity,
            "confidence": result.confidence,
            "recommended_services": result.recommended_services,
            "trigger_deploy": result.trigger_deploy,
            "trigger_reroute": result.trigger_reroute,
            "trigger_evacuation": result.trigger_evacuation,
            "flag": result.flag,
            "strategy_used": result.strategy_used,
            "evaluated_at": evaluated_at,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_context(
        report: Dict[str, Any],
        traffic_ctx: Optional[dict],
        weather_ctx: Optional[dict],
    ) -> EvaluationContext:
        """Convert a report dict + enrichment into an EvaluationContext."""
        # severity is stored as lowercase value — map back to enum
        severity_value: str = report["severity"]  # e.g. "high"
        severity = DisasterSeverity(severity_value)

        lat: Optional[float] = report.get("location", {}).get("lat")
        lon: Optional[float] = report.get("location", {}).get("lon")

        # Use current UTC hour for time-of-day rules
        hour_of_day = datetime.now(timezone.utc).hour

        return EvaluationContext(
            report_id=report["id"],
            disaster_type=report["disaster_type"],     # lowercase value
            severity=severity,
            description=report.get("description", ""),
            people_affected=report.get("people_affected", 0),
            multiple_casualties=report.get("multiple_casualties", False),
            structural_damage=report.get("structural_damage", False),
            road_blocked=report.get("road_blocked", False),
            lat=lat,
            lon=lon,
            hour_of_day=hour_of_day,
            traffic_context=traffic_ctx,
            weather_context=weather_ctx,
        )
