"""

Tests for the live map system:

1. MapProvider unit tests          — tile URL generation, styles, validation
2. LiveMapService unit tests       — caching patterns, fallback behaviour
3. Live Map API endpoint tests     — HTTP layer, request validation, error handling

"""

import json
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import status

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bounds():
    """Standard Dublin bounding box for tests."""
    return "53.30,-6.35,53.40,-6.20"


def _center():
    return {"lat": 53.3498, "lon": -6.2603}


FAKE_MAPBOX_KEY = "pk.test_mapbox_key_12345"


# =========================================================================
# 1.  MapProvider Unit Tests
# =========================================================================


class TestMapProviderInit:
    """Tests for MapProvider constructor and configuration."""

    def test_init_stores_api_key(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        assert provider.api_key == FAKE_MAPBOX_KEY

    def test_init_default_style(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        assert provider.style == "streets-v12"

    def test_init_custom_style(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY, style="dark-v11")
        assert provider.style == "dark-v11"

    def test_init_default_tilesize(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        assert provider.tilesize == 256

    def test_init_retina_default_true(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        assert provider.retina is True

    def test_init_empty_key_does_not_crash(self):
        """App should start even without Mapbox key (features just won't work)."""
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key="")
        assert provider.api_key == ""


class TestMapProviderGetBaseMapTiles:
    """Tests for get_base_map_tiles tile URL generation."""

    async def test_returns_tiles_url_with_placeholders(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        result = await provider.get_base_map_tiles(center=_center(), zoom=12)

        assert "{z}" in result["tiles_url"]
        assert "{x}" in result["tiles_url"]
        assert "{y}" in result["tiles_url"]

    async def test_tiles_url_contains_api_key(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        result = await provider.get_base_map_tiles(center=_center(), zoom=12)

        assert FAKE_MAPBOX_KEY in result["tiles_url"]

    async def test_tiles_url_contains_style(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY, style="dark-v11")
        result = await provider.get_base_map_tiles(center=_center(), zoom=12)

        assert "dark-v11" in result["tiles_url"]

    async def test_retina_suffix_present(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY, retina=True)
        result = await provider.get_base_map_tiles(center=_center(), zoom=12)

        assert "@2x" in result["tiles_url"]

    async def test_no_retina_suffix_when_disabled(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY, retina=False)
        result = await provider.get_base_map_tiles(center=_center(), zoom=12)

        assert "@2x" not in result["tiles_url"]

    async def test_returns_attribution(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        result = await provider.get_base_map_tiles(center=_center(), zoom=12)

        assert "attribution" in result
        assert "Mapbox" in result["attribution"]

    async def test_returns_style_name(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY, style="satellite-v9")
        result = await provider.get_base_map_tiles(center=_center(), zoom=12)

        assert result["style"] == "satellite-v9"

    async def test_style_override_in_call(self):
        """Per-request style override takes precedence over default."""
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY, style="streets-v12")
        result = await provider.get_base_map_tiles(
            center=_center(), zoom=12, style="dark-v11"
        )

        assert "dark-v11" in result["tiles_url"]
        assert result["style"] == "dark-v11"

    async def test_zoom_clamped_to_max(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        result = await provider.get_base_map_tiles(center=_center(), zoom=25)

        assert result["zoom"] == 20  # MAX_ZOOM

    async def test_zoom_clamped_to_min(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        result = await provider.get_base_map_tiles(center=_center(), zoom=0)

        assert result["zoom"] == 1  # MIN_ZOOM

    async def test_center_passed_through(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        center = {"lat": 40.7128, "lon": -74.0060}
        result = await provider.get_base_map_tiles(center=center, zoom=10)

        assert result["center"] == center

    async def test_tilesize_in_url(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY, tilesize=512)
        result = await provider.get_base_map_tiles(center=_center(), zoom=12)

        assert "/512/" in result["tiles_url"]


class TestMapProviderGetMapInit:
    """Tests for get_map_init initialization config."""

    async def test_returns_center(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        result = await provider.get_map_init(lat=53.35, lon=-6.26, zoom=12)

        assert result["center"]["lat"] == 53.35
        assert result["center"]["lon"] == -6.26

    async def test_returns_zoom(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        result = await provider.get_map_init(lat=53.35, lon=-6.26, zoom=14)

        assert result["zoom"] == 14

    async def test_returns_bounds(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        result = await provider.get_map_init(lat=53.35, lon=-6.26, zoom=12)

        bounds = result["bounds"]
        assert bounds["south"] < 53.35 < bounds["north"]
        assert bounds["west"] < -6.26 < bounds["east"]

    async def test_returns_tiles_config(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        result = await provider.get_map_init(lat=53.35, lon=-6.26, zoom=12)

        assert "tiles" in result
        assert "tiles_url" in result["tiles"]


class TestMapProviderStyles:
    """Tests for style listing and validation."""

    def test_get_available_styles_returns_list(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        styles = provider.get_available_styles()

        assert isinstance(styles, list)
        assert len(styles) > 0

    def test_each_style_has_name_and_id(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        for s in provider.get_available_styles():
            assert "name" in s
            assert "id" in s

    def test_streets_style_present(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        ids = [s["id"] for s in provider.get_available_styles()]
        assert "streets-v12" in ids

    def test_is_valid_style_true(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        assert provider.is_valid_style("dark-v11") is True

    def test_is_valid_style_false(self):
        from app.providers.map_provider import MapProvider

        provider = MapProvider(api_key=FAKE_MAPBOX_KEY)
        assert provider.is_valid_style("invalid-style-999") is False


# =========================================================================
# 2.  LiveMapService Unit Tests (caching / fallback)
# =========================================================================


class TestLiveMapServiceBaseTiles:
    """Service delegates to map provider for tiles."""

    async def test_get_base_map_tiles_calls_provider(self, mock_redis_client):
        map_provider = AsyncMock()
        map_provider.get_base_map_tiles.return_value = {
            "tiles_url": "https://tiles.example/{z}/{x}/{y}.pbf",
            "attribution": "Example",
        }
        traffic_provider = AsyncMock()
        repo = AsyncMock()

        from app.services.live_map_service import LiveMapService

        svc = LiveMapService(repo, AsyncMock(), mock_redis_client, map_provider, traffic_provider)
        out = await svc.get_base_map_tiles(center=_center(), zoom=12)

        assert out["tiles_url"].startswith("https://")
        map_provider.get_base_map_tiles.assert_awaited_once()

    async def test_get_base_map_tiles_raises_on_provider_failure(self, mock_redis_client):
        map_provider = AsyncMock()
        map_provider.get_base_map_tiles.side_effect = ConnectionError("Mapbox down")
        traffic_provider = AsyncMock()
        repo = AsyncMock()

        from app.services.live_map_service import LiveMapService

        svc = LiveMapService(repo, AsyncMock(), mock_redis_client, map_provider, traffic_provider)

        with pytest.raises(ConnectionError):
            await svc.get_base_map_tiles(center=_center(), zoom=12)


class TestLiveMapServiceDisasterCache:
    """Cache-aside pattern for disasters."""

    async def test_cache_hit_returns_cached_skips_db(self, mock_redis_client):
        bounds = _bounds()
        cached = [{"id": "d1", "type": "flood"}, {"id": "d2", "type": "fire"}]
        mock_redis_client.get.return_value = json.dumps(cached)

        repo = AsyncMock()
        map_provider = AsyncMock()
        traffic_provider = AsyncMock()

        from app.services.live_map_service import LiveMapService

        svc = LiveMapService(repo, AsyncMock(), mock_redis_client, map_provider, traffic_provider)
        out = await svc.get_active_disasters(bounds=bounds)

        assert out == cached
        repo.list_active_disasters.assert_not_called()
        mock_redis_client.get.assert_awaited_once()

    async def test_cache_miss_queries_db_and_caches(self, mock_redis_client):
        bounds = _bounds()
        cache_key = f"live_map:disasters:{bounds}"
        mock_redis_client.get.return_value = None

        repo = AsyncMock()
        repo.list_active_disasters.return_value = [{"id": "d1"}]
        map_provider = AsyncMock()
        traffic_provider = AsyncMock()

        from app.services.live_map_service import LiveMapService

        svc = LiveMapService(repo, AsyncMock(), mock_redis_client, map_provider, traffic_provider)
        out = await svc.get_active_disasters(bounds=bounds)

        assert out == [{"id": "d1"}]
        repo.list_active_disasters.assert_awaited_once_with(bounds=bounds)

        # Verify cache.setex was called with the right key
        mock_redis_client.setex.assert_awaited()
        args, _ = mock_redis_client.setex.await_args
        assert args[0] == cache_key
        assert args[1] > 0  # positive TTL (service uses 1800s for disasters)
        assert isinstance(args[2], str)  # JSON string


class TestLiveMapServiceTrafficFallback:
    """Stale-while-revalidate pattern for traffic."""

    async def test_traffic_success_caches_live(self, mock_redis_client):
        bounds = _bounds()
        traffic_provider = AsyncMock()
        traffic_provider.get_traffic.return_value = {
            "source": "tomtom",
            "flow": [{"speed": 22}],
        }
        repo = AsyncMock()
        map_provider = AsyncMock()

        from app.services.live_map_service import LiveMapService

        svc = LiveMapService(repo, AsyncMock(), mock_redis_client, map_provider, traffic_provider)
        out = await svc.get_traffic(bounds=bounds)

        assert out["flow"][0]["speed"] == 22
        traffic_provider.get_traffic.assert_awaited_once_with(bounds=bounds)
        mock_redis_client.setex.assert_awaited()

    async def test_provider_down_falls_back_to_cached(self, mock_redis_client):
        bounds = _bounds()
        cache_key = f"live_map:traffic:{bounds}"

        cached = {"source": "cache", "flow": [{"speed": 18}]}
        mock_redis_client.get.return_value = json.dumps(cached)

        traffic_provider = AsyncMock()
        traffic_provider.get_traffic.side_effect = TimeoutError("TomTom timeout")
        repo = AsyncMock()
        map_provider = AsyncMock()

        from app.services.live_map_service import LiveMapService

        svc = LiveMapService(repo, AsyncMock(), mock_redis_client, map_provider, traffic_provider)
        out = await svc.get_traffic(bounds=bounds)

        assert out["flow"][0]["speed"] == 18
        mock_redis_client.get.assert_awaited_once_with(cache_key)

    async def test_provider_down_no_cache_returns_none(self, mock_redis_client):
        bounds = _bounds()
        mock_redis_client.get.return_value = None

        traffic_provider = AsyncMock()
        traffic_provider.get_traffic.side_effect = TimeoutError("TomTom timeout")
        repo = AsyncMock()
        map_provider = AsyncMock()

        from app.services.live_map_service import LiveMapService

        svc = LiveMapService(repo, AsyncMock(), mock_redis_client, map_provider, traffic_provider)
        out = await svc.get_traffic(bounds=bounds)

        assert out is None


class TestLiveMapServiceCombinedData:
    """get_live_map_data parallel aggregation."""

    async def test_combines_all_three_sources(self, mock_redis_client):
        map_provider = AsyncMock()
        map_provider.get_base_map_tiles.return_value = {
            "tiles_url": "https://tiles.example/{z}/{x}/{y}.pbf",
            "attribution": "Test",
            "style": "streets-v12",
            "zoom": 12,
        }

        mock_redis_client.get.return_value = None  # cache miss for disasters

        repo = AsyncMock()
        repo.list_active_disasters.return_value = [
            {"id": "d1", "type": "flood"}
        ]

        traffic_provider = AsyncMock()
        traffic_provider.get_traffic.return_value = {
            "source": "tomtom",
            "flow": [{"speed": 40}],
        }

        from app.services.live_map_service import LiveMapService

        svc = LiveMapService(repo, AsyncMock(), mock_redis_client, map_provider, traffic_provider)
        out = await svc.get_live_map_data(
            bounds=_bounds(), center=_center(), zoom=12
        )

        assert "base_map" in out
        assert "disasters" in out
        assert "traffic" in out
        assert "metadata" in out
        assert out["disasters"] == [{"id": "d1", "type": "flood"}]


# =========================================================================
# 3.  API Endpoint Tests
# =========================================================================


class TestInitializeEndpoint:
    """GET /live-map/initialize"""

    async def test_returns_defaults(self, async_client):
        resp = await async_client.get("/api/v1/live-map/initialize")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "center" in data
        assert "zoom" in data
        assert "bounds" in data
        assert data["center"]["lat"] == pytest.approx(53.3498, abs=0.01)

    async def test_override_center(self, async_client):
        resp = await async_client.get(
            "/api/v1/live-map/initialize?lat=40.71&lon=-74.01&zoom=10"
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["center"]["lat"] == pytest.approx(40.71, abs=0.01)
        assert data["zoom"] == 10

    async def test_invalid_lat_rejected(self, async_client):
        resp = await async_client.get("/api/v1/live-map/initialize?lat=999")
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_invalid_zoom_rejected(self, async_client):
        resp = await async_client.get("/api/v1/live-map/initialize?zoom=0")
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestTilesEndpoint:
    """GET /live-map/tiles"""

    async def test_returns_tiles_url(self, async_client, mock_map_provider):
        mock_map_provider.get_base_map_tiles.return_value = {
            "tiles_url": "https://api.mapbox.com/styles/v1/mapbox/streets-v12/tiles/256/{z}/{x}/{y}@2x?access_token=pk.test",
            "attribution": "© Mapbox © OpenStreetMap",
            "style": "streets-v12",
            "zoom": 12,
        }

        resp = await async_client.get("/api/v1/live-map/tiles?zoom=12")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "{z}" in data["tiles_url"]
        assert "attribution" in data

    async def test_invalid_style_returns_400(self, async_client, mock_map_provider):
        mock_map_provider.is_valid_style.return_value = False

        resp = await async_client.get(
            "/api/v1/live-map/tiles?zoom=12&style=bad-style"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    async def test_provider_unavailable_returns_503(self, async_client):
        """When map provider is None (not configured), return 503."""
        import app.api.v1.live_map as lm_module

        original = lm_module._map_provider
        lm_module._map_provider = None
        try:
            resp = await async_client.get("/api/v1/live-map/tiles?zoom=12")
            assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        finally:
            lm_module._map_provider = original


class TestStylesEndpoint:
    """GET /live-map/styles"""

    async def test_returns_style_list(self, async_client, mock_map_provider):
        mock_map_provider.get_available_styles.return_value = [
            {"name": "streets", "id": "streets-v12"},
            {"name": "dark", "id": "dark-v11"},
        ]
        mock_map_provider.style = "streets-v12"

        resp = await async_client.get("/api/v1/live-map/styles")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "styles" in data
        assert len(data["styles"]) == 2
        assert data["current_default"] == "streets-v12"


class TestDisastersEndpoint:
    """GET /live-map/disasters"""

    async def test_returns_disasters(self, async_client, mock_live_map_service):
        mock_live_map_service.get_active_disasters.return_value = [
            {"id": "d1", "type": "flood", "severity": "high"}
        ]

        resp = await async_client.get(
            f"/api/v1/live-map/disasters?bounds={_bounds()}"
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["count"] == 1
        assert data["disasters"][0]["type"] == "flood"

    async def test_missing_bounds_returns_422(self, async_client):
        resp = await async_client.get("/api/v1/live-map/disasters")
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_empty_result(self, async_client, mock_live_map_service):
        mock_live_map_service.get_active_disasters.return_value = []

        resp = await async_client.get(
            f"/api/v1/live-map/disasters?bounds={_bounds()}"
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["count"] == 0
        assert data["disasters"] == []


class TestTrafficEndpoint:
    """GET /live-map/traffic"""

    async def test_returns_live_traffic(self, async_client, mock_live_map_service):
        mock_live_map_service.get_traffic.return_value = {
            "source": "tomtom",
            "flow": [{"speed": 45, "congestion_level": "free"}],
        }

        resp = await async_client.get(
            f"/api/v1/live-map/traffic?bounds={_bounds()}"
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["available"] is True
        assert data["cache_status"] == "live"

    async def test_returns_unavailable_when_none(
        self, async_client, mock_live_map_service
    ):
        mock_live_map_service.get_traffic.return_value = None

        resp = await async_client.get(
            f"/api/v1/live-map/traffic?bounds={_bounds()}"
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["available"] is False
        assert "unavailable" in data["message"].lower()

    async def test_stale_cache_status(self, async_client, mock_live_map_service):
        mock_live_map_service.get_traffic.return_value = {
            "source": "cache",
            "flow": [{"speed": 18}],
        }

        resp = await async_client.get(
            f"/api/v1/live-map/traffic?bounds={_bounds()}"
        )
        data = resp.json()
        assert data["available"] is True
        assert data["cache_status"] == "stale"


class TestCombinedDataEndpoint:
    """GET /live-map/data"""

    async def test_returns_combined_data(
        self, async_client, mock_live_map_service
    ):
        mock_live_map_service.get_live_map_data.return_value = {
            "base_map": {
                "tiles_url": "https://tiles.example/{z}/{x}/{y}.pbf",
                "attribution": "Test",
                "style": "streets-v12",
                "zoom": 12,
            },
            "disasters": [{"id": "d1", "type": "flood"}],
            "traffic": {"source": "tomtom", "flow": [{"speed": 30}]},
            "metadata": {
                "timestamp": time.time(),
                "cache_status": {"disasters": "live", "traffic": "live"},
            },
        }

        resp = await async_client.get(
            f"/api/v1/live-map/data?bounds={_bounds()}&zoom=12"
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "base_map" in data
        assert "disasters" in data
        assert "traffic" in data
        assert "metadata" in data

    async def test_traffic_none_handled_gracefully(
        self, async_client, mock_live_map_service
    ):
        mock_live_map_service.get_live_map_data.return_value = {
            "base_map": {
                "tiles_url": "https://tiles.example/{z}/{x}/{y}.pbf",
                "attribution": "Test",
                "style": "streets-v12",
                "zoom": 12,
            },
            "disasters": [],
            "traffic": None,
            "metadata": {
                "timestamp": time.time(),
                "cache_status": {"disasters": "live", "traffic": "cached"},
            },
        }

        resp = await async_client.get(
            f"/api/v1/live-map/data?bounds={_bounds()}&zoom=12"
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["traffic"]["available"] is False

    async def test_missing_bounds_returns_422(self, async_client):
        resp = await async_client.get("/api/v1/live-map/data?zoom=12")
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestPendingDisastersEndpoint:
    """GET /live-map/pending-disasters"""

    async def test_returns_pending_list(
        self, async_client, mock_live_map_service
    ):
        mock_live_map_service.disaster_report_repo.get_pending_reports.return_value = [
            {
                "id": "p1",
                "tracking_id": "DIS-2026-000001",
                "type": "fire",
                "severity": "medium",
                "report_status": "pending",
            }
        ]

        resp = await async_client.get("/api/v1/live-map/pending-disasters")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["count"] == 1
        assert "actions" in data
        assert "verify" in data["actions"]

    async def test_empty_pending(self, async_client, mock_live_map_service):
        mock_live_map_service.disaster_report_repo.get_pending_reports.return_value = []

        resp = await async_client.get("/api/v1/live-map/pending-disasters")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["count"] == 0
