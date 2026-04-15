"""
conftest.py — Shared fixtures for the full test suite.

Sections:
  1. Live Map fixtures
     - mock_redis_client
     - mock_map_provider
     - mock_live_map_service
     - async_client

  2. Reroute Traffic fixtures
     - disaster_id, region_id
     - sample_blocked_roads, sample_impacted_vehicles
     - sample_tomtom_traffic_response, sample_tomtom_routing_response
     - sample_alternative_routes
     - mock_db_repository
     - mock_external_integration_service
     - mock_mapping_service
     - mock_notification_service
     - mock_publisher
"""

import asyncio
import uuid
from datetime import datetime, timezone
UTC = timezone.utc
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from faker import Faker
from httpx import AsyncClient, ASGITransport

fake = Faker()


# =============================================================================
# 1. Live Map fixtures
# =============================================================================

@pytest.fixture
def mock_redis_client():
    """
    AsyncMock Redis client.

    Supports: get, set, setex, delete, exists
    Default: get returns None (cache miss).
    """
    client = AsyncMock()
    client.get.return_value = None
    client.setex.return_value = True
    client.set.return_value = True
    client.delete.return_value = True
    return client


@pytest.fixture
def mock_map_provider():
    """
    Mixed AsyncMock/MagicMock MapProvider.

    Async methods (get_base_map_tiles, get_map_init) → AsyncMock.
    Sync methods (is_valid_style, get_available_styles) → MagicMock
    so that synchronous endpoint code can use their return values directly.
    """
    provider = AsyncMock()
    provider.style = "streets-v12"
    provider.get_base_map_tiles.return_value = {
        "tiles_url": (
            "https://api.mapbox.com/styles/v1/mapbox/streets-v12"
            "/tiles/256/{z}/{x}/{y}@2x?access_token=pk.test"
        ),
        "attribution": "© Mapbox © OpenStreetMap",
        "style": "streets-v12",
        "zoom": 12,
    }
    # These two are called synchronously in the endpoint, so use MagicMock
    provider.get_available_styles = MagicMock(return_value=[
        {"name": "streets", "id": "streets-v12"},
        {"name": "dark", "id": "dark-v11"},
    ])
    provider.is_valid_style = MagicMock(return_value=True)
    return provider


@pytest.fixture
def mock_live_map_service():
    """
    AsyncMock LiveMapService.

    All methods return sensible defaults; override in individual tests.
    """
    service = AsyncMock()
    service.get_base_map_tiles.return_value = {
        "tiles_url": "https://tiles.example/{z}/{x}/{y}.pbf",
        "attribution": "Test",
        "style": "streets-v12",
        "zoom": 12,
    }
    service.get_active_disasters.return_value = []
    service.get_traffic.return_value = None
    service.get_live_map_data.return_value = {
        "base_map": {
            "tiles_url": "https://tiles.example/{z}/{x}/{y}.pbf",
            "attribution": "Test",
            "style": "streets-v12",
            "zoom": 12,
        },
        "disasters": [],
        "traffic": None,
        "metadata": {
            "timestamp": 1000000.0,
            "cache_status": {"disasters": "live", "traffic": "cached"},
        },
    }
    # Attach a mock disaster_repo for pending-disasters endpoint
    service.disaster_repo = AsyncMock()
    service.disaster_repo.list_pending_disasters.return_value = []
    service.disaster_report_repo = AsyncMock()
    service.disaster_report_repo.get_pending_reports.return_value = []
    return service


@pytest_asyncio.fixture
async def async_client(mock_map_provider, mock_live_map_service):
    """
    httpx.AsyncClient wired to the FastAPI app with dependency overrides.

    - Overrides get_live_map_service_dependency → mock_live_map_service
    - Sets the global _map_provider to mock_map_provider
    """
    from app.main import app
    from app.api.v1.live_map import (
        get_live_map_service_dependency,
    )
    import app.api.v1.live_map as lm_module

    # Inject mocks
    app.dependency_overrides[get_live_map_service_dependency] = (
        lambda: mock_live_map_service
    )
    original_provider = lm_module._map_provider
    lm_module._map_provider = mock_map_provider

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    # Cleanup
    app.dependency_overrides.clear()
    lm_module._map_provider = original_provider


# =============================================================================
# 2. Reroute Traffic fixtures
# =============================================================================

