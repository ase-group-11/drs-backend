"""

live map service - Orchestrates map data from provider with caching.

This service coordinates: 

- Base map tiles from map provider (MapBox)
- Active disaster data from the database
- Real-time traffic data from external APIs

Implements resilient caching patterns.

"""

# Example usage for reference:

"""
# Initialize the service
live_map_service = LiveMapService(
    disaster_repo=disaster_repository,
    cache=redis_client,
    map_provider=mapbox_provider,
    traffic_provider=tomtom_provider
)

# Get complete map data
map_data = await live_map_service.get_live_map_data(
    bounds="53.30,-6.35,53.40,-6.20",  # Dublin
    center={"lat": 53.3498, "lon": -6.2603},
    zoom=12
)

# Or get individual components
disasters = await live_map_service.get_active_disasters(bounds="...")
traffic = await live_map_service.get_traffic(bounds="...")
"""

import json, logging, asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class LiveMapService:
    """
    Service for aggregating live map data including disasters, traffic, and base map tiles.
    
    Uses multi-tier caching strategy:
    - Base map tiles: Long TTL (static data)
    - Disasters: Medium TTL (30-120s, changes moderately)
    - Traffic: Short TTL (15-30s, highly dynamic)
    """
    
    # Cache TTLs in seconds
    DISASTER_CACHE_TTL = 60  # 1 minute
    TRAFFIC_CACHE_TTL = 30   # 30 seconds
    
    def __init__(
        self,
        disaster_repo,
        cache,
        map_provider,
        traffic_provider
    ):
        """
        Initialize the LiveMapService.
        
        Args:
            disaster_repo: Repository for querying disaster data
            cache: Redis cache client (async)
            map_provider: Provider for base map tiles
            traffic_provider: Provider for real-time traffic data
        """
        self.disaster_repo = disaster_repo
        self.cache = cache
        self.map_provider = map_provider
        self.traffic_provider = traffic_provider
    
    async def get_base_map_tiles(
        self, 
        center: Dict[str, float], 
        zoom: int
    ) -> Dict[str, Any]:
        """
        Get base map tiles configuration from the map provider.
        
        Args:
            center: Dict with 'lat' and 'lon' keys
            zoom: Zoom level (typically 1-20)
            
        Returns:
            Dict containing:
                - tiles_url: Template URL for tiles (e.g., https://.../{z}/{x}/{y}.pbf)
                - attribution: Attribution text
                - style: Optional style configuration
                
        Raises:
            Exception: If map provider is unavailable
        """
        logger.info(f"Fetching base map tiles for center={center}, zoom={zoom}")
        
        try:
            tiles_config = await self.map_provider.get_base_map_tiles(
                center=center,
                zoom=zoom
            )
            return tiles_config
            
        except Exception as e:
            logger.error(f"Failed to fetch base map tiles: {e}")
            # Could implement fallback to default tiles here
            raise
    
    async def get_active_disasters(self, bounds: str) -> List[Dict[str, Any]]:
        """
        Get active disasters within the specified bounds, with caching.
        
        Implements cache-aside pattern:
        1. Check cache first
        2. On cache miss, query database
        3. Store result in cache for future requests
        
        Args:
            bounds: Bounding box string "south,west,north,east"
            
        Returns:
            List of disaster objects with id, type, severity, location, etc.
        """
        cache_key = f"live_map:disasters:{bounds}"
        
        # Try cache first
        try:
            cached_data = await self.cache.get(cache_key)
            if cached_data is not None:
                logger.info(f"Cache HIT for disasters: {cache_key}")
                return json.loads(cached_data)
        except Exception as e:
            logger.warning(f"Cache read failed for disasters : {e}")

        # Cache miss - fetch from database
        logger.info(f"Cache MISS for disasters: {cache_key}, querying database")
        disasters = await self.disaster_repo.list_active_disasters(bounds=bounds)
        
        try:
            # Cache the result
            await self.cache.setex(
                cache_key,
                self.DISASTER_CACHE_TTL,
                json.dumps(disasters)
            )
            logger.info(f"Successfully cached disasters data")
        except Exception as e:
            logger.warning(f"Failed to cache disasters: {e}")
            
        return disasters
    
    async def get_traffic(self, bounds: str, style: str = "relative") -> Optional[Dict[str, Any]]:
        """
        Get real-time traffic data with resilient fallback to cache.
        
        Implements caching pattern:
        1. Try to fetch live traffic data
        2. On success, cache it with short TTL
        3. On failure, fall back to cached data (stale-while-revalidate)
        4. If no cached data available, return None
        
        This ensures the map remains functional even when traffic provider is down.
        
        Args:
            bounds: Bounding box string "south,west,north,east"
            
        Returns:
            Traffic data dict with:
                - source: 'live', 'cache', or metadata about data source
                - flow: List of traffic flow segments
            Returns None if provider is down and no cached data exists.
        """
        cache_key = f"live_map:traffic:{bounds}:{style}"
        
        try:
            # Try to get live traffic data
            logger.info(f"Fetching live traffic for bounds: {bounds}")
            cached_data = await self.cache.get(cache_key)
            if cached_data is not None:
                logger.info(f"Cache HIT for traffic: {cache_key}")
                return json.loads(cached_data)
        except Exception as e:
            logger.warning(f"cache read failed: {e}")
        try:
            traffic_data = await self.traffic_provider.get_traffic(bounds=bounds)

            if traffic_data:
                traffic_data["style"] = style

                try:
            
                # Success - cache the live data
                    await self.cache.setex(
                        cache_key,
                        self.TRAFFIC_CACHE_TTL,
                        json.dumps(traffic_data)
                    )
                    logger.info(f"Successfully cached traffic data")
                except Exception as cache_error:
                    logger.warning(f"Failed to cache traffic data: {cache_error}")
            
            logger.info(f"Successfully fetched and cached live traffic data")
            return traffic_data
            
        except Exception as e:
            # Provider failed - try to use cached data
            logger.warning(
                f"Traffic provider failed ({type(e).__name__}: {e}), "
                f"attempting to use cached data"
            )
            try:
                cached_data = await self.cache.get(cache_key)
                if cached_data is not None:
                    logger.info(f"Using cached traffic data (stale-while-revalidate)")
                    return json.loads(cached_data)
            except Exception as cache_error:
                logger.warning(f"Cache fallback failed: {cache_error}")
            
            # No cached data available
            logger.error(f"No cached traffic data available, returning None")
            return None
    
    async def get_live_map_data(
        self,
        bounds: str,
        center: Dict[str, float],
        zoom: int
    ) -> Dict[str, Any]:
        """
        Fetch live data from provider.
        
        This is the main method that aggregates:
        - Base map tiles
        - Active disasters
        - Real-time traffic
        
        Args:
            bounds: Bounding box "south,west,north,east"
            center: Dict with 'lat' and 'lon'
            zoom: Zoom level
            
        Returns:
            Combined map data dict with:
                - base_map: Tiles configuration
                - disasters: List of active disasters
                - traffic: Traffic data (may be None)
                - metadata: Timestamps and cache status
        """
        logger.info(
            f"Fetching complete live map data for bounds={bounds}, "
            f"center={center}, zoom={zoom}"
        )
        
        # Fetch all data sources (could be done in parallel with asyncio.gather)
        base_map, disasters, traffic =  await asyncio.gather(
            self.get_base_map_tiles(center=center, zoom=zoom),
            self.get_active_disasters(bounds=bounds),
            self.get_traffic(bounds=bounds), 
            return_exceptions = False
        )
        
        # Determine cache status for metadata
        cache_status = {
            "disasters": "live",  # Would need to track this in get_active_disasters
            "traffic": "live" if traffic and traffic.get("source") != "cache" else "cached"
        }
        
        return {
            "base_map": base_map,
            "disasters": disasters,
            "traffic": traffic,
            "metadata": {
                "timestamp": datetime.utcnow().isoformat(),
                "bounds": bounds,
                "center": center,
                "zoom": zoom,
                "cache_status": cache_status
            }
        }


