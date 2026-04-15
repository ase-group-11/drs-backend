"""
Phase 2 evaluation strategy: XGBoost severity classifier.

Implements BaseEvaluationStrategy. Loaded once at application startup via
XGBoostStrategy.load(); thereafter reused as a singleton per request.

Fallback behaviour:
  If the model's top-class probability is below FALLBACK_THRESHOLD (0.60),
  the result is delegated to RulesEngineStrategy so no response is degraded.

Service/trigger derivation:
  XGBoost outputs a severity label. Recommended services, triggers, and
  the evaluation flag are derived by running RulesEngineStrategy with a
  corrected context where the severity has been replaced by the ML prediction.
  This keeps all business logic in one place (the rules engine).
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Optional

import joblib

from app.db.models.enums import DisasterSeverity
from app.services.evaluation.base import (
    BaseEvaluationStrategy,
    EvaluationContext,
    EvaluationResult,
)
from app.services.evaluation.features import FEATURE_NAMES, build_feature_vector
from app.services.evaluation.rules_engine import RulesEngineStrategy

logger = logging.getLogger(__name__)


class XGBoostStrategy(BaseEvaluationStrategy):
    """
    XGBoost-based severity classifier (Phase 2).

    Usage:
        strategy = XGBoostStrategy(model_path="models/severity_classifier_v1.joblib")
        strategy.load()   # raises FileNotFoundError if artifact missing
        result = await strategy.evaluate(context)
    """

    STRATEGY_ID = "xgboost_v1"
    FALLBACK_THRESHOLD = 0.60

    def __init__(self, model_path: str) -> None:
        self._model_path = model_path
        self._model = None
        self._label_encoder = None
        self._fallback = RulesEngineStrategy()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> None:
        """
        Load the joblib artifact from disk.

        Raises:
            FileNotFoundError: if the artifact path does not exist.
        """
        path = Path(self._model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"XGBoost model artifact not found: {path.resolve()}"
            )
        artifact = joblib.load(path)
        self._model = artifact["model"]
        self._label_encoder = artifact["label_encoder"]

        # Sanity-check: ensure feature names match what the artifact expects
        artifact_features = artifact.get("feature_names", [])
        if artifact_features and artifact_features != FEATURE_NAMES:
            logger.warning(
                "XGBoost artifact feature_names differ from current FEATURE_NAMES. "
                "Re-train or check for version mismatch."
            )

        logger.info(
            "XGBoost model loaded: version=%s, classes=%s",
            artifact.get("version", "unknown"),
            list(self._label_encoder.classes_),
        )

    # ------------------------------------------------------------------
    # Strategy implementation
    # ------------------------------------------------------------------

    async def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        """
        Evaluate a disaster report using the XGBoost classifier.

        If the model is not loaded or confidence is below the threshold,
        falls back to RulesEngineStrategy.

        Args:
            context: Fully populated EvaluationContext (after enrichment).

        Returns:
            EvaluationResult with strategy_used set to either
            "xgboost_v1" (confident) or "rules_v1" (fallback).
        """
        if self._model is None:
            raise RuntimeError(
                "XGBoostStrategy.load() must be called before evaluate()"
            )

        vec = build_feature_vector(context)

        # Guard: vector length must match what the model was trained on
        if len(vec) != len(FEATURE_NAMES):
            raise RuntimeError(
                f"Feature vector length mismatch: got {len(vec)}, "
                f"expected {len(FEATURE_NAMES)}"
            )

        probas = self._model.predict_proba([vec])[0]   # shape (n_classes,)
        predicted_idx = int(probas.argmax())
        confidence = float(probas[predicted_idx])
        predicted_label: str = self._label_encoder.classes_[predicted_idx]  # e.g. "HIGH"

        # Low-confidence → delegate entirely to rules engine
        if confidence < self.FALLBACK_THRESHOLD:
            logger.debug(
                "XGBoost confidence %.3f below threshold %.2f for report %s — "
                "falling back to rules engine",
                confidence,
                self.FALLBACK_THRESHOLD,
                context.report_id,
            )
            return await self._fallback.evaluate(context)

        # Derive services/triggers/flag via rules engine with the ML-predicted severity
        corrected_severity = DisasterSeverity(predicted_label.upper())
        corrected_context = dataclasses.replace(context, severity=corrected_severity)
        rules_result = await self._fallback.evaluate(corrected_context)

        logger.debug(
            "XGBoost prediction for report %s: severity=%s confidence=%.3f",
            context.report_id,
            predicted_label,
            confidence,
        )

        return EvaluationResult(
            disaster_id=context.report_id,
            severity=predicted_label,                          # uppercase
            confidence=min(1.0, confidence),
            recommended_services=rules_result.recommended_services,
            trigger_deploy=rules_result.trigger_deploy,
            trigger_reroute=rules_result.trigger_reroute,
            trigger_evacuation=rules_result.trigger_evacuation,
            flag=rules_result.flag,
            strategy_used=self.STRATEGY_ID,
        )
