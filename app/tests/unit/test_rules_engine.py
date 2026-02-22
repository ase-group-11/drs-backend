"""
Unit tests for the RulesEngineStrategy.

No DB, no HTTP — all logic is pure and synchronous under the async wrapper.
"""

import pytest

from app.db.models.enums import DisasterSeverity, DisasterType
from app.services.evaluation.base import EvaluationContext
from app.services.evaluation.rules_engine import RulesEngineStrategy


def make_context(**overrides) -> EvaluationContext:
    """Build a minimal EvaluationContext with sensible defaults."""
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


# ---------------------------------------------------------------------------
# Service mapping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("dtype,expected_services", [
    (DisasterType.FIRE.value,       ["fire_brigade", "ambulance", "police"]),
    (DisasterType.FLOOD.value,      ["rescue", "police", "ambulance"]),
    (DisasterType.EARTHQUAKE.value, ["rescue", "ambulance", "fire_brigade", "police"]),
    (DisasterType.HURRICANE.value,  ["rescue", "police", "ambulance"]),
    (DisasterType.TORNADO.value,    ["rescue", "ambulance", "police"]),
    (DisasterType.TSUNAMI.value,    ["rescue", "police", "ambulance"]),
    (DisasterType.DROUGHT.value,    ["ambulance"]),
    (DisasterType.HEATWAVE.value,   ["ambulance", "police"]),
    (DisasterType.COLDWAVE.value,   ["ambulance", "police"]),
    (DisasterType.STORM.value,      ["rescue", "police", "ambulance"]),
    (DisasterType.OTHER.value,      ["police", "ambulance"]),
])
async def test_service_mapping_by_disaster_type(strategy, dtype, expected_services):
    ctx = make_context(disaster_type=dtype)
    result = await strategy.evaluate(ctx)
    # All expected services must be present (augmentation may add more)
    for svc in expected_services:
        assert svc in result.recommended_services


# ---------------------------------------------------------------------------
# Augmentation rules
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_structural_damage_adds_fire_brigade(strategy):
    # FLOOD base set does not include fire_brigade
    ctx = make_context(disaster_type=DisasterType.FLOOD.value, structural_damage=True)
    result = await strategy.evaluate(ctx)
    assert "fire_brigade" in result.recommended_services


@pytest.mark.asyncio
async def test_multiple_casualties_adds_rescue(strategy):
    # DROUGHT base set does not include rescue
    ctx = make_context(disaster_type=DisasterType.DROUGHT.value, multiple_casualties=True)
    result = await strategy.evaluate(ctx)
    assert "rescue" in result.recommended_services


@pytest.mark.asyncio
async def test_road_blocked_adds_police(strategy):
    # DROUGHT base set does not include police
    ctx = make_context(disaster_type=DisasterType.DROUGHT.value, road_blocked=True)
    result = await strategy.evaluate(ctx)
    assert "police" in result.recommended_services


@pytest.mark.asyncio
async def test_no_duplicate_services_when_already_present(strategy):
    ctx = make_context(
        disaster_type=DisasterType.FIRE.value,  # already has fire_brigade
        structural_damage=True,
    )
    result = await strategy.evaluate(ctx)
    assert result.recommended_services.count("fire_brigade") == 1


# ---------------------------------------------------------------------------
# Severity → triggers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_critical_severity_triggers_evacuation(strategy):
    ctx = make_context(severity=DisasterSeverity.CRITICAL)
    result = await strategy.evaluate(ctx)
    assert result.trigger_evacuation is True


@pytest.mark.asyncio
async def test_high_severity_and_casualties_triggers_evacuation(strategy):
    ctx = make_context(severity=DisasterSeverity.HIGH, multiple_casualties=True)
    result = await strategy.evaluate(ctx)
    assert result.trigger_evacuation is True


@pytest.mark.asyncio
async def test_medium_severity_no_evacuation(strategy):
    ctx = make_context(severity=DisasterSeverity.MEDIUM, multiple_casualties=False)
    result = await strategy.evaluate(ctx)
    assert result.trigger_evacuation is False


@pytest.mark.asyncio
async def test_tsunami_medium_triggers_evacuation(strategy):
    ctx = make_context(
        disaster_type=DisasterType.TSUNAMI.value,
        severity=DisasterSeverity.MEDIUM,
    )
    result = await strategy.evaluate(ctx)
    assert result.trigger_evacuation is True


