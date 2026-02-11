"""

Live Map API endpoints.

FEATURES:
- Map initialization (default center, zoom, tiles config)
- Map tiles configuration (Mapbox)
- Map style listing and validation
- Real-time traffic data (TomTom with Redis caching)
- Active verified disasters (PostGIS + Redis caching)
- Combined live map data (parallel fetching with asyncio.gather)
- Emergency team pending verifications

"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import logging
import time

from app.db.session import get_db
from app.services.live_map_service import LiveMapService
from app.providers.map_provider import MapProvider
from app.providers.traffic import TrafficProvider
from app.repositories.disaster_repository import DisasterRepository
from cache.redis_client import get_redis_client
from app.core.config import settings
from app.schemas.live_map import (
    MapInitializeResponse,
    MapTilesResponse,
    DisastersResponse,
    TrafficResponse,
    LiveMapDataResponse,
    PendingDisastersResponse,
    DisasterResponse,
    DisasterLocationResponse,
    TrafficFlowResponse,
    TrafficDataResponse,
    LiveMapMetadataResponse,
    PendingDisasterResponse,
    LocationResponse,
    BoundsResponse,
)
from app.schemas.common import ResponseBase


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live-map", tags=["Live Map"])


# ---------------------------------------------------------------------------
# Global providers (set once at startup from main.py)
# ---------------------------------------------------------------------------

_map_provider: Optional[MapProvider] = None
_traffic_provider: Optional[TrafficProvider] = None


def set_live_map_providers(
    map_provider: MapProvider,
    traffic_provider: TrafficProvider,
):
    """
    Set global providers for live map service.

    Called from main.py on application startup.

    Args:
        map_provider: Mapbox provider instance
        traffic_provider: TomTom provider instance
    """
    global _map_provider, _traffic_provider
    _map_provider = map_provider
    _traffic_provider = traffic_provider


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------

async def get_live_map_service_dependency(
    db: AsyncSession = Depends(get_db),
) -> LiveMapService:
    """
    Dependency injection for LiveMapService.

    Creates service instance with all required dependencies:
    - Map provider (Mapbox)
    - Traffic provider (TomTom)
    - Disaster repository (PostGIS)
    - Redis client (caching)
    """
    disaster_repo = DisasterRepository(db_session=db)
    redis_client = await get_redis_client()

    service = LiveMapService(
        disaster_repo=disaster_repo,
        cache=redis_client,
        map_provider=_map_provider,
        traffic_provider=_traffic_provider,
    )
    return service


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/initialize",
    response_model=MapInitializeResponse,
    status_code=status.HTTP_200_OK,
    summary="Initialize map view",
    description=(
        "Returns default map configuration for first load: "
        "center coordinates, zoom level, bounds, and location name."
    ),
)
async def initialize_map(
    lat: Optional[float] = Query(
        None,
        ge=-90,
        le=90,
        description="Override center latitude",
    ),
    lon: Optional[float] = Query(
        None,
        ge=-180,
        le=180,
        description="Override center longitude",
    ),
    zoom: Optional[int] = Query(
        None,
        ge=1,
        le=20,
        description="Override zoom level",
    ),
):
    """
    Get map initialization configuration.

    Uses server defaults (Dublin) when no overrides are provided.
    The frontend calls this once on app launch to set up the map view.
    """
    center_lat = lat if lat is not None else settings.DEFAULT_LOCATION_LAT
    center_lon = lon if lon is not None else settings.DEFAULT_LOCATION_LON
    zoom_level = zoom if zoom is not None else settings.DEFAULT_ZOOM_LEVEL

    # Approximate visible bounds from zoom
    delta_lat = 180 / (2 ** zoom_level)
    delta_lon = 360 / (2 ** zoom_level)

    return MapInitializeResponse(
        center=LocationResponse(lat=center_lat, lon=center_lon),
        zoom=zoom_level,
        location_name="Dublin, Ireland",
        bounds=BoundsResponse(
            south=center_lat - delta_lat,
            west=center_lon - delta_lon,
            north=center_lat + delta_lat,
            east=center_lon + delta_lon,
        ),
    )


@router.get(
    "/tiles",
    response_model=MapTilesResponse,
    status_code=status.HTTP_200_OK,
    summary="Get map tiles configuration",
    description=(
        "Returns the Mapbox tile URL template the frontend uses to load "
        "map imagery as the user pans and zooms."
    ),
)
async def get_map_tiles(
    zoom: int = Query(
        12,
        ge=1,
        le=20,
        description="Zoom level (1-20)",
    ),
    style: Optional[str] = Query(
        None,
        description="Mapbox style override (e.g., 'dark-v11')",
    ),
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Get Mapbox tiles URL template.

    The response contains a tiles_url with {z}/{x}/{y} placeholders
    that the frontend map library (Mapbox GL JS / MapLibre) resolves
    at render time.
    """
    if _map_provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Map provider is not configured",
        )

    # Validate style if provided
    if style and not _map_provider.is_valid_style(style):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid map style: '{style}'. Use GET /live-map/styles for valid options.",
        )

    center = {
        "lat": settings.DEFAULT_LOCATION_LAT,
        "lon": settings.DEFAULT_LOCATION_LON,
    }

    try:
        tiles_config = await service.get_base_map_tiles(
            center=center, zoom=zoom
        )
        return MapTilesResponse(
            tiles_url=tiles_config["tiles_url"],
            attribution=tiles_config["attribution"],
            style=tiles_config.get("style", _map_provider.style),
            zoom=tiles_config.get("zoom", zoom),
        )
    except Exception as e:
        logger.error(f"Failed to get map tiles: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Map tiles service temporarily unavailable",
        )


