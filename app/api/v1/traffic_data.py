"""
API endpoint definition for fetching traffic data from external provider.

Core business logic for the same can be found at /providers/traffic.py

"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.db.session import get_db
from cache.redis_client import(
    get_redis_client
)

from typing import Optional
from app.providers import traffic
from app.services.live_map_service import LiveMapService
from app.providers.traffic import TrafficProvider
from app.repositories import DisasterRepository
from app.schemas.live_map import(
    TrafficResponse,
    TrafficDataResponse,
    TrafficFlowResponse
)
from app.api.v1.live_map import get_live_map_service_dependency
from app.schemas.common import ResponseBase
import logging

logging.basicConfig(
    level = logging.INFO,
    format='%(asctime)s - %(name)s -%(levelname)s -%(message)s'
)

logger = logging.getLogger(__name__)

traffic_provider: Optional[TrafficProvider] = None

router = APIRouter(prefix="/live-map", tags=["Live Map"])


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
    style: str = Query(
        "relative",
        description="Traffic style (absolute, relative, relative-delay)"
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
        valid_styles = ["absolute", "relative", "relative-delay"]
        if style not in valid_styles:
            raise ValueError(f"Style must be: {', '.join(valid_styles)}")
        
        # Get traffic data
        traffic_data = await service.get_traffic(bounds=bounds, style=style)
        
        # Convert to Pydantic model
        if traffic_data.get("available") and traffic_data.get("traffic"):
            traffic_obj = traffic_data["traffic"]
            
            # Convert flow items
            flow_models = []
            for item in traffic_obj.get("flow", []):
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
                source=traffic_obj["source"],
                style=traffic_obj["style"],
                flow=flow_models,
                sample_count=traffic_obj["sample_count"],
                timestamp=traffic_obj["timestamp"]
            )
            
            return TrafficResponse(
                available=True,
                traffic=traffic_schema,
                cache_status=traffic_data.get("cache_status")
            )
        else:
            return TrafficResponse(
                available=False,
                traffic=None,
                message=traffic_data.get("message"),
                error=traffic_data.get("error")
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
