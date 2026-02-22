"""
Unit tests for app/services/evaluation/xgboost_strategy.py

Uses MagicMock to avoid any real model file on disk. All tests are fully
isolated from the file system and the database.
"""

from __future__ import annotations

import pytest
import numpy as np
from unittest.mock import MagicMock, patch, AsyncMock

from app.db.models.enums import DisasterSeverity, DisasterType
from app.services.evaluation.base import EvaluationContext, EvaluationResult
from app.services.evaluation.xgboost_strategy import XGBoostStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_context(**overrides) -> EvaluationContext:
    defaults = dict(
        report_id="test-report-id",
        disaster_type=DisasterType.FIRE.value,
        severity=DisasterSeverity.HIGH,
        description="test",
        people_affected=50,
        multiple_casualties=True,
        structural_damage=True,
        road_blocked=False,
        lat=53.35,
        lon=-6.26,
        hour_of_day=14,
        traffic_context={"flow": [{"congestion_level": "moderate"}], "source": "test"},
        weather_context={"temperature_c": 12.0, "wind_speed_kmh": 20.0, "condition": "rain"},
    )
    defaults.update(overrides)
    return EvaluationContext(**defaults)


def _make_loaded_strategy(
    probas: list[list[float]],
    classes: list[str],
    model_path: str = "fake/path.joblib",
) -> XGBoostStrategy:
    """
    Return an XGBoostStrategy that behaves as if load() was called successfully,
    with a mocked model whose predict_proba returns the given probas array.
    """
    strategy = XGBoostStrategy(model_path=model_path)

    mock_model = MagicMock()
    mock_model.predict_proba.return_value = np.array(probas)

    mock_le = MagicMock()
    mock_le.classes_ = np.array(classes)

    strategy._model = mock_model
    strategy._label_encoder = mock_le
    return strategy


# ---------------------------------------------------------------------------
# load() lifecycle
# ---------------------------------------------------------------------------


def test_load_raises_file_not_found_for_missing_path():
    """load() with a non-existent path must raise FileNotFoundError."""
    strategy = XGBoostStrategy(model_path="/does/not/exist/model.joblib")
    with pytest.raises(FileNotFoundError, match="not found"):
        strategy.load()


def test_evaluate_before_load_raises_runtime_error():
    """evaluate() before load() must raise RuntimeError."""
    strategy = XGBoostStrategy(model_path="irrelevant")
    ctx = make_context()
    with pytest.raises(RuntimeError, match="load\\(\\) must be called"):
        import asyncio
        asyncio.get_event_loop().run_until_complete(strategy.evaluate(ctx))


# ---------------------------------------------------------------------------
# Confident prediction path (confidence ≥ 0.60)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_confidence_uses_xgboost_strategy_id():
    """Confidence ≥ 0.60 → strategy_used == 'xgboost_v1'."""
    # HIGH has probability 0.75
    strategy = _make_loaded_strategy(
        probas=[[0.05, 0.75, 0.10, 0.10]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    ctx = make_context(severity=DisasterSeverity.HIGH)
    result = await strategy.evaluate(ctx)
    assert result.strategy_used == "xgboost_v1"


@pytest.mark.asyncio
async def test_high_confidence_severity_is_uppercase():
    """Severity in result must be uppercase."""
    strategy = _make_loaded_strategy(
        probas=[[0.05, 0.75, 0.10, 0.10]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    ctx = make_context()
    result = await strategy.evaluate(ctx)
    assert result.severity == result.severity.upper()
    assert result.severity in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


@pytest.mark.asyncio
async def test_high_confidence_recommended_services_nonempty():
    """recommended_services must be a non-empty list for a fire HIGH event."""
    strategy = _make_loaded_strategy(
        probas=[[0.05, 0.75, 0.10, 0.10]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    ctx = make_context(disaster_type=DisasterType.FIRE.value, severity=DisasterSeverity.HIGH)
    result = await strategy.evaluate(ctx)
    assert isinstance(result.recommended_services, list)
    assert len(result.recommended_services) > 0


@pytest.mark.asyncio
async def test_confidence_clamped_to_one():
    """confidence in the result must not exceed 1.0."""
    # probas already sum to 1.0, but make sure the clamp is tested
    strategy = _make_loaded_strategy(
        probas=[[0.00, 0.99, 0.01, 0.00]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    ctx = make_context()
    result = await strategy.evaluate(ctx)
    assert result.confidence <= 1.0


@pytest.mark.asyncio
async def test_predicted_severity_overrides_context_severity():
    """
    The ML-predicted label should appear in the result, not the context severity.
    Context says LOW, model predicts CRITICAL with high confidence.
    """
    strategy = _make_loaded_strategy(
        probas=[[0.85, 0.05, 0.05, 0.05]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    ctx = make_context(severity=DisasterSeverity.LOW)
    result = await strategy.evaluate(ctx)
    assert result.severity == "CRITICAL"


# ---------------------------------------------------------------------------
# Fallback path (confidence < 0.60)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_low_confidence_falls_back_to_rules():
    """Confidence < 0.60 → strategy_used == 'rules_v1' (fallback)."""
    # Spread probabilities so max is 0.35 (below 0.60)
    strategy = _make_loaded_strategy(
        probas=[[0.35, 0.30, 0.20, 0.15]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    ctx = make_context()
    result = await strategy.evaluate(ctx)
    assert result.strategy_used == "rules_v1"


@pytest.mark.asyncio
async def test_exactly_at_threshold_uses_xgboost():
    """confidence == FALLBACK_THRESHOLD (0.60) → xgboost_v1 (not fallback)."""
    strategy = _make_loaded_strategy(
        probas=[[0.05, 0.60, 0.20, 0.15]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    ctx = make_context()
    result = await strategy.evaluate(ctx)
    assert result.strategy_used == "xgboost_v1"


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_optional_context_fields_do_not_crash():
    """
    None lat/lon, traffic_context, weather_context must not raise an exception.
    Feature extractor must handle missing optional fields gracefully.
    """
    strategy = _make_loaded_strategy(
        probas=[[0.05, 0.75, 0.10, 0.10]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    ctx = make_context(lat=None, lon=None, traffic_context=None, weather_context=None)
    # Should not raise
    result = await strategy.evaluate(ctx)
    assert result is not None


@pytest.mark.asyncio
async def test_result_is_evaluation_result_instance():
    """evaluate() must return an EvaluationResult."""
    strategy = _make_loaded_strategy(
        probas=[[0.05, 0.75, 0.10, 0.10]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    ctx = make_context()
    result = await strategy.evaluate(ctx)
    assert isinstance(result, EvaluationResult)


@pytest.mark.asyncio
async def test_disaster_id_matches_report_id():
    """EvaluationResult.disaster_id must equal context.report_id."""
    strategy = _make_loaded_strategy(
        probas=[[0.05, 0.75, 0.10, 0.10]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    ctx = make_context(report_id="specific-id-xyz")
    result = await strategy.evaluate(ctx)
    assert result.disaster_id == "specific-id-xyz"
