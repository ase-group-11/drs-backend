"""

Live Map API endpoints.

FEATURES:
- Map tiles configuration (Mapbox)
- Real-time traffic data (TomTom with Redis caching)
- Active verified disasters (PostGIS + Redis caching)
- Combined live map data (parallel fetching with asyncio.gather)
- Emergency team pending verifications

"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import logging

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


map_provider: Optional[MapProvider] = None

def set_live_map_providers(map_provider: MapProvider, traffic_provider: TrafficProvider):
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


async def get_live_map_service_dependency(
    db: AsyncSession = Depends(get_db)
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
        map_provider=_map_provider,
        traffic_provider=_traffic_provider,
        disaster_repository=disaster_repo,
        redis_client=redis_client,
        disaster_cache_ttl=settings.DISASTER_CACHE_TTL
    )
    
    return service