"""
Base dataclasses and abstract strategy for the evaluation service.

EvaluationContext  — input to any strategy
EvaluationResult   — output from any strategy (matches API schema)
BaseEvaluationStrategy — ABC that all strategies must implement

To swap Phase 1 → Phase 2: create xgboost_strategy.py implementing
BaseEvaluationStrategy and change one line in the router dependency factory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from app.db.models.enums import DisasterSeverity


@dataclass
class EvaluationContext:
    """
    All data available to the evaluation strategy.

    Populated by DisasterEvaluationService from the report dict
    and the enrichment pipeline output.
    """

    report_id: str
    disaster_type: str          # DisasterType.value (lowercase)
    severity: DisasterSeverity  # enum instance
    description: str
    people_affected: int
    multiple_casualties: bool
    structural_damage: bool
    road_blocked: bool
    lat: Optional[float]
    lon: Optional[float]
    hour_of_day: int            # UTC hour [0, 23] for time-of-day rules
    traffic_context: Optional[dict]
    weather_context: Optional[dict]


@dataclass
class EvaluationResult:
    """
    Fixed-schema output of the evaluation strategy.

    severity is ALWAYS uppercase (use DisasterSeverity.name, never .value).
    flag is ALWAYS one of: NORMAL, LIMITED_DATA, FALSE_ALARM, PENDING_REVIEW.
    """

    disaster_id: str
    severity: str               # uppercase — e.g. "HIGH"
    confidence: float           # clamped to [0.0, 1.0]
    recommended_services: List[str]
    trigger_deploy: bool
    trigger_reroute: bool
    trigger_evacuation: bool
    flag: str                   # EvaluationFlag.name (uppercase)
    strategy_used: str          # e.g. "rules_v1"


class BaseEvaluationStrategy(ABC):
    """
    Abstract base class for evaluation strategies.

    Implement this to swap Phase 1 (rules) for Phase 2 (XGBoost)
    without modifying any API or service code.
    """

    @abstractmethod
    async def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        """
        Evaluate a disaster report and return a structured result.

        Args:
            context: All available data for this report

        Returns:
            EvaluationResult with fixed schema
        """
        ...
