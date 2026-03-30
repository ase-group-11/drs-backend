# File: app/api/v1/live_map.py
"""
Live Map API — UC4

Provides the frontend map with:
  - Base map tile configuration (Mapbox)
  - Active verified disasters (PostGIS + Redis 60s cache)
  - Real-time traffic data (TomTom + Redis 30s cache)
  - Combined data endpoint (parallel fetch via asyncio.gather)
  - Pending disaster reports (ERT dashboard only)

Global providers (MapProvider + TrafficProvider) are initialised once at
startup via set_live_map_providers() called from main.py lifespan.
They are passed into LiveMapService via the dependency factory below.

Caching strategy (cache-aside pattern):
  - Disasters:     60 s TTL  (moderate change rate)
  - Traffic:       30 s TTL  (high change rate)
  - Map tiles:     long TTL  (static config)
"""

import logging
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.providers.map_provider import MapProvider
from app.providers.traffic import TrafficProvider
from app.repositories.disaster_report_repository import DisasterReportRepository
from app.repositories.disaster_repository import DisasterRepository
from app.schemas.live_map import (
    BoundsResponse,
    DisasterLocationResponse,
    DisasterResponse,
    DisastersResponse,
    LiveMapDataResponse,
    LiveMapMetadataResponse,
    Map3DConfigResponse,
    MapCameraConfig,
    MapBuildingsConfig,
    MapTerrainConfig,
    MapInitializeResponse,
    MapTilesResponse,
    PendingDisasterResponse,
    PendingDisastersResponse,
    TrafficDataResponse,
    TrafficFlowResponse,
    TrafficResponse,
)
from app.services.live_map_service import LiveMapService
from cache.redis_client import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live-map", tags=["Live Map — UC4"])


# ─────────────────────────────────────────────────────────────────────────────
# Global providers — set once at startup from main.py lifespan
# ─────────────────────────────────────────────────────────────────────────────

_map_provider: Optional[MapProvider] = None
_traffic_provider: Optional[TrafficProvider] = None


def set_live_map_providers(
    map_provider: MapProvider,
    traffic_provider: TrafficProvider,
) -> None:
    """
    Initialise global Mapbox + TomTom providers.
    Called from main.py lifespan at startup — never call directly.
    """
    global _map_provider, _traffic_provider
    _map_provider     = map_provider
    _traffic_provider = traffic_provider


# ─────────────────────────────────────────────────────────────────────────────
# Dependency factory
# ─────────────────────────────────────────────────────────────────────────────

