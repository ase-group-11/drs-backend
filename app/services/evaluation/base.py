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
from datetime import datetime
from typing import Dict, List, Optional

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
    surveillance_context: Optional[dict] = None   # nearby cameras from OpenStreetMap
    population_context: Optional[dict] = None    # nearest place population from GeoNames
    infrastructure_context: Optional[dict] = None  # hospitals, fire stations, schools, police
    historical_context: Optional[dict] = None   # past outcomes of similar incidents from DB
    image_analysis_context: Optional[dict] = None  # CLIP disaster classification scores
    user_id: Optional[str] = None           # reporter's user_id — credibility signal
    photo_count: int = 0                    # number of photos attached — evidence quality
    description_length: int = 0            # len(description) — reporter effort signal
    created_at: Optional[datetime] = None  # when the report was submitted
    report_age_minutes: float = 0.0        # minutes between report creation and evaluation
    nearby_report_count: int = 0           # reports of same type within radius + window
    max_nearby_severity: Optional[str] = None  # highest severity seen in nearby reports


@dataclass
class EvaluationResult:
    """
    Fixed-schema output of the evaluation strategy.

    severity is ALWAYS uppercase (use DisasterSeverity.name, never .value).
    flag is ALWAYS one of: NORMAL, LIMITED_DATA, FALSE_ALARM, PENDING_REVIEW,
    DUPLICATE, ESCALATED, CORROBORATED.
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
    # Impact assessment — populated by DisasterEvaluationService after strategy call
    impact_radius_km: float = 0.0
    estimated_population: int = 0
    affected_roads: List[str] = field(default_factory=list)


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