@pytest.mark.asyncio
async def test_deploy_triggered_at_medium(strategy):
    ctx = make_context(severity=DisasterSeverity.MEDIUM)
    result = await strategy.evaluate(ctx)
    assert result.trigger_deploy is True


@pytest.mark.asyncio
async def test_deploy_not_triggered_at_low(strategy):
    ctx = make_context(severity=DisasterSeverity.LOW)
    result = await strategy.evaluate(ctx)
    assert result.trigger_deploy is False


# ---------------------------------------------------------------------------
# Flag determination
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_false_alarm_flag(strategy):
    ctx = make_context(
        severity=DisasterSeverity.LOW,
        people_affected=0,
        multiple_casualties=False,
        structural_damage=False,
        road_blocked=False,
        traffic_context=None,
        weather_context=None,
    )
    result = await strategy.evaluate(ctx)
    assert result.flag == "FALSE_ALARM"


@pytest.mark.asyncio
async def test_limited_data_flag(strategy):
    ctx = make_context(
        severity=DisasterSeverity.LOW,
        people_affected=0,
        traffic_context=None,
        weather_context=None,
        # LOW + no flags → base confidence 0.55, no boosts → < 0.70
    )
    result = await strategy.evaluate(ctx)
    # Either FALSE_ALARM or LIMITED_DATA — both indicate missing data
    assert result.flag in ("FALSE_ALARM", "LIMITED_DATA")


@pytest.mark.asyncio
async def test_pending_review_flag_high_no_flags(strategy):
    ctx = make_context(
        severity=DisasterSeverity.HIGH,
        multiple_casualties=False,
        structural_damage=False,
        road_blocked=False,
    )
    result = await strategy.evaluate(ctx)
    assert result.flag == "PENDING_REVIEW"


@pytest.mark.asyncio
async def test_normal_flag_with_supporting_evidence(strategy):
    ctx = make_context(
        severity=DisasterSeverity.HIGH,
        multiple_casualties=True,
        structural_damage=True,
    )
    result = await strategy.evaluate(ctx)
    assert result.flag == "NORMAL"


# ---------------------------------------------------------------------------
# Confidence adjustments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_night_hours_reduce_confidence(strategy):
    ctx_day = make_context(hour_of_day=12)
    ctx_night = make_context(hour_of_day=2)
    r_day = await strategy.evaluate(ctx_day)
    r_night = await strategy.evaluate(ctx_night)
    assert r_night.confidence < r_day.confidence


@pytest.mark.asyncio
async def test_heavy_traffic_boosts_confidence(strategy):
    traffic_heavy = {"flow": [{"congestion_level": "heavy"}], "source": "tomtom"}
    ctx_no_traffic = make_context(traffic_context=None)
    ctx_heavy = make_context(traffic_context=traffic_heavy)
    r_no = await strategy.evaluate(ctx_no_traffic)
    r_heavy = await strategy.evaluate(ctx_heavy)
    assert r_heavy.confidence > r_no.confidence


@pytest.mark.asyncio
async def test_confidence_clamped_to_one(strategy):
    ctx = make_context(
        severity=DisasterSeverity.CRITICAL,  # base 0.90
        multiple_casualties=True,            # +0.04
        structural_damage=True,              # +0.03
        road_blocked=True,                   # +0.02
        people_affected=100,                 # +0.02 + 0.03
        traffic_context={"flow": [{"congestion_level": "heavy"}]},  # +0.04
    )
    result = await strategy.evaluate(ctx)
    assert result.confidence <= 1.0


# ---------------------------------------------------------------------------
# Output format invariants
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_severity_is_uppercase(strategy):
    for sev in DisasterSeverity:
        ctx = make_context(severity=sev)
        result = await strategy.evaluate(ctx)
        assert result.severity == result.severity.upper(), (
            f"severity '{result.severity}' is not uppercase for {sev}"
        )


@pytest.mark.asyncio
async def test_strategy_used_is_rules_v1(strategy):
    ctx = make_context()
    result = await strategy.evaluate(ctx)
    assert result.strategy_used == "rules_v1"
