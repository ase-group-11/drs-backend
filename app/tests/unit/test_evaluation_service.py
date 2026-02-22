"""
Unit tests for DisasterEvaluationService.

All external dependencies are mocked — no DB, no HTTP.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from app.services.evaluation.service import DisasterEvaluationService
from app.services.evaluation.base import EvaluationResult


def make_report(**overrides):
    base = {
        "id": "report-uuid-1234",
        "user_id": "user-uuid-5678",
        "disaster_type": "fire",
        "severity": "high",
        "description": "Large fire on main street",
        "location": {"lat": 53.35, "lon": -6.26},
        "location_address": "Main St, Dublin",
        "people_affected": 20,
        "multiple_casualties": True,
        "structural_damage": False,
        "road_blocked": True,
        "report_status": "pending",
        "disaster_id": None,
        "reviewed_by_id": None,
        "reviewed_at": None,
        "rejection_reason": None,
        "created_at": "2026-02-22T10:00:00",
        "photo_count": 0,
    }
    base.update(overrides)
    return base


def make_eval_result() -> EvaluationResult:
    return EvaluationResult(
        disaster_id="report-uuid-1234",
        severity="HIGH",
        confidence=0.82,
        recommended_services=["fire_brigade", "ambulance", "police"],
        trigger_deploy=True,
        trigger_reroute=True,
        trigger_evacuation=True,
        flag="NORMAL",
        strategy_used="xgboost_v1",
    )


@pytest.fixture
def mock_report_repo():
    repo = AsyncMock()
    repo.get_report_by_id = AsyncMock(return_value=make_report())
    return repo


@pytest.fixture
def mock_eval_repo():
    repo = AsyncMock()
    repo.create_evaluation = AsyncMock(return_value=MagicMock())
    return repo


@pytest.fixture
def mock_strategy():
    strategy = AsyncMock()
    strategy.evaluate = AsyncMock(return_value=make_eval_result())
    return strategy


@pytest.fixture
def mock_enrichment():
    enrichment = AsyncMock()
    enrichment.enrich = AsyncMock(return_value=(
        {"flow": [{"congestion_level": "moderate"}], "source": "tomtom"},
        {"temperature_c": 15.0, "condition": "clear", "source": "mock"},
    ))
    return enrichment


@pytest.fixture
def service(mock_report_repo, mock_eval_repo, mock_strategy, mock_enrichment):
    return DisasterEvaluationService(
        report_repo=mock_report_repo,
        evaluation_repo=mock_eval_repo,
        strategy=mock_strategy,
        enrichment=mock_enrichment,
    )


@pytest.mark.asyncio
async def test_raises_404_when_report_not_found(mock_eval_repo, mock_strategy, mock_enrichment):
    repo = AsyncMock()
    repo.get_report_by_id = AsyncMock(return_value=None)
    svc = DisasterEvaluationService(
        report_repo=repo,
        evaluation_repo=mock_eval_repo,
        strategy=mock_strategy,
        enrichment=mock_enrichment,
    )
    with pytest.raises(HTTPException) as exc_info:
        await svc.evaluate("nonexistent-id")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_evaluation_persisted_to_repo(service, mock_eval_repo):
    await service.evaluate("report-uuid-1234")
    mock_eval_repo.create_evaluation.assert_called_once()
