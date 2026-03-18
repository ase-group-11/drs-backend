"""
Unit tests for feature engineering.
"""

from app.db.models.enums import DisasterSeverity, DisasterType
from app.services.evaluation.base import EvaluationContext
from app.services.evaluation.features import build_feature_vector


def make_context(**overrides) -> EvaluationContext:
    defaults = dict(
        report_id="test-id",
        disaster_type=DisasterType.FIRE.value,
        severity=DisasterSeverity.MEDIUM,
        description="test",
        people_affected=10,
        multiple_casualties=False,
        structural_damage=False,
        road_blocked=False,
        lat=53.35,
        lon=-6.26,
        hour_of_day=12,
        traffic_context=None,
        weather_context=None,
    )
    defaults.update(overrides)
    return EvaluationContext(**defaults)


def test_vector_length():
    vec = build_feature_vector(make_context())
    assert len(vec) == 29
