"""
Integration tests for the Disaster Evaluation API.

Uses httpx.AsyncClient + dependency_overrides — no live DB or HTTP calls.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport


VALID_REPORT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
UNKNOWN_REPORT_ID = "00000000-0000-0000-0000-000000000000"

_VALID_EVAL_RESPONSE = {
    "disaster_id": VALID_REPORT_ID,
    "severity": "HIGH",
    "confidence": 0.82,
    "recommended_services": ["fire", "medical", "police"],
    "trigger_deploy": True,
    "trigger_reroute": True,
    "trigger_evacuation": False,
    "flag": "NORMAL",
    "strategy_used": "rules_v1",
    "evaluated_at": datetime.now(timezone.utc),
}


@pytest_asyncio.fixture
async def async_client():
    from app.main import app
    from app.api.v1.disaster_evaluation import (
        get_evaluation_service_dependency,
        set_evaluation_providers,
    )
    import app.api.v1.disaster_evaluation as eval_module

    set_evaluation_providers(MagicMock(), MagicMock())

    mock_service = AsyncMock()
    mock_service.evaluate = AsyncMock(return_value=_VALID_EVAL_RESPONSE)
    app.dependency_overrides[get_evaluation_service_dependency] = lambda: mock_service

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    eval_module._map_provider = None
    eval_module._traffic_provider = None
    eval_module._strategy = None


@pytest_asyncio.fixture
async def async_client_404():
    from app.main import app
    from app.api.v1.disaster_evaluation import (
        get_evaluation_service_dependency,
        set_evaluation_providers,
    )
    import app.api.v1.disaster_evaluation as eval_module
    from fastapi import HTTPException

    set_evaluation_providers(MagicMock(), MagicMock())

    not_found_service = AsyncMock()
    not_found_service.evaluate = AsyncMock(
        side_effect=HTTPException(status_code=404, detail="Not found")
    )
    app.dependency_overrides[get_evaluation_service_dependency] = lambda: not_found_service

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    eval_module._map_provider = None
    eval_module._traffic_provider = None
    eval_module._strategy = None


@pytest.mark.asyncio
async def test_evaluate_valid_report_returns_200(async_client):
    response = await async_client.post(
        f"/api/v1/disaster-evaluation/evaluate/{VALID_REPORT_ID}"
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_evaluate_unknown_report_returns_404(async_client_404):
    response = await async_client_404.post(
        f"/api/v1/disaster-evaluation/evaluate/{UNKNOWN_REPORT_ID}"
    )
    assert response.status_code == 404


