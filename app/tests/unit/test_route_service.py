"""
tests/unit/test_route_service.py

Unit tests for RouteService (UC6 route calculation).

IntegrationService is injected directly as a mock — no patching needed.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.route_service import RouteService, _haversine_km


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_db(row=None):
    """Build a mock db where execute().mappings().first() returns `row`."""
    db     = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    db.execute = AsyncMock(return_value=result)
    return db


def _sample_dep_row(
    station_lat=53.3474, station_lon=-6.2530,
    dis_lat=53.3498,     dis_lon=-6.2603,
    cur_lat=None,        cur_lon=None,
    status="DISPATCHED",
):
    return {
        "deployment_id":     "dep-1",
        "deployment_status": status,
        "current_latitude":  cur_lat,
        "current_longitude": cur_lon,
        "unit_code":         "F-01",
        "unit_name":         "Fire Engine 1",
        "station_lat":       station_lat,
        "station_lon":       station_lon,
        "dis_lat":           dis_lat,
        "dis_lon":           dis_lon,
        "location_address":  "O'Connell Street, Dublin 1",
    }


def _mock_integration(routes=None, exception=None):
    integration = AsyncMock()
    if exception:
        integration.get_directions = AsyncMock(side_effect=exception)
    else:
        integration.get_directions = AsyncMock(return_value={"routes": routes or []})
    return integration


def _tomtom_route(length_m=3500, travel_s=360):
    return {
        "length_meters":       length_m,
        "travel_time_seconds": travel_s,
        "points":              [[53.34, -6.26], [53.35, -6.25]],
        "geojson":             {"type": "LineString", "coordinates": []},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tests: get_deployment_route
# ─────────────────────────────────────────────────────────────────────────────

class TestGetDeploymentRoute:

    @pytest.mark.asyncio
    async def test_returns_tomtom_route_when_available(self):
        db          = _make_db(_sample_dep_row())
        integration = _mock_integration(routes=[_tomtom_route(3500, 360)])

        service = RouteService(db, integration)
        result  = await service.get_deployment_route("dep-1")

        assert result["source"]           == "tomtom"
        assert result["distance_km"]      == pytest.approx(3.5, rel=0.01)
        assert result["duration_minutes"] == pytest.approx(6.0, rel=0.01)
        assert len(result["polyline"])    > 0

    @pytest.mark.asyncio
    async def test_falls_back_to_estimate_on_tomtom_exception(self):
        db          = _make_db(_sample_dep_row())
        integration = _mock_integration(exception=Exception("TomTom down"))

        service = RouteService(db, integration)
        result  = await service.get_deployment_route("dep-1")

        assert result["source"]  == "estimate"
        assert result["distance_km"]      > 0
        assert result["duration_minutes"] > 0
        assert result["polyline"]         == []
        assert "note" in result

    @pytest.mark.asyncio
    async def test_falls_back_to_estimate_on_empty_routes(self):
        db          = _make_db(_sample_dep_row())
        integration = _mock_integration(routes=[])  # TomTom returns nothing

        service = RouteService(db, integration)
        result  = await service.get_deployment_route("dep-1")

        assert result["source"] == "estimate"

    @pytest.mark.asyncio
    async def test_uses_current_gps_as_origin_when_available(self):
        """Unit already moving — use GPS position, not station."""
        row         = _sample_dep_row(cur_lat=53.35, cur_lon=-6.25)
        db          = _make_db(row)
        integration = _mock_integration(routes=[_tomtom_route()])

        service = RouteService(db, integration)
        result  = await service.get_deployment_route("dep-1")

        assert result["origin"]["lat"]   == 53.35
        assert result["origin"]["label"] == "current GPS position"

        # Verify IntegrationService was called with GPS coords, not station
        call_kwargs = integration.get_directions.call_args
        assert call_kwargs.kwargs["origin"]["lat"] == pytest.approx(53.35, rel=1e-4)

    @pytest.mark.asyncio
    async def test_uses_station_as_origin_when_no_gps(self):
        db          = _make_db(_sample_dep_row())  # cur_lat=None
        integration = _mock_integration(routes=[_tomtom_route()])

        service = RouteService(db, integration)
        result  = await service.get_deployment_route("dep-1")

        assert result["origin"]["lat"]   == pytest.approx(53.3474, rel=1e-4)
        assert result["origin"]["label"] == "station"

    @pytest.mark.asyncio
    async def test_raises_404_for_missing_deployment(self):
        db          = _make_db(None)  # not found
        integration = _mock_integration()

        from fastapi import HTTPException
        service = RouteService(db, integration)
        with pytest.raises(HTTPException) as exc_info:
            await service.get_deployment_route("bad-id")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_422_when_no_station_no_gps(self):
        row = _sample_dep_row(station_lat=None, station_lon=None)
        db  = _make_db(row)

        from fastapi import HTTPException
        service = RouteService(db, _mock_integration())
        with pytest.raises(HTTPException) as exc_info:
            await service.get_deployment_route("dep-1")
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_result_includes_unit_and_destination_info(self):
        db          = _make_db(_sample_dep_row())
        integration = _mock_integration(routes=[_tomtom_route()])

        service = RouteService(db, integration)
        result  = await service.get_deployment_route("dep-1")

        assert result["deployment_id"] == "dep-1"
        assert result["unit_code"]     == "F-01"
        assert "destination" in result
        assert "origin" in result


# ─────────────────────────────────────────────────────────────────────────────
# Tests: calculate_route (general A→B)
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateRoute:

    @pytest.mark.asyncio
    async def test_returns_tomtom_route(self):
        db          = AsyncMock()
        integration = _mock_integration(routes=[_tomtom_route(5000, 600)])

        service = RouteService(db, integration)
        result  = await service.calculate_route(53.34, -6.26, 53.40, -6.20)

        assert result["source"]      == "tomtom"
        assert result["distance_km"] == pytest.approx(5.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_haversine_fallback_on_exception(self):
        db          = AsyncMock()
        integration = _mock_integration(exception=RuntimeError("timeout"))

        service = RouteService(db, integration)
        result  = await service.calculate_route(53.34, -6.26, 53.40, -6.20)

        assert result["source"]  == "estimate"
        assert result["polyline"] == []
        assert "note" in result

    @pytest.mark.asyncio
    async def test_haversine_road_factor_applied(self):
        """Road distance should be straight-line × 1.4."""
        straight = _haversine_km(53.34, -6.26, 53.40, -6.20)
        db       = AsyncMock()
        integration = _mock_integration(exception=Exception("down"))

        service = RouteService(db, integration)
        result  = await service.calculate_route(53.34, -6.26, 53.40, -6.20)

        assert result["distance_km"] == pytest.approx(straight * 1.4, rel=0.01)

    @pytest.mark.asyncio
    async def test_haversine_duration_at_40_kmh(self):
        """duration_minutes = road_km / 40 * 60."""
        db       = AsyncMock()
        integration = _mock_integration(exception=Exception("down"))

        service = RouteService(db, integration)
        result  = await service.calculate_route(53.34, -6.26, 53.40, -6.20)

        expected_duration = round(result["distance_km"] / 40 * 60, 1)
        assert result["duration_minutes"] == pytest.approx(expected_duration, rel=0.01)

    @pytest.mark.asyncio
    async def test_same_origin_and_destination_returns_zero_distance(self):
        db       = AsyncMock()
        integration = _mock_integration(exception=Exception("down"))

        service = RouteService(db, integration)
        result  = await service.calculate_route(53.34, -6.26, 53.34, -6.26)

        assert result["distance_km"]      == pytest.approx(0.0, abs=0.01)
        assert result["duration_minutes"] == pytest.approx(0.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_integration_called_with_correct_origin_dest(self):
        db          = AsyncMock()
        integration = _mock_integration(routes=[_tomtom_route()])

        service = RouteService(db, integration)
        await service.calculate_route(53.34, -6.26, 53.40, -6.20)

        integration.get_directions.assert_called_once()
        call_kwargs = integration.get_directions.call_args.kwargs
        assert call_kwargs["origin"]["lat"]      == 53.34
        assert call_kwargs["origin"]["lng"]      == -6.26   # Note: lng not lon
        assert call_kwargs["destination"]["lat"] == 53.40
        assert call_kwargs["destination"]["lng"] == -6.20


# ─────────────────────────────────────────────────────────────────────────────
# Tests: _haversine_km
# ─────────────────────────────────────────────────────────────────────────────

class TestHaversineKm:

    def test_zero_distance_same_point(self):
        assert _haversine_km(53.34, -6.26, 53.34, -6.26) == pytest.approx(0.0, abs=0.001)

    def test_dublin_to_cork_approx_220km(self):
        d = _haversine_km(53.3498, -6.2603, 51.8985, -8.4756)
        assert 210 < d < 235

    def test_symmetric(self):
        d1 = _haversine_km(53.34, -6.26, 53.40, -6.20)
        d2 = _haversine_km(53.40, -6.20, 53.34, -6.26)
        assert d1 == pytest.approx(d2, rel=1e-6)

    def test_positive_result(self):
        d = _haversine_km(53.34, -6.26, 53.40, -6.20)
        assert d > 0