# Event loop — single loop for the entire session
@pytest.fixture(scope="session")
def event_loop():
    """Single event loop shared across all async tests in the session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# Domain primitives

@pytest.fixture
def disaster_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def region_id() -> str:
    return "region-dublin-m50"


@pytest.fixture
def sample_blocked_roads() -> list[dict]:
    """Realistic blocked road segments (M50 motorway flood scenario)."""
    return [
        {
            "segment_id": "seg-m50-junction-6-7",
            "road_name": "M50 Northbound J6-J7",
            "start_lat": 53.3020,
            "start_lng": -6.3615,
            "end_lat": 53.3120,
            "end_lng": -6.3580,
            "reason": "flood",
        },
        {
            "segment_id": "seg-m50-junction-7-8",
            "road_name": "M50 Northbound J7-J8",
            "start_lat": 53.3120,
            "start_lng": -6.3580,
            "end_lat": 53.3250,
            "end_lng": -6.3540,
            "reason": "flood",
        },
    ]


@pytest.fixture
def sample_impacted_vehicles() -> list[dict]:
    return [
        {
            "user_id": f"user-{i}",
            "current_location": {"lat": fake.latitude(), "lng": fake.longitude()},
            "destination": {"lat": fake.latitude(), "lng": fake.longitude()},
            "current_route": f"route-original-{i}",
            "compliance_rate": 0.85,
        }
        for i in range(10)
    ]


@pytest.fixture
def sample_tomtom_traffic_response() -> dict:
    """Mocked TomTom Traffic Flow API response."""
    return {
        "flowSegmentData": [
            {
                "frc": "FRC0",
                "currentSpeed": 45,
                "freeFlowSpeed": 110,
                "currentTravelTime": 240,
                "freeFlowTravelTime": 120,
                "confidence": 0.9,
                "coordinates": {
                    "coordinate": [
                        {"latitude": 53.302, "longitude": -6.361},
                        {"latitude": 53.312, "longitude": -6.358},
                    ]
                },
            }
        ]
    }


@pytest.fixture
def sample_tomtom_routing_response() -> dict:
    """Mocked TomTom Routing API response with 3 alternative routes."""
    def make_route(travel_time: int, length_meters: int) -> dict:
        return {
            "summary": {
                "lengthInMeters": length_meters,
                "travelTimeInSeconds": travel_time,
                "trafficDelayInSeconds": travel_time // 5,
                "departureTime": datetime.now(UTC).isoformat(),
                "arrivalTime": datetime.now(UTC).isoformat(),
            },
            "legs": [
                {
                    "points": [
                        {"latitude": 53.302 + i * 0.01, "longitude": -6.361 + i * 0.005}
                        for i in range(5)
                    ]
                }
            ],
            "guidance": {"instructions": []},
        }

    return {
        "routes": [
            make_route(travel_time=900,  length_meters=12000),
            make_route(travel_time=1100, length_meters=14500),
            make_route(travel_time=1350, length_meters=17000),
        ]
    }


@pytest.fixture
def sample_alternative_routes(sample_tomtom_routing_response) -> list[dict]:
    """Parsed alternative routes ready for capacity analysis."""
    return [
        {
            "route_id": f"route-alt-{i}",
            "travel_time_seconds": r["summary"]["travelTimeInSeconds"],
            "length_meters": r["summary"]["lengthInMeters"],
            "segments": [f"seg-alt-{i}-{j}" for j in range(3)],
            "segment_capacities": {f"seg-alt-{i}-{j}": 300 for j in range(3)},
            "current_load": {f"seg-alt-{i}-{j}": 50 + i * 30 for j in range(3)},
        }
        for i, r in enumerate(sample_tomtom_routing_response["routes"])
    ]


# ---------------------------------------------------------------------------
# Service mocks
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db_repository():
    """Async mock for all database repository calls."""
    repo = AsyncMock()
    repo.get_blocked_roads = AsyncMock(return_value=[])
    repo.save_reroute_plan = AsyncMock(return_value={"id": str(uuid.uuid4())})
    repo.update_road_status = AsyncMock(return_value=True)
    repo.get_users_in_affected_area = AsyncMock(return_value=[])
    repo.log_event = AsyncMock(return_value=True)
    repo.apply_override = AsyncMock(return_value=True)
    repo.upsert_road_segments = AsyncMock(return_value=True)
    return repo


@pytest.fixture
def mock_external_integration_service():
    """Async mock for the External Integration Service (TomTom facade)."""
    svc = AsyncMock()
    svc.fetch_traffic_data = AsyncMock(return_value={"segments": [], "mode": "mock"})
    svc.get_traffic_conditions = AsyncMock()
    svc.get_directions = AsyncMock(return_value={"routes": []})
    svc.recompute_multi_incident_detours = AsyncMock(return_value={"routes": []})
    svc.recompute_with_overrides = AsyncMock(return_value={"routes": []})
    svc.health_check = AsyncMock(return_value={"mode": "mock", "circuit_breaker_state": "closed", "api_key_configured": False})
    svc.is_mock = True
    svc.mode = "mock"
    return svc


@pytest.fixture
def mock_mapping_service():
    """Async mock for the Mapping Service (GeoJSON → Mapbox GL JS via Socket.IO)."""
    svc = AsyncMock()
    svc.highlight_alternative_routes = AsyncMock(return_value={"status": "displayed"})
    svc.clear_detours = AsyncMock(return_value={"status": "cleared"})
    svc.send_updated_routes = AsyncMock(return_value={"status": "updated"})
    return svc


@pytest.fixture
def mock_notification_service():
    """Async mock for the Notification Service (kept for backward compat)."""
    svc = AsyncMock()
    svc.send_traffic_alerts = AsyncMock(return_value={"status": "sent"})
    svc.send_updated_reroute_recommendation = AsyncMock(return_value={"status": "sent"})
    svc.send_all_clear = AsyncMock(return_value={"status": "sent"})
    return svc


@pytest.fixture
def mock_publisher():
    """Async mock for the RabbitMQ ReroutePublisher."""
    pub = AsyncMock()
    pub.publish_reroute_triggered = AsyncMock(return_value=True)
    pub.publish_route_updated = AsyncMock(return_value=True)
    pub.publish_all_clear = AsyncMock(return_value=True)
    pub.is_connected = True
    return pub
