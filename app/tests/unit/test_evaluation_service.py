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
        "photo_urls": [],
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
    repo.get_recent_reports_near = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_disaster_repo():
    repo = AsyncMock()
    created = MagicMock()
    created.id = "disaster-uuid-9999"
    repo.create_disaster = AsyncMock(return_value=created)
    repo.get_active_disaster_near = AsyncMock(return_value=None)
    repo.get_reports_by_disaster_id = AsyncMock(return_value=[])
    repo.update_evaluation_metadata = AsyncMock()
    repo.get_historical_outcomes = AsyncMock(return_value={
        "total": 5,
        "verified_count": 4,
        "false_alarm_count": 1,
        "false_alarm_rate": 0.2,
        "avg_confidence": 0.81,
        "source": "historical_db",
    })
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
        {"flow": [{"congestion_level": "moderate"}], "source": "livemap"},
        {"temperature_c": 15.0, "condition": "clear", "source": "openweathermap"},
        {"camera_count": 2, "cameras": [], "radius_m": 500, "source": "openstreetmap"},
        {"nearest_place": "Dublin", "population": 553165, "distance_km": 1.2, "source": "geonames"},
        {"facilities": [{"name": "St James's Hospital", "amenity": "hospital"}], "count": 1, "radius_m": 1000, "source": "openstreetmap"},
        None,  # image_analysis_context
    ))
    return enrichment


@pytest.fixture
def mock_user_repo():
    repo = AsyncMock()
    user = MagicMock()
    user.phone_number = "+353871234567"
    repo.get_by_id = AsyncMock(return_value=user)
    return repo


@pytest.fixture
def service(mock_report_repo, mock_disaster_repo, mock_strategy, mock_enrichment, mock_user_repo):
    return DisasterEvaluationService(
        report_repo=mock_report_repo,
        disaster_repo=mock_disaster_repo,
        strategy=mock_strategy,
        enrichment=mock_enrichment,
        user_repo=mock_user_repo,
    )


@pytest.mark.asyncio
async def test_raises_404_when_report_not_found(mock_disaster_repo, mock_strategy, mock_enrichment, mock_user_repo):
    repo = AsyncMock()
    repo.get_report_by_id = AsyncMock(return_value=None)
    repo.get_recent_reports_near = AsyncMock(return_value=[])
    svc = DisasterEvaluationService(
        report_repo=repo,
        disaster_repo=mock_disaster_repo,
        strategy=mock_strategy,
        enrichment=mock_enrichment,
        user_repo=mock_user_repo,
    )
    with pytest.raises(HTTPException) as exc_info:
        await svc.evaluate("nonexistent-id")
    assert exc_info.value.status_code == 404


