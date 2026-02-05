
import json
import pytest
from unittest.mock import AsyncMock

pytestmark = pytest.mark.asyncio


def _bounds():
    # south,west,north,east (simple bbox format)
    return "53.30,-6.35,53.40,-6.20"


def _center():
    return {"lat": 53.3498, "lon": -6.2603}


async def test_base_map_tiles_calls_map_provider(mock_redis_client):
    """
    Expectation:
    - LiveMapService.get_base_map_tiles(center, zoom) calls MapProvider.get_base_map_tiles
    - Returns the provider payload (tiles template / style / other params)
    """
    map_provider = AsyncMock()
    map_provider.get_base_map_tiles.return_value = {
        "tiles_url": "https://tiles.example/{z}/{x}/{y}.pbf",
        "attribution": "Example"
    }

    traffic_provider = AsyncMock()
    repo = AsyncMock()

    from app.services.live_map import LiveMapService 

    svc = LiveMapService(
        disaster_repo=repo,
        cache=mock_redis_client,
        map_provider=map_provider,
        traffic_provider=traffic_provider,
    )

    out = await svc.get_base_map_tiles(center=_center(), zoom=12)

    assert out["tiles_url"].startswith("https://")
    map_provider.get_base_map_tiles.assert_awaited_once()


async def test_get_disasters_cache_hit_returns_cached(mock_redis_client):
    """
    Cache hit:
    - cache.get(key) returns JSON string
    - service returns parsed list
    - DB/repo not called
    """
    bounds = _bounds()
    cache_key = f"live_map:disasters:{bounds}"

    cached = [{"id": "d1", "type": "flood"}, {"id": "d2", "type": "fire"}]
    mock_redis_client.get.return_value = json.dumps(cached)

    repo = AsyncMock()
    map_provider = AsyncMock()
    traffic_provider = AsyncMock()

    from app.services.live_map import LiveMapService

    svc = LiveMapService(repo, mock_redis_client, map_provider, traffic_provider)

    out = await svc.get_active_disasters(bounds=bounds)

    assert out == cached
    repo.list_active_disasters.assert_not_called()
    mock_redis_client.get.assert_awaited_once_with(cache_key)


async def test_get_disasters_cache_miss_fetches_repo_and_sets_cache(mock_redis_client):
    """
    Cache miss:
    - cache.get(key) -> None
    - repo.list_active_disasters(bounds) called
    - cache.setex(key, ttl, json_payload) called
    """
    bounds = _bounds()
    cache_key = f"live_map:disasters:{bounds}"

    mock_redis_client.get.return_value = None

    repo = AsyncMock()
    repo.list_active_disasters.return_value = [{"id": "d1"}]

    map_provider = AsyncMock()
    traffic_provider = AsyncMock()

    from app.services.live_map import LiveMapService

    svc = LiveMapService(repo, mock_redis_client, map_provider, traffic_provider)

    out = await svc.get_active_disasters(bounds=bounds)

    assert out == [{"id": "d1"}]
    repo.list_active_disasters.assert_awaited_once_with(bounds=bounds)

    # TTL
    mock_redis_client.setex.assert_awaited()
    args, _ = mock_redis_client.setex.await_args
    assert args[0] == cache_key
    assert args[1] in (30, 60, 120)  


async def test_get_traffic_success_caches_live(mock_redis_client):
    """
    Live traffic OK:
    - traffic_provider.get_traffic(bounds) returns payload
    - cache.setex called (short TTL like 30s)
    - response indicates live source
    """
    bounds = _bounds()
    cache_key = f"live_map:traffic:{bounds}"

    repo = AsyncMock()
    map_provider = AsyncMock()

    traffic_provider = AsyncMock()
    traffic_provider.get_traffic.return_value = {"source": "tomtom", "flow": [{"speed": 22}]}

    from app.services.live_map import LiveMapService

    svc = LiveMapService(repo, mock_redis_client, map_provider, traffic_provider)

    out = await svc.get_traffic(bounds=bounds)

    assert out["flow"][0]["speed"] == 22
    traffic_provider.get_traffic.assert_awaited_once_with(bounds=bounds)

    mock_redis_client.setex.assert_awaited()
    args, _ = mock_redis_client.setex.await_args
    assert args[0] == cache_key
    assert args[1] in (15, 30, 60)  # choose your TTL later and tighten
    assert isinstance(args[2], str)  # cached JSON


async def test_get_traffic_provider_down_falls_back_to_cached(mock_redis_client):
    """
    Provider fails:
    - traffic_provider raises
    - service tries cache.get(traffic_key)
    - returns cached payload (and marks it as cached)
    """
    bounds = _bounds()
    cache_key = f"live_map:traffic:{bounds}"

    cached = {"source": "cache", "flow": [{"speed": 18}]}
    mock_redis_client.get.return_value = json.dumps(cached)

    repo = AsyncMock()
    map_provider = AsyncMock()

    traffic_provider = AsyncMock()
    traffic_provider.get_traffic.side_effect = TimeoutError("TomTom timeout")

    from app.services.live_map import LiveMapService

    svc = LiveMapService(repo, mock_redis_client, map_provider, traffic_provider)

    out = await svc.get_traffic(bounds=bounds)

    assert out["flow"][0]["speed"] == 18
    mock_redis_client.get.assert_awaited_once_with(cache_key)


async def test_get_traffic_provider_down_no_cache_returns_none(mock_redis_client):
    """
    Provider fails + cache empty:
    - return None (so frontend can show "traffic unavailable")
    """
    bounds = _bounds()
    cache_key = f"live_map:traffic:{bounds}"

    mock_redis_client.get.return_value = None

    repo = AsyncMock()
    map_provider = AsyncMock()

    traffic_provider = AsyncMock()
    traffic_provider.get_traffic.side_effect = TimeoutError("TomTom timeout")

    from app.services.live_map import LiveMapService

    svc = LiveMapService(repo, mock_redis_client, map_provider, traffic_provider)

    out = await svc.get_traffic(bounds=bounds)

    assert out is None
    mock_redis_client.get.assert_awaited_once_with(cache_key)
