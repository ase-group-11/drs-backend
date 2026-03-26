"""
Unit tests for XGBoostStrategy.
"""

import pytest
import numpy as np
from unittest.mock import MagicMock

from app.db.models.enums import DisasterSeverity, DisasterType
from app.services.evaluation.base import EvaluationContext
from app.services.evaluation.xgboost_strategy import XGBoostStrategy


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


def make_loaded_strategy(probas, classes):
    strategy = XGBoostStrategy(model_path="fake/path.joblib")
    mock_model = MagicMock()
    mock_model.predict_proba.return_value = np.array(probas)
    mock_le = MagicMock()
    mock_le.classes_ = np.array(classes)
    strategy._model = mock_model
    strategy._label_encoder = mock_le
    return strategy


def test_load_raises_file_not_found_for_missing_path():
    strategy = XGBoostStrategy(model_path="/does/not/exist/model.joblib")
    with pytest.raises(FileNotFoundError, match="not found"):
        strategy.load()


@pytest.mark.asyncio
async def test_high_confidence_uses_xgboost_strategy_id():
    strategy = make_loaded_strategy(
        probas=[[0.05, 0.75, 0.10, 0.10]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    result = await strategy.evaluate(make_context())
    assert result.strategy_used == "xgboost_v1"


@pytest.mark.asyncio
async def test_low_confidence_falls_back_to_rules():
    strategy = make_loaded_strategy(
        probas=[[0.35, 0.30, 0.20, 0.15]],
        classes=["CRITICAL", "HIGH", "LOW", "MEDIUM"],
    )
    result = await strategy.evaluate(make_context())
    assert result.strategy_used == "rules_v1"
