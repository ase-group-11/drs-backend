"""

Pytest fixtures for live map tests.

Provides:
- mock_redis_client: AsyncMock Redis for cache tests
- mock_map_provider: AsyncMock MapProvider for endpoint tests
- mock_live_map_service: AsyncMock LiveMapService injected via dependency override
- async_client: httpx.AsyncClient bound to the FastAPI app with overrides

"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport


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
    AsyncMock MapProvider.

    Default return for get_base_map_tiles provides a plausible tile config.
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
    provider.get_available_styles.return_value = [
        {"name": "streets", "id": "streets-v12"},
        {"name": "dark", "id": "dark-v11"},
    ]
    provider.is_valid_style.return_value = True
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
    return service


@pytest_asyncio.fixture
async def async_client(mock_map_provider, mock_live_map_service):
    """
    httpx.AsyncClient wired to the FastAPI app with dependency overrides.

    - Overrides get_live_map_service_dependency → mock_live_map_service
    - Sets the global _map_provider to mock_map_provider
    """
    from app.main import app  # adjust import if your app factory differs
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
