"""
Phase 1 evaluation strategy: heuristic rules engine.

Implements BaseEvaluationStrategy using a deterministic rule set.
To replace with XGBoost in Phase 2, create xgboost_strategy.py and
swap the import in the router dependency factory — no other changes needed.
"""

from __future__ import annotations

from typing import List

from app.db.models.enums import DisasterSeverity, DisasterType, EvaluationFlag
from app.services.evaluation.base import (
    BaseEvaluationStrategy,
    EvaluationContext,
    EvaluationResult,
)

STRATEGY_ID = "rules_v1"

# ---------------------------------------------------------------------------
# Service mapping by disaster type
# ---------------------------------------------------------------------------
_SERVICE_MAP: dict[str, List[str]] = {
    DisasterType.FIRE.value:       ["fire", "medical", "police"],
    DisasterType.FLOOD.value:      ["medical", "police"],
    DisasterType.EARTHQUAKE.value: ["medical", "fire", "police"],
    DisasterType.HURRICANE.value:  ["medical", "police"],
    DisasterType.TORNADO.value:    ["medical", "police"],
    DisasterType.TSUNAMI.value:    ["medical", "police"],
    DisasterType.DROUGHT.value:    ["medical"],
    DisasterType.HEATWAVE.value:   ["medical", "police"],
    DisasterType.COLDWAVE.value:   ["medical", "police"],
    DisasterType.STORM.value:      ["medical", "police"],
    DisasterType.OTHER.value:      ["police", "medical"],
}

# Base confidence by severity
_BASE_CONFIDENCE: dict[DisasterSeverity, float] = {
    DisasterSeverity.LOW:      0.55,
    DisasterSeverity.MEDIUM:   0.65,
    DisasterSeverity.HIGH:     0.78,
    DisasterSeverity.CRITICAL: 0.90,
}

# Severity ordering for comparisons
_SEVERITY_ORDER = [
    DisasterSeverity.LOW,
    DisasterSeverity.MEDIUM,
    DisasterSeverity.HIGH,
    DisasterSeverity.CRITICAL,
]

# Disaster types that trigger evacuation at MEDIUM severity or above
_EVACUATION_TYPES = {DisasterType.TSUNAMI.value, DisasterType.HURRICANE.value}


def _severity_gte(sev: DisasterSeverity, threshold: DisasterSeverity) -> bool:
    return _SEVERITY_ORDER.index(sev) >= _SEVERITY_ORDER.index(threshold)