@router.get(
    "/styles",
    status_code=status.HTTP_200_OK,
    summary="List available map styles",
    description="Returns all Mapbox styles the client can use.",
)
async def get_map_styles():
    """
    List available map styles.

    Useful for letting users switch between street, satellite,
    dark-mode, and other visual themes.
    """
    if _map_provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Map provider is not configured",
        )

    return {
        "styles": _map_provider.get_available_styles(),
        "current_default": _map_provider.style,
    }


@router.get(
    "/disasters",
    response_model=DisastersResponse,
    status_code=status.HTTP_200_OK,
    summary="Get active disasters",
    description=(
        "Returns verified active disasters within the bounding box. "
        "Uses Redis cache (60 s TTL) with cache-aside pattern."
    ),
)
async def get_disasters(
    bounds: str = Query(
        ...,
        description="Bounding box 'south,west,north,east'",
        example="53.30,-6.35,53.40,-6.20",
    ),
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Get active, verified disasters for the live map.

    Only disasters with report_status=VERIFIED and status=ACTIVE
    are returned to the public map. Citizen-reported disasters
    that are still PENDING do not appear here.
    """
    try:
        disasters = await service.get_active_disasters(bounds=bounds)
        return DisastersResponse(
            disasters=disasters,
            count=len(disasters),
            bounds=bounds,
        )
    except Exception as e:
        logger.error(f"Failed to get disasters: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve disaster data",
        )


@router.get(
    "/traffic",
    response_model=TrafficResponse,
    status_code=status.HTTP_200_OK,
    summary="Get real-time traffic",
    description=(
        "Returns traffic flow data from TomTom with Redis caching "
        "(30 s TTL). Falls back to stale cache on provider failure."
    ),
)
async def get_traffic(
    bounds: str = Query(
        ...,
        description="Bounding box 'south,west,north,east'",
        example="53.30,-6.35,53.40,-6.20",
    ),
    style: str = Query(
        "relative",
        description="Traffic style (absolute, relative, relative-delay)",
    ),
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Get real-time traffic data within specified bounds.

    Uses 5×5 grid sampling (25 points) with parallel requests
    to TomTom. Returns congestion levels (free/moderate/heavy/severe)
    per road segment for colour-coding the map overlay.
    """
    try:
        traffic_data = await service.get_traffic(bounds=bounds)

        if traffic_data is None:
            return TrafficResponse(
                available=False,
                message="Traffic data temporarily unavailable",
            )

        source = traffic_data.get("source", "unknown")
        cache_status = "stale" if source == "cache" else "live"

        return TrafficResponse(
            available=True,
            traffic=traffic_data,
            cache_status=cache_status,
        )

    except Exception as e:
        logger.error(f"Failed to get traffic data: {e}")
        return TrafficResponse(
            available=False,
            message="Traffic data temporarily unavailable",
            error=str(e),
        )


@router.get(
    "/data",
    response_model=LiveMapDataResponse,
    status_code=status.HTTP_200_OK,
    summary="Get combined live map data",
    description=(
        "Single endpoint that returns base map tiles, active disasters, "
        "and traffic in one response using asyncio.gather for parallel "
        "fetching."
    ),
)
async def get_live_map_data(
    bounds: str = Query(
        ...,
        description="Bounding box 'south,west,north,east'",
        example="53.30,-6.35,53.40,-6.20",
    ),
    zoom: int = Query(12, ge=1, le=20, description="Zoom level"),
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Get everything the frontend needs in a single call.

    Fires three concurrent tasks via asyncio.gather:
      - Mapbox tiles configuration
      - Active verified disasters from PostGIS
      - Real-time traffic from TomTom

    Typical response time: 200-500 ms (first), ~5 ms (cache hit).
    """
    start = time.time()

    center = {
        "lat": settings.DEFAULT_LOCATION_LAT,
        "lon": settings.DEFAULT_LOCATION_LON,
    }

    try:
        result = await service.get_live_map_data(
            bounds=bounds, center=center, zoom=zoom
        )

        elapsed_ms = int((time.time() - start) * 1000)

        # Build traffic sub-response
        traffic_raw = result.get("traffic")
        if traffic_raw is None:
            traffic_resp = TrafficResponse(
                available=False,
                message="Traffic data temporarily unavailable",
            )
        else:
            traffic_resp = TrafficResponse(
                available=True,
                traffic=traffic_raw,
                cache_status=result.get("metadata", {})
                .get("cache_status", {})
                .get("traffic", "unknown"),
            )

        metadata = LiveMapMetadataResponse(
            timestamp=time.time(),
            request_time_ms=elapsed_ms,
            cache_status=result.get("metadata", {}).get("cache_status", {}),
        )

        return LiveMapDataResponse(
            base_map=result["base_map"],
            disasters=result.get("disasters", []),
            traffic=traffic_resp,
            metadata=metadata,
        )

    except Exception as e:
        logger.error(f"Failed to get live map data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve live map data",
        )


@router.get(
    "/pending-disasters",
    response_model=PendingDisastersResponse,
    status_code=status.HTTP_200_OK,
    summary="Get pending disaster reports",
    description=(
        "Returns user-reported disasters awaiting verification. "
        "Intended for emergency team dashboards only."
    ),
)
async def get_pending_disasters(
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Get disasters that citizens have reported but haven't been
    verified by an emergency team yet.

    These do NOT appear on the public live map until verified.
    """
    try:
        pending = await service.disaster_repo.list_pending_disasters()

        return PendingDisastersResponse(
            pending_disasters=pending,
            count=len(pending),
            actions={
                "verify": "/api/v1/disasters/{id}/verify",
                "reject": "/api/v1/disasters/{id}/reject",
                "mark_duplicate": "/api/v1/disasters/{id}/duplicate",
            },
        )

    except Exception as e:
        logger.error(f"Failed to get pending disasters: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve pending disasters",
        )
