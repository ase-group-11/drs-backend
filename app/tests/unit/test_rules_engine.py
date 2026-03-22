"""
Unit tests for the RulesEngineStrategy.

No DB, no HTTP — all logic is pure and synchronous under the async wrapper.
"""

import pytest

from app.db.models.enums import DisasterSeverity, DisasterType
from app.services.evaluation.base import EvaluationContext
from app.services.evaluation.rules_engine import RulesEngineStrategy


def make_context(**overrides) -> EvaluationContext:
    defaults = dict(
        report_id="test-report-id",
        disaster_type=DisasterType.FIRE.value,
        severity=DisasterSeverity.MEDIUM,
        description="Test fire event",
        people_affected=5,
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


@pytest.fixture
def strategy():
    return RulesEngineStrategy()


@pytest.mark.asyncio
async def test_critical_severity_triggers_evacuation(strategy):
    ctx = make_context(severity=DisasterSeverity.CRITICAL)
    result = await strategy.evaluate(ctx)
    assert result.trigger_evacuation is True
