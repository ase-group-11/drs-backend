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
from app.repositories.disaster_report_repository import DisasterReportRepository
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
    Map3DConfigResponse, 
    MapCameraConfig, 
    MapTerrainConfig, 
    MapBuildingsConfig
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
    disaster_report_repo = DisasterReportRepository(db_session=db)
    redis_client = await get_redis_client()

    service = LiveMapService(
        disaster_repo=disaster_repo,
        disaster_report_repo = disaster_report_repo,
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
    "/map-config",
    response_model = Map3DConfigResponse,
    status_code = status.HTTP_200_OK,
    summary = "Get complete map configuration for 3D rendering",
    description = "Returns Mapbox styles and camera settings for Mapbox GL Js"
)
async def get_map_config(
    style: Optional[str] = Query("dark-v11", description = "Mapbox style (light, dark, streets, satellite, outdoors)")
):
    """
    
        Get complete map configurations for 3D rendering.

        Returns:
        - Style URLs
        - Default camera settings
        - Available styles
    
    """

    if _map_provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail = "Map provider is not configured",
        )
    
    available_styles = {
        "light": "mapbox://styles/mapbox/light-v11",
        "dark": "mapbox://styles/mapbox/dark-v11",
        "streets": "mapbox://styles/mapbox/streets-v12",
        "satellite": "mapbox://styles/mapbox/satellite-v9",
        "outdoors": "mapbox://styles/mapbox/outdoors-v12",
    }

    selected_style = available_styles.get(style, available_styles["dark"])

    return Map3DConfigResponse(

        styles = available_styles, 
        defaultStyle = selected_style, 
        camera = MapCameraConfig(
            center = LocationResponse(
                lat = settings.DEFAULT_LOCATION_LAT,
                lon = settings.DEFAULT_LOCATION_LON
            ),
            zoomLevel = 17, 
            pitch = 65, 
            bearing = 30
        ), 
        terrain = MapTerrainConfig(
            enabled = True, 
            exaggeration = 1.5
        ), 
        buildings = MapBuildingsConfig (
            enabled = True, 
            extrusion = True
        )

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
    response_model = TrafficResponse,
    status_code = status.HTTP_200_OK,
    summary = "Get real time traffic updates",
    description = "Get traffic flow from TomTom external API with Redis caching" 
)
async def get_traffic(
    bounds: str = Query(
        ...,
        description="Bounding box 'south,west,north,east'",
        example="53.30,-6.35,53.40,-6.20"
    ),
    service: LiveMapService = Depends(get_live_map_service_dependency)
):
    """
    Get real-time traffic data within specified bounds.
    
    Fetches traffic flow from TomTom API with intelligent caching.
    Uses 5x5 grid sampling (25 points) for efficient coverage.
    
    Steps:
    1. Validate bounds and style parameters
    2. Check Redis cache (30s TTL)
    3. If cache miss:
       a. Generate 5x5 grid sample points (25 total)
       b. Fetch traffic for each point in parallel (asyncio.gather)
       c. Calculate congestion levels based on speed ratios
    4. Cache results in Redis (with in-memory fallback)
    5. Return traffic data
    
    **Query Parameters:**
    - bounds: Bounding box "south,west,north,east" (required)
    - style: Visualization style (optional, default: "relative")
      * absolute: Actual speeds in km/h
      * relative: Relative to free-flow speed
      * relative-delay: Delay-based coloring
    
    **Response:**
    - available: Whether traffic data is available
    - traffic: Traffic data object (null if unavailable)
      * source: "tomtom"
      * style: Style used
      * flow: Array of traffic flow points
      * sample_count: Number of sample points
      * timestamp: Data timestamp
    - cache_status: Cache hit/miss/stale
    - message: Message if unavailable
    - error: Error if failed
    
    **Traffic Flow Point:**
    - current_speed: Current speed (km/h)
    - free_flow_speed: Free-flow speed (km/h)
    - confidence: Confidence level (0-1)
    - congestion_level: light | moderate | heavy | severe
    - coordinates: Road segment coordinates
    - road_name: Road name
    
    **Caching Strategy:**
    - Redis cache: 30s TTL
    - In-memory fallback: If Redis unavailable
    - Stale-while-revalidate: Return stale on error
    
    **Congestion Calculation:**
    - light: speed >= 80% of free-flow
    - moderate: 50% <= speed < 80% of free-flow
    - heavy: 30% <= speed < 50% of free-flow
    - severe: speed < 30% of free-flow
    
    **Errors:**
    - 400: Invalid bounds or style
    - 503: TomTom API unavailable
    - 500: Internal error
    
    **Note:** TomTom API key must be configured in environment.
    """
    try:
        # Validate bounds
        parts = bounds.split(',')
        if len(parts) != 4:
            raise ValueError("Bounds must be 'south,west,north,east'")
        
        try:
            south, west, north, east = map(float, parts)
            if not (-90 <= south <= 90 and -90 <= north <= 90):
                raise ValueError("Latitude must be -90 to 90")
            if not (-180 <= west <= 180 and -180 <= east <= 180):
                raise ValueError("Longitude must be -180 to 180")
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid bounds: {e}")
        
        # Validate style
        # valid_styles = ["absolute", "relative", "relative-delay"]
        # if style not in valid_styles:
        #     raise ValueError(f"Style must be: {', '.join(valid_styles)}")
        
        # Get traffic data
        traffic_data = await service.get_traffic(bounds=bounds)

        if traffic_data is None:
            return TrafficResponse(
                available=False,
                traffic=None,
                message="Traffic data temporarily unavailable"
            )
        
        # Convert to Pydantic model
        flow_list = traffic_data.get("flow", []) 
        # Convert flow items
        flow_models = []
        for item in flow_list:
            flow_models.append(
                TrafficFlowResponse(
                    current_speed=item.get("current_speed"),
                    free_flow_speed=item.get("free_flow_speed"),
                    confidence=item.get("confidence"),
                    congestion_level=item["congestion_level"],
                    coordinates=item["coordinates"],
                    road_name=item["road_name"]
                )
            )
            
        traffic_schema = TrafficDataResponse(
            source=traffic_data["source"],
            style=traffic_data["style"],
            flow=flow_models,
            sample_count=len(flow_models),
            timestamp=traffic_data["timestamp"]
        )
            
        return TrafficResponse(
            available=True,
            traffic=traffic_schema,
            cache_status=traffic_data.get("cache_status")
        )

        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.exception("Failed to fetch traffic data")
        
        if "api" in str(e).lower() or "tomtom" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Traffic service temporarily unavailable"
            )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch traffic data"
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
            flow_list = traffic_raw.get("flow", [])
            flow_models = []

            for item in flow_list:
                flow_models.append(
                    TrafficFlowResponse(
                        current_speed = item.get("current_speed"),
                        free_flow_speed = item.get("free_flow_speed"),
                        confidence = item.get("confidence"),
                        congestion_level = item["congestion_level"],
                        coordinates = item["coordinates"],
                        road_name = item["road_name"]
                    )
                )

            traffic_data_model = TrafficDataResponse(
                source = traffic_raw["source"],
                style = traffic_raw["style"],
                flow = flow_models,
                sample_count = len(flow_models),
                timestamp = traffic_raw["timestamp"]
            )

            traffic_resp = TrafficResponse(
                available=True,
                traffic=traffic_data_model,
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
    limit: int = Query(50, ge=1, le=100, description = "Maximum number of reports"),
    service: LiveMapService = Depends(get_live_map_service_dependency),
):
    """
    Get disasters that citizens have reported but haven't been
    verified by an emergency team yet.

    These do NOT appear on the public live map until verified.
    """
    try:
        pending = await service.disaster_report_repo.get_pending_reports(limit = limit)

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
