"""
EnsembleStrategy — weighted combination of RulesEngineStrategy and XGBoostStrategy.

Both strategies run in parallel. Their severity predictions and confidence scores
are blended using configurable weights.

Severity blending:  60% rules / 40% XGBoost (ordinal vote)
Confidence blending: 5/7 rules / 2/7 XGBoost (~71.4% / ~28.6%)

The confidence weights are chosen so that when the service-level blend applies
70% engine + 30% CLIP image analysis, the effective three-way split becomes:
    50% rules engine + 20% XGBoost ML + 30% CLIP image analysis

The blended severity is then fed back into the rules engine to produce coherent
services, deployment triggers, and evaluation flag.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Optional

from app.db.models.enums import DisasterSeverity
from app.services.evaluation.base import (
    BaseEvaluationStrategy,
    EvaluationContext,
    EvaluationResult,
)
from app.services.evaluation.rules_engine import RulesEngineStrategy
from app.services.evaluation.xgboost_strategy import XGBoostStrategy

logger = logging.getLogger(__name__)

_SEV_TO_ORD: dict[str, int] = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}
_ORD_TO_SEV: dict[int, str] = {v: k for k, v in _SEV_TO_ORD.items()}


class EnsembleStrategy(BaseEvaluationStrategy):
    """
    Weighted ensemble of RulesEngineStrategy and XGBoostStrategy.

    Severity:   weighted ordinal vote (60% rules / 40% XGBoost)
    Confidence: linear blend (5/7 rules / 2/7 XGBoost ≈ 71.4% / 28.6%)

    The confidence weights are set so that the downstream CLIP image blend
    (70% engine / 30% CLIP) produces the final three-way split:
        50% rules + 20% XGBoost + 30% CLIP

    Services / Triggers / Flag: re-derived by running the rules engine with
              the blended severity so all business logic stays consistent.
    """

    STRATEGY_ID = "ensemble_v1"

    def __init__(
        self,
        rules: RulesEngineStrategy,
        xgb: XGBoostStrategy,
        sev_rules_weight: float = 0.60,
        sev_xgb_weight: float = 0.40,
        conf_rules_weight: float = 5 / 7,
        conf_xgb_weight: float = 2 / 7,
    ) -> None:
        self._rules = rules
        self._xgb = xgb
        self._sev_rules_weight = sev_rules_weight
        self._sev_xgb_weight = sev_xgb_weight
        self._conf_rules_weight = conf_rules_weight
        self._conf_xgb_weight = conf_xgb_weight

    async def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        """
        Run both strategies in parallel and return a blended result.

        If XGBoost falls back to the rules engine internally (low confidence),
        the blend still works correctly — it just means both paths used the
        rules engine and the result matches rules output.
        """
        rules_result, xgb_result = await asyncio.gather(
            self._rules.evaluate(context),
            self._xgb.evaluate(context),
        )

        blended_severity = self._blend_severity(
            rules_result.severity, xgb_result.severity
        )
        blended_confidence = round(
            self._conf_rules_weight * rules_result.confidence
            + self._conf_xgb_weight * xgb_result.confidence,
            4,
        )

        # Re-derive services / triggers / flag using the blended severity.
        # This keeps all business logic (service mapping, trigger thresholds,
        # flag priority order) in the rules engine where it belongs.
        if blended_severity != rules_result.severity:
            corrected = dataclasses.replace(
                context,
                severity=DisasterSeverity(blended_severity.upper()),
            )
            final = await self._rules.evaluate(corrected)
        else:
            final = rules_result

        logger.info(
            "Ensemble [%s]: rules=%s/%.3f xgb=%s/%.3f → blended=%s/%.3f flag=%s",
            context.report_id,
            rules_result.severity, rules_result.confidence,
            xgb_result.severity, xgb_result.confidence,
            blended_severity, blended_confidence,
            final.flag,
        )

        return EvaluationResult(
            disaster_id=context.report_id,
            severity=blended_severity,
            confidence=min(1.0, blended_confidence),
            recommended_services=final.recommended_services,
            trigger_deploy=final.trigger_deploy,
            trigger_reroute=final.trigger_reroute,
            trigger_evacuation=final.trigger_evacuation,
            flag=final.flag,
            strategy_used=self.STRATEGY_ID,
        )

    def _blend_severity(self, rules_sev: str, xgb_sev: str) -> str:
        """
        Weighted ordinal vote between two severity strings.

        Converts to ordinal (LOW=1 … CRITICAL=4), applies weights,
        rounds to nearest integer, maps back to severity name.
        """
        rules_ord = _SEV_TO_ORD.get(rules_sev.upper(), 2)
        xgb_ord = _SEV_TO_ORD.get(xgb_sev.upper(), 2)
        blended = round(self._sev_rules_weight * rules_ord + self._sev_xgb_weight * xgb_ord)
        return _ORD_TO_SEV[max(1, min(4, blended))]