class RulesEngineStrategy(BaseEvaluationStrategy):
    """
    Deterministic heuristic rules engine (Phase 1).

    All logic is pure — no DB or HTTP calls — making it fully unit-testable.
    """

    async def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        services = self._determine_services(context)
        confidence = self._calculate_confidence(context)
        triggers = self._calculate_triggers(context, services)
        flag = self._determine_flag(context, confidence)

        return EvaluationResult(
            disaster_id=context.report_id,
            severity=context.severity.name,          # UPPERCASE
            confidence=confidence,
            recommended_services=services,
            trigger_deploy=triggers["deploy"],
            trigger_reroute=triggers["reroute"],
            trigger_evacuation=triggers["evacuation"],
            flag=flag,
            strategy_used=STRATEGY_ID,
        )

    # ------------------------------------------------------------------
    # Internal rule methods
    # ------------------------------------------------------------------

    def _determine_services(self, ctx: EvaluationContext) -> List[str]:
        """Map disaster type to base services, then augment with flag rules."""
        services: List[str] = list(
            _SERVICE_MAP.get(ctx.disaster_type, _SERVICE_MAP[DisasterType.OTHER.value])
        )

        # Augmentation rules (maintain order, prepend if missing)
        if ctx.structural_damage and "fire" not in services:
            services.insert(0, "fire")

        if ctx.multiple_casualties and "medical" not in services:
            services.insert(0, "medical")

        if ctx.road_blocked and "police" not in services:
            services.append("police")

        return services

    def _calculate_confidence(self, ctx: EvaluationContext) -> float:
        """Apply base confidence + adjustments, clamped to [0.0, 1.0]."""
        score = _BASE_CONFIDENCE.get(ctx.severity, 0.55)

        # Positive adjustments
        if ctx.multiple_casualties:
            score += 0.04
        if ctx.structural_damage:
            score += 0.03
        if ctx.road_blocked:
            score += 0.02
        if ctx.people_affected > 10:
            score += 0.02
        if ctx.people_affected > 50:
            score += 0.03  # cumulative with the >10 bonus

        # Evidence quality boosts
        img_ctx = ctx.image_analysis_context or {}
        img_score = img_ctx.get("disaster_score", 0.0)
        if img_ctx.get("analysed_count", 0) > 0:
            # CLIP confirmed disaster visuals — graduated boost
            if img_score >= 0.75:
                score += 0.08
            elif img_score >= 0.50:
                score += 0.05
            else:
                score += 0.02
        elif ctx.photo_count > 0:
            # Photos present but not analysed (fallback)
            score += 0.03
        if ctx.description_length > 100:
            score += 0.02

        # Traffic context boost
        traffic_congestion = self._get_congestion_level(ctx.traffic_context)
        if traffic_congestion in ("heavy", "severe"):
            score += 0.04

        # Surveillance proximity boost — more cameras near the incident
        # means the area is monitored and the report is harder to fabricate
        camera_count = (ctx.surveillance_context or {}).get("camera_count", 0)
        if camera_count >= 3:
            score += 0.04
        elif camera_count >= 1:
            score += 0.02

        # Historical outcomes adjustment
        # High false alarm rate in this area → penalise; high verification rate → boost
        hist = ctx.historical_context or {}
        if hist.get("total", 0) >= 3:
            false_alarm_rate = hist.get("false_alarm_rate", 0.0)
            if false_alarm_rate >= 0.5:
                score -= 0.05  # this area has a history of false alarms
            elif false_alarm_rate <= 0.1:
                score += 0.03  # this area has a strong verification history

        # Night-time penalty
        if ctx.hour_of_day >= 22 or ctx.hour_of_day < 6:
            score -= 0.03

        return max(0.0, min(1.0, score))

    def _calculate_triggers(
        self, ctx: EvaluationContext, services: List[str]
    ) -> dict:
        sev = ctx.severity

        trigger_deploy = _severity_gte(sev, DisasterSeverity.MEDIUM)

        trigger_reroute = ctx.road_blocked or _severity_gte(sev, DisasterSeverity.HIGH)

        trigger_evacuation = (
            sev == DisasterSeverity.CRITICAL
            or (_severity_gte(sev, DisasterSeverity.HIGH) and ctx.multiple_casualties)
            or (ctx.disaster_type in _EVACUATION_TYPES and _severity_gte(sev, DisasterSeverity.MEDIUM))
        )

        return {
            "deploy": trigger_deploy,
            "reroute": trigger_reroute,
            "evacuation": trigger_evacuation,
        }

    def _determine_flag(self, ctx: EvaluationContext, confidence: float) -> str:
        """Evaluate flags in priority order, return first match."""
        sev = ctx.severity
        any_flag = ctx.multiple_casualties or ctx.structural_damage or ctx.road_blocked

        # FALSE_ALARM: low severity, no flags, zero casualties, low confidence
        if (
            sev == DisasterSeverity.LOW
            and not any_flag
            and ctx.people_affected == 0
            and confidence < 0.58
        ):
            return EvaluationFlag.FALSE_ALARM.name

        # ESCALATED: severity is higher than anything seen in the window
        if ctx.max_nearby_severity is not None:
            max_nearby = DisasterSeverity(ctx.max_nearby_severity)
            if _severity_gte(sev, DisasterSeverity.MEDIUM) and _SEVERITY_ORDER.index(sev) > _SEVERITY_ORDER.index(max_nearby):
                return EvaluationFlag.ESCALATED.name

        # CORROBORATED: 2+ independent reports confirm the same event
        if ctx.nearby_report_count >= 2:
            return EvaluationFlag.CORROBORATED.name

        # DUPLICATE: a report of the same type exists nearby within the consolidation window
        if ctx.nearby_report_count >= 1 and ctx.report_age_minutes < 15:
            return EvaluationFlag.DUPLICATE.name

        # LIMITED_DATA: either enrichment source unavailable
        if ctx.traffic_context is None or ctx.weather_context is None:
            return EvaluationFlag.LIMITED_DATA.name

        # PENDING_REVIEW: high/critical but no supporting evidence
        if _severity_gte(sev, DisasterSeverity.HIGH) and not any_flag:
            return EvaluationFlag.PENDING_REVIEW.name

        return EvaluationFlag.NORMAL.name

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_congestion_level(traffic_ctx: dict | None) -> str | None:
        """Extract the dominant congestion level from traffic context."""
        if not traffic_ctx:
            return None
        flow = traffic_ctx.get("flow", [])
        if not flow:
            return None
        # Use the first segment's congestion level as representative
        return flow[0].get("congestion_level") if flow else None
