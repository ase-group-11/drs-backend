"""

TomTom traffic provider - Integration with TomTom traffic flow API
Provides real-time traffic flow data including speed, congestion levels, and incident information for rendering traffic overlays on maps.

API Documentation: https://developer.tomtom.com/traffic-api/documentation/tomtom-maps/product-information/introduction

"""

# Example usage
"""
# Initialize provider
traffic_provider = TrafficProvider(
    api_key="your-tomtom-api-key",
    style="relative",
    include_incidents=True
)

# Get traffic data
traffic = await traffic_provider.get_traffic(
    bounds="53.30,-6.35,53.40,-6.20"  # Dublin
)

# Response structure:
{
    "source": "tomtom",
    "flow": [
        {
            "segment_id": "seg_123",
            "current_speed": 22,
            "free_flow_speed": 50,
            "confidence": 0.85,
            "congestion_level": "moderate",
            "coordinates": [[53.35, -6.25], [53.36, -6.24]],
            "road_name": "O'Connell Street"
        }
    ],
    "incidents": [
        {
            "type": "accident",
            "delay": 300,
            "coordinates": [53.35, -6.25],
            "description": "Traffic accident blocking 2 lanes"
        }
    ],
    "timestamp": "2026-02-05T14:30:00Z"
}

# Don't forget to close the session when done

await traffic_provider.close()

"""

import logging
from typing import Dict, Any, List, Optional
import aiohttp, asyncio
from datetime import datetime


logger = logging.getLogger(__name__)