async def get_live_map_service_dependency(
    db: AsyncSession = Depends(get_db),
) -> LiveMapService:
    """
    Build LiveMapService with all required dependencies.
    Returns a degraded-mode service if Redis is unavailable.
    """
    redis = await get_redis_client()
    return LiveMapService(
        disaster_repo=DisasterRepository(db),
        disaster_report_repo=DisasterReportRepository(db),
        cache=redis,
        map_provider=_map_provider,
        traffic_provider=_traffic_provider,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/initialize",
    response_model=MapInitializeResponse,
    status_code=status.HTTP_200_OK,
    summary="Map initialisation config (center, zoom, tile URLs)",
)
async def initialize_map(
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Returns everything the frontend needs to initialise the Mapbox map:
    default center, default zoom level, and Mapbox tile URL configuration.
    Called once on page load.
    No auth required — public endpoint.
    """
    try:
        center = {
            "lat": settings.DEFAULT_LOCATION_LAT,
            "lon": settings.DEFAULT_LOCATION_LON,
        }
        return await service.get_map_initialization(center=center, zoom=12)
    except Exception as exc:
        logger.exception("Map initialization failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Map initialization failed",
        )


@router.get(
    "/tiles",
    response_model=MapTilesResponse,
    status_code=status.HTTP_200_OK,
    summary="Mapbox tile configuration",
)
async def get_map_tiles(
    style: str = Query("streets", description="Map style: streets | satellite | dark"),
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Returns the Mapbox tile URL and style configuration for the requested style.
    Used by the frontend to switch between street, satellite, and dark modes.
    No auth required — public endpoint.
    """
    try:
        center = {
            "lat": settings.DEFAULT_LOCATION_LAT,
            "lon": settings.DEFAULT_LOCATION_LON,
        }
        return await service.get_map_tiles(center=center, zoom=12, style=style)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.exception("Tiles fetch failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch map tiles",
        )


@router.get(
    "/disasters",
    response_model=DisastersResponse,
    status_code=status.HTTP_200_OK,
    summary="Active verified disasters within a bounding box",
)
async def get_disasters(
    bounds: str = Query(
        ...,
        description="Bounding box as 'south,west,north,east'",
        example="53.30,-6.35,53.40,-6.20",
    ),
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Returns all ACTIVE, VERIFIED disasters within the specified map bounds.
    Cached in Redis for 60 seconds (cache-aside pattern).

    Only verified disasters appear here — citizen-reported disasters that
    are still PENDING are not shown to the public map.
    No auth required — public endpoint.
    """
    try:
        disasters = await service.get_active_disasters(bounds=bounds)
        return DisastersResponse(
            disasters=disasters,
            count=len(disasters),
            bounds=bounds,
        )
    except Exception as exc:
        logger.exception("Disasters fetch failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve disaster data",
        )


@router.get(
    "/traffic",
    response_model=TrafficResponse,
    status_code=status.HTTP_200_OK,
    summary="Real-time traffic data from TomTom",
)
async def get_traffic(
    bounds: str = Query(
        ...,
        description="Bounding box as 'south,west,north,east'",
        example="53.30,-6.35,53.40,-6.20",
    ),
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Returns live traffic flow data for the specified bounds from TomTom.
    Cached in Redis for 30 seconds.

    Uses a 5×5 grid (25 sample points) with parallel fetching for coverage.
    Falls back to stale cached data if TomTom is temporarily unavailable.
    No auth required — public endpoint.
    """
    try:
        traffic_data = await service.get_traffic(bounds=bounds)

        if not traffic_data:
            return TrafficResponse(
                available=False,
                message="Traffic data temporarily unavailable",
            )

        flow_models = [
            TrafficFlowResponse(
                current_speed=item.get("current_speed"),
                free_flow_speed=item.get("free_flow_speed"),
                confidence=item.get("confidence"),
                congestion_level=item["congestion_level"],
                coordinates=item["coordinates"],
                road_name=item["road_name"],
            )
            for item in traffic_data.get("flow", [])
        ]

        return TrafficResponse(
            available=True,
            traffic=TrafficDataResponse(
                source=traffic_data["source"],
                style=traffic_data["style"],
                flow=flow_models,
                sample_count=len(flow_models),
                timestamp=traffic_data["timestamp"],
            ),
            cache_status=traffic_data.get("cache_status", "unknown"),
        )

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.exception("Traffic fetch failed")
        if "api" in str(exc).lower() or "tomtom" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Traffic service temporarily unavailable",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch traffic data",
        )


@router.get(
    "/data",
    response_model=LiveMapDataResponse,
    status_code=status.HTTP_200_OK,
    summary="Combined live map data (tiles + disasters + traffic in one call)",
)
async def get_live_map_data(
    bounds: str = Query(
        ...,
        description="Bounding box as 'south,west,north,east'",
        example="53.30,-6.35,53.40,-6.20",
    ),
    zoom: int = Query(12, ge=1, le=20, description="Current map zoom level"),
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Single endpoint that returns everything the frontend map needs in one call.
    Fires three concurrent tasks via asyncio.gather:
      1. Mapbox tile configuration
      2. Active verified disasters (PostGIS + Redis cache)
      3. Real-time traffic from TomTom (Redis cache)

    Typical response time: 200–500 ms on first request, ~5 ms on cache hit.
    No auth required — public endpoint.
    """
    start = time.time()
    center = {
        "lat": settings.DEFAULT_LOCATION_LAT,
        "lon": settings.DEFAULT_LOCATION_LON,
    }

    try:
        result     = await service.get_live_map_data(bounds=bounds, center=center, zoom=zoom)
        elapsed_ms = int((time.time() - start) * 1000)

        traffic_raw = result.get("traffic")
        if not traffic_raw:
            traffic_resp = TrafficResponse(
                available=False,
                message="Traffic data temporarily unavailable",
            )
        else:
            flow_models = [
                TrafficFlowResponse(
                    current_speed=item.get("current_speed"),
                    free_flow_speed=item.get("free_flow_speed"),
                    confidence=item.get("confidence"),
                    congestion_level=item["congestion_level"],
                    coordinates=item["coordinates"],
                    road_name=item["road_name"],
                )
                for item in traffic_raw.get("flow", [])
            ]
            traffic_resp = TrafficResponse(
                available=True,
                traffic=TrafficDataResponse(
                    source=traffic_raw["source"],
                    style=traffic_raw["style"],
                    flow=flow_models,
                    sample_count=len(flow_models),
                    timestamp=traffic_raw["timestamp"],
                ),
                cache_status=result.get("metadata", {}).get("cache_status", {}).get("traffic", "unknown"),
            )

        return LiveMapDataResponse(
            base_map=result["base_map"],
            disasters=result.get("disasters", []),
            traffic=traffic_resp,
            metadata=LiveMapMetadataResponse(
                timestamp=time.time(),
                request_time_ms=elapsed_ms,
                cache_status=result.get("metadata", {}).get("cache_status", {}),
            ),
        )

    except Exception as exc:
        logger.exception("Live map data fetch failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve live map data",
        )


@router.get(
    "/pending-disasters",
    response_model=PendingDisastersResponse,
    status_code=status.HTTP_200_OK,
    summary="Pending disaster reports awaiting ERT verification (Admin)",
)
async def get_pending_disasters(
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Returns citizen-reported disasters that are still PENDING — not yet
    approved or rejected by the ERT. Used by the admin dashboard to
    surface reports that need immediate review.

    These reports do NOT appear on the public map (/disasters endpoint).
    Requires: emergency team access (enforced in service layer).
    """
    try:
        return await service.get_pending_disaster_reports()
    except Exception as exc:
        logger.exception("Pending disasters fetch failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve pending disasters",
        )