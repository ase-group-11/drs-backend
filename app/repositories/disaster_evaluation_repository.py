"""
DisasterEvaluationRepository — persistence layer for evaluation results.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.disaster_evaluation import DisasterEvaluation
from app.db.models.enums import DisasterSeverity, EvaluationFlag
from app.services.evaluation.base import EvaluationResult

logger = logging.getLogger(__name__)


class DisasterEvaluationRepository:
    """Repository for persisting and querying disaster evaluations."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    async def create_evaluation(
        self,
        report_id: str,
        result: EvaluationResult,
        enrichment_context: Optional[Dict[str, Any]],
        evaluated_at: datetime,
    ) -> DisasterEvaluation:
        """
        Persist a new evaluation record.

        Args:
            report_id: UUID of the disaster report being evaluated
            result: Output from the evaluation strategy
            enrichment_context: Traffic/weather snapshot used
            evaluated_at: Timestamp of this evaluation

        Returns:
            Persisted DisasterEvaluation ORM instance
        """
        evaluation = DisasterEvaluation(
            report_id=report_id,
            severity=DisasterSeverity(result.severity.lower()),
            confidence=result.confidence,
            recommended_services=result.recommended_services,
            trigger_deploy=result.trigger_deploy,
            trigger_reroute=result.trigger_reroute,
            trigger_evacuation=result.trigger_evacuation,
            flag=EvaluationFlag[result.flag],
            strategy_used=result.strategy_used,
            enrichment_context=enrichment_context,
            evaluated_at=evaluated_at,
        )

        self.db.add(evaluation)
        await self.db.flush()
        await self.db.refresh(evaluation)

        logger.info(
            "Persisted evaluation %s for report %s (severity=%s, confidence=%.2f)",
            evaluation.id,
            report_id,
            result.severity,
            result.confidence,
        )

        return evaluation

    async def get_evaluations_for_report(
        self, report_id: str
    ) -> List[DisasterEvaluation]:
        """
        Retrieve all evaluations for a given report, newest first.

        Useful for audit trails and re-evaluation comparisons.
        """
        query = (
            select(DisasterEvaluation)
            .where(DisasterEvaluation.report_id == report_id)
            .order_by(DisasterEvaluation.evaluated_at.desc())
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_latest_evaluation(
        self, report_id: str
    ) -> Optional[DisasterEvaluation]:
        """Return the most recent evaluation for a report, or None."""
        evals = await self.get_evaluations_for_report(report_id)
        return evals[0] if evals else None