class TrafficProvider:
    """
    
    Integration with TomTom Traffic API for real-time traffic data.

    Provides: 
    - Traffic Flow: Current speed and congestion levels on road segments
    - Traffic incidents: Accidents, construction, road closures

    Uses TomTom's Traffic Flow API v4 for vector-based traffic data.
    
    """

    FLOW_BASE_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData"
    INCIDENTS_BASE_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"

    STYLE_ABSOLUTE = "absolute"
    STYLE_RELATIVE = "relative"
    STYLE_RELATIVE_DELAY = "relative-delay"

    # Zoom levels for traffic data
    MIN_ZOOM = 10 # Don't fetch traffic below this zoom
    MAX_ZOOM = 18

    def __init__(
        self,
        api_key: str,
        style: str = "relative", 
        timeout: int = 5, #shorter timeout for traffic (it's time-sensitive)
        include_incidents: bool = False
    ):
        """

        Initialize the TomTom Traffic provider.
        
        Args:
            api_key: TomTom API key
            style: Traffic visualization style (absolute, relative, relative-delay)
            timeout: Request timeout in seconds
            include_incidents: Whether to fetch traffic incidents (accidents, etc.)

        """

        self.api_key = api_key
        self.style = style
        self.timeout = timeout
        self.include_incidents = include_incidents

        self.session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        """

        Get or create aiohttp session for connection pooling.

        """
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session
    
    async def close(self):
        """
        
        Close the HTTP session.
        
        """

        if self.session and not self.session.closed:
            await self.session.close()

    async def get_traffic(self, bounds: str, style: Optional[str] = None) -> Dict[str, Any]:
        """

        Get real-time traffic flow data for the specified bounds.
        
        TomTom's API works by requesting traffic for specific road segments.
        For a bounding box, we sample key points and aggregate the data.
        
        Args:
            bounds: Bounding box string "south,west,north,east"
            
        Returns:
            Dict containing:
                - source: "tomtom"
                - flow: List of traffic flow segments
                - timestamp: When data was fetched
                - incidents: List of traffic incidents (if enabled)
                
        Raises:
            TimeoutError: If API request times out
            aiohttp.ClientError: If API request fails
            
        Example response:
            {
                "source": "tomtom",
                "flow": [
                    {
                        "segment_id": "seg_001",
                        "current_speed": 22,
                        "free_flow_speed": 50,
                        "confidence": 0.85,
                        "congestion_level": "moderate",
                        "coordinates": [[53.35, -6.25], [53.36, -6.24]],
                        "road_name": "O'Connell Street"
                    }
                ],
                "incidents": [...],  # if include_incidents=True
                "timestamp": "2026-02-05T14:30:00Z"
            }
        

        """

        logger.info(f"Fetching TomTom traffic data for bounds: {bounds}")

        active_style = style or self.style

        try: 
            # Parse bounds

            south, west, north, east = map(float, bounds.split(','))

            # sample points within the bounding box
            # For better coverage, sample a grid of points

            flow_data = await self._fetch_flow_data_for_bounds(
                south, west, north, east
            )

            # Fetch Incidents
            incidents = []

            if self.include_incidents:
                incidents = await self._fetch_incidents(south, west, north, east)

            response = {
                "source" : "tomtom", 
                "flow" : flow_data,
                "timestamp" : datetime.utcnow().isoformat(),
                "bounds" : bounds, 
                "style" : self.style
            }

            if self.include_incidents:
                response["incidents"] = incidents
            
            logger.info(f"Successfully fetched {len(flow_data)} traffic segments")

            return response
        except asyncio.TimeoutError:
            logger.error(f"TomTom API timeout after {self.timeout}s")
            raise TimeoutError(f"TomTom traffic request timed out")
        
        except aiohttp.ClientError as e:
            logger.error(f"TomTom API client error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching traffic: {e}")
            raise
                       
    async def _fetch_flow_data_for_bounds(
            
        self,
        south: float,
        west: float, 
        north: float, 
        east: float,
        # style: str = "relative"
            
    ) -> List[Dict[str, Any]]:
        """

        Fetch traffic flow data for a bounding box.
        
        Strategy: Sample a grid of points within the bounds and fetch traffic
        for roads near each point. This gives good coverage of the area.

        """

        # Calculate grid of sample points (5x5 grid)

        lat_step = (north - south) / 5
        lon_step = (east - west) / 5

        sample_points = []

        for i in range(5):
            for j in range(5):
                lat = south + (i + 0.5) * lat_step
                lon = west + (j + 0.5) * lon_step
                sample_points.append((lat, lon))
        
        # Fetch traffic for all sample points (in parallel)
        session = await self.get_session()
        tasks = [
            self.fetch_flow_at_point(session, lat, lon)
            for lat, lon in sample_points
        ]

        results = await asyncio.gather(*tasks, return_exceptions = True)

        # Aggregate and deduplicate results

        all_segments = []
        seen_segments = set()

        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Failed to fetch traffic at point: {result}")
                continue
        
            if result and isinstance(result, list):
                for segment in result:
                    # Use coordinates as unique identifier
                    segment_key = str(segment.get("coordinates"))
                    if segment_key not in seen_segments:
                        seen_segments.add(segment_key)
                        all_segments.append(segment)
        
        return all_segments
    
    async def fetch_flow_at_point(
        self,
        session: aiohttp.ClientSession, 
        lat: float, 
        lon: float,
        zoom: int = 10, 
        style: str = "relative"
    ) -> List[Dict[str, Any]]:
        
        """

        Fetch traffic flow data for roads near a specific point.
        
        Uses TomTom's Flow Segment Data API.

        """

        url = f"{self.FLOW_BASE_URL}/{style}/{zoom}/json"

        params = {
            "key": self.api_key,
            "point" : f"{lat},{lon}",
            "unit" : "KMPH",
            "thickness" : 10,
        }

        try:
            async with session.get(
                url, 
                params = params,
                timeout = aiohttp.ClientTimeout(total = self.timeout)
            ) as response:
                if response.status != 200:
                    logger.warning(
                        f"TomTom API returned status {response.status}"
                        f" for point {lat}, {lon}"
                    )

                    return []
                
                data = await response.json()
                return self._parse_flow_response(data)
            
        except Exception as e:
            logger.warning(f"Error fetching traffic at {lat}, {lon}: {e}")
            return []
        
    def _parse_flow_response(self, data: Dict) -> List[Dict[str, Any]]:
        """

        Parse TomTom flow response into standardized format.

        TomTom returns data in their proprietary format - we normalize it to match our API contract.

        """

        segments = []

        flow_segment = data.get("flowSegmentData")
        if not flow_segment:
            return segments
        
        current_speed = flow_segment.get("currentSpeed", 0)
        free_flow_speed = flow_segment.get("freeFlowSpeed", 0)
        confidence = flow_segment.get("confidence", 0.0)

        if free_flow_speed > 0:
            speed_ratio = current_speed / free_flow_speed
            if speed_ratio >= 0.8:
                congestion = "light"
            elif speed_ratio >= 0.5:
                congestion = "moderate"
            elif speed_ratio >= 0.3:
                congestion = "heavy"
            else:
                congestion = "severe"
        else:
            congestion = "unknown"
        
        # Extract coordinates (if present)

        coordinates = flow_segment.get("coordinates", {})
        coord_list = []

        if "coordinates" in coordinates:
            coord_list = [
                [c.get("latitude"), c.get("longitude")]
                for c in coordinates["coordinates"]
            ]

        segment = {
            "segment_id" : f"seg_{hash(str(coord_list)) % 1000000}",
            "current_speed" : current_speed,
            "free_flow_speed" : free_flow_speed,
            "confidence" : confidence,
            "congestion_level" : congestion,
            "coordinates" : coord_list,
            "road_name" : flow_segment.get("roadName", "Unknown Road"),
            "road_type" : flow_segment.get("roadType", ""),
        }

        segments.append(segment)

        return segments
    
    async def _fetch_incidents(
            self, 
            south: float, 
            west: float, 
            north: float, 
            east: float
    ) -> List[Dict[str, Any]]:
        """

        Fetch traffic incidents (accidents, construction, etc.) for bounds.
        
        Uses TomTom's Traffic Incident Details API.

        """

        session = await self.get_session()

        url = f"{self.INCIDENTS_BASE_URL}/json"

        params = {

            "key" : self.api_key,
            "bbox" : f"{west}, {south}, {east}, {north}",
            "fields" : "{incidents{type, geometry{type, coordinates}, properties{iconCategory, magnitudeOfDelay, events{description, code, iconCategory}}}}"
        }

        try:
            async with session.get(
                url,
                params = params, 
                timeout = aiohttp.ClientTimeout(total = self.timeout)
            ) as response:
                if response.status != 200:
                    logger.warning(f"Failed to fetch incidents: {response.status}")
                    return []
                
                data = await response.json()
                return self._parse_incidents_response(data)
        
        except Exception as e:
            logger.warning(f"Error fetching incidents: {e}")
            return []
        
    def _parse_incidents_response(self, data: Dict) -> List[Dict[str, Any]]:

        """

        Parse TomTom incidents into standardized format.

        """

        incidents = []

        for incident in data.get("incidents", []):
            properties = incident.get("properties", {})
            geometry = incident.get("geometry", {})


            parsed = {
                "type": properties.get("iconCategory", "unknown"),
                "delay": properties.get("magnitudeOfDelay", 0),
                "coordinates": geometry.get("coordinates", []),
                "description": "",
            }

            events = properties.get("events", [])
            if events:
                parsed["description"] = events[0].get("description", "")

            incidents.append(parsed)

        return incidents
    
    async def validate_api_key(self) -> bool:
        """
        Validate that the TomTom API key is working.

        Returns:
            True if API key is valid, False otherwise

        """

        try:
            session = await self.get_session()

            # Dublin Coordinates
            url = f"{self.FLOW_BASE_URL}/absolute/10/json"
            params = {
                "key" : self.api_key,
                "point" : "53.3498, -6.2603"
            }

            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                
                if response.status == 200:
                    logger.info("TomTom API Key validated successfully")

                    return True
                else:
                    logger.error(f"TomTom API key validation failed: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"TomTom API key validation error: {e}")
            return False

       
