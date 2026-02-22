"""
Integration tests for the Disaster Evaluation API.

Uses httpx.AsyncClient + dependency_overrides — no live DB or HTTP calls.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_REPORT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
UNKNOWN_REPORT_ID = "00000000-0000-0000-0000-000000000000"

_VALID_EVAL_RESPONSE = {
    "disaster_id": VALID_REPORT_ID,
    "severity": "HIGH",
    "confidence": 0.82,
    "recommended_services": ["fire_brigade", "ambulance", "police"],
    "trigger_deploy": True,
    "trigger_reroute": True,
    "trigger_evacuation": False,
    "flag": "NORMAL",
    "strategy_used": "rules_v1",
    "evaluated_at": datetime.now(timezone.utc),
}


def make_mock_service(report_id: str = VALID_REPORT_ID):
    """Return an AsyncMock service that returns a valid evaluation dict."""
    svc = AsyncMock()
    svc.evaluate = AsyncMock(return_value=_VALID_EVAL_RESPONSE)
    return svc


def make_not_found_service():
    """Return a service that raises 404 for any report."""
    from fastapi import HTTPException
    svc = AsyncMock()
    svc.evaluate = AsyncMock(
        side_effect=HTTPException(status_code=404, detail="Not found")
    )
    return svc


@pytest_asyncio.fixture
async def async_client():
    """httpx client wired to FastAPI app with evaluation service overridden."""
    from app.main import app
    from app.api.v1.disaster_evaluation import (
        get_evaluation_service_dependency,
        set_evaluation_providers,
    )
    import app.api.v1.disaster_evaluation as eval_module

    mock_traffic = MagicMock()
    set_evaluation_providers(mock_traffic)

    mock_service = make_mock_service()
    app.dependency_overrides[get_evaluation_service_dependency] = lambda: mock_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    eval_module._traffic_provider = None


@pytest_asyncio.fixture
async def async_client_404():
    """Client where the service raises 404."""
    from app.main import app
    from app.api.v1.disaster_evaluation import (
        get_evaluation_service_dependency,
        set_evaluation_providers,
    )
    import app.api.v1.disaster_evaluation as eval_module

    mock_traffic = MagicMock()
    set_evaluation_providers(mock_traffic)

    not_found_service = make_not_found_service()
    app.dependency_overrides[get_evaluation_service_dependency] = (
        lambda: not_found_service
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    eval_module._traffic_provider = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluate_valid_report_returns_200(async_client):
    response = await async_client.post(
        f"/api/v1/disaster-evaluation/evaluate/{VALID_REPORT_ID}"
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_evaluate_response_has_all_required_fields(async_client):
    response = await async_client.post(
        f"/api/v1/disaster-evaluation/evaluate/{VALID_REPORT_ID}"
    )
    data = response.json()
    required = {
        "disaster_id", "severity", "confidence", "recommended_services",
        "trigger_deploy", "trigger_reroute", "trigger_evacuation",
        "flag", "strategy_used", "evaluated_at",
    }
    assert required.issubset(set(data.keys()))


@pytest.mark.asyncio
async def test_evaluate_unknown_report_returns_404(async_client_404):
    response = await async_client_404.post(
        f"/api/v1/disaster-evaluation/evaluate/{UNKNOWN_REPORT_ID}"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_severity_is_uppercase(async_client):
    response = await async_client.post(
        f"/api/v1/disaster-evaluation/evaluate/{VALID_REPORT_ID}"
    )
    severity = response.json()["severity"]
    assert severity in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert severity == severity.upper()


@pytest.mark.asyncio
async def test_confidence_is_float_between_0_and_1(async_client):
    response = await async_client.post(
        f"/api/v1/disaster-evaluation/evaluate/{VALID_REPORT_ID}"
    )
    confidence = response.json()["confidence"]
    assert isinstance(confidence, float)
    assert 0.0 <= confidence <= 1.0


@pytest.mark.asyncio
async def test_recommended_services_is_non_empty_list(async_client):
    response = await async_client.post(
        f"/api/v1/disaster-evaluation/evaluate/{VALID_REPORT_ID}"
    )
    services = response.json()["recommended_services"]
    assert isinstance(services, list)
    assert len(services) > 0


@pytest.mark.asyncio
async def test_flag_is_valid_value(async_client):
    response = await async_client.post(
        f"/api/v1/disaster-evaluation/evaluate/{VALID_REPORT_ID}"
    )
    flag = response.json()["flag"]
    assert flag in ("NORMAL", "LIMITED_DATA", "FALSE_ALARM", "PENDING_REVIEW")


@pytest.mark.asyncio
async def test_evaluated_at_is_valid_iso_timestamp(async_client):
    response = await async_client.post(
        f"/api/v1/disaster-evaluation/evaluate/{VALID_REPORT_ID}"
    )
    ts = response.json()["evaluated_at"]
    # Should parse without error
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert isinstance(parsed, datetime)


@pytest.mark.asyncio
async def test_503_when_traffic_provider_not_set():
    """Without a traffic provider the dependency factory should return 503."""
    from app.main import app
    from app.db.session import get_db
    from app.api.v1.disaster_evaluation import get_evaluation_service_dependency
    import app.api.v1.disaster_evaluation as eval_module

    # Ensure provider is None
    eval_module._traffic_provider = None

    # Mock get_db so DB teardown doesn't hit the real asyncpg/greenlet stack
    async def mock_get_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = mock_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/disaster-evaluation/evaluate/{VALID_REPORT_ID}"
        )

    app.dependency_overrides.clear()
    assert response.status_code == 503
