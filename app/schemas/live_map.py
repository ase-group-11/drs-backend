# File: app/schemas/live_map.py
"""
Pydantic schemas for Live Map API endpoints.

Request and response models for live map, disasters, and traffic data.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any


# ========== Request Models ==========

class DisastersRequest(BaseModel):
    """Request model for disasters query."""
    
    bounds: str = Field(
        ...,
        description="Bounding box as 'south,west,north,east'",
        example="53.30,-6.35,53.40,-6.20"
    )
    types: Optional[str] = Field(
        None,
        description="Comma-separated disaster types (flood,fire,earthquake,...)"
    )
    min_severity: Optional[str] = Field(
        None,
        description="Minimum severity level (low,medium,high,critical)"
    )
    
    @validator('bounds')
    def validate_bounds(cls, v):
        """Validate bounds format."""
        try:
            parts = v.split(',')
            if len(parts) != 4:
                raise ValueError("Bounds must have 4 comma-separated values")
            
            south, west, north, east = map(float, parts)
            
            if not (-90 <= south <= 90 and -90 <= north <= 90):
                raise ValueError("Latitude must be between -90 and 90")
            if not (-180 <= west <= 180 and -180 <= east <= 180):
                raise ValueError("Longitude must be between -180 and 180")
            if south >= north:
                raise ValueError("South latitude must be less than north latitude")
                
            return v
        except Exception as e:
            raise ValueError(f"Invalid bounds format: {e}")
    
    @validator('min_severity')
    def validate_severity(cls, v):
        """Validate severity level."""
        if v is not None:
            valid_severities = ["low", "medium", "high", "critical"]
            if v.lower() not in valid_severities:
                raise ValueError(f"Severity must be one of: {', '.join(valid_severities)}")
        return v


class TrafficRequest(BaseModel):
    """Request model for traffic data query."""
    
    bounds: str = Field(
        ...,
        description="Bounding box as 'south,west,north,east'",
        example="53.30,-6.35,53.40,-6.20"
    )
    style: Optional[str] = Field(
        "relative",
        description="Traffic style (absolute, relative, relative-delay)"
    )
    
    @validator('bounds')
    def validate_bounds(cls, v):
        """Validate bounds format."""
        try:
            parts = v.split(',')
            if len(parts) != 4:
                raise ValueError("Bounds must have 4 comma-separated values")
            
            south, west, north, east = map(float, parts)
            
            if not (-90 <= south <= 90 and -90 <= north <= 90):
                raise ValueError("Latitude must be between -90 and 90")
            if not (-180 <= west <= 180 and -180 <= east <= 180):
                raise ValueError("Longitude must be between -180 and 180")
                
            return v
        except Exception as e:
            raise ValueError(f"Invalid bounds format: {e}")
    
    @validator('style')
    def validate_style(cls, v):
        """Validate traffic style."""
        valid_styles = ["absolute", "relative", "relative-delay"]
        if v not in valid_styles:
            raise ValueError(f"Style must be one of: {', '.join(valid_styles)}")
        return v


# ========== Response Models ==========

class LocationResponse(BaseModel):
    """Location coordinates."""
    lat: float
    lon: float


class BoundsResponse(BaseModel):
    """Bounding box coordinates."""
    south: float
    west: float
    north: float
    east: float


class MapInitializeResponse(BaseModel):
    """Response for map initialization."""
    
    center: LocationResponse
    zoom: int
    location_name: str
    bounds: BoundsResponse


class MapTilesResponse(BaseModel):
    """Response for map tiles."""
    
    tiles_url: str = Field(..., description="Mapbox tile URL template")
    attribution: str = Field(..., description="Map attribution text")
    style: str = Field(..., description="Map style name")
    zoom: int = Field(..., description="Zoom level")


class DisasterLocationResponse(BaseModel):
    """Disaster location details."""
    lat: float
    lon: float
    geojson: Optional[Dict[str, Any]] = None


class DisasterResponse(BaseModel):
    """Single disaster data."""
    
    id: str
    tracking_id: str
    type: str
    severity: str
    status: str
    report_status: str
    location: DisasterLocationResponse
    affected_area: Optional[str] = None
    description: Optional[str] = None
    created_at: str
    updated_at: str
    is_user_reported: bool
    reporter_id: Optional[str] = None
    verified_by_id: Optional[str] = None
    photo_count: int = 0
    metadata: Optional[Dict[str, Any]] = None


class DisastersResponse(BaseModel):
    """Response for disasters query."""
    
    disasters: List[DisasterResponse]
    count: int
    bounds: str
    verification_note: str = "Only verified disasters are shown"
    cache_ttl: int = 60


class TrafficFlowResponse(BaseModel):
    """Single traffic flow data point."""
    
    current_speed: Optional[float]
    free_flow_speed: Optional[float]
    confidence: Optional[float] = None
    coordinates: List[List[float]] = []
    road_name: str = "Unknown"
    congestion_level: str


class TrafficDataResponse(BaseModel):
    """Traffic data details."""
    
    source: str = "tomtom"
    style: str
    flow: List[TrafficFlowResponse]
    sample_count: int
    timestamp: str


class TrafficResponse(BaseModel):
    """Response for traffic query."""
    
    available: bool
    traffic: Optional[TrafficDataResponse] = None
    cache_status: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


class LiveMapMetadataResponse(BaseModel):
    """Metadata for live map data."""
    
    timestamp: float
    request_time_ms: int
    cache_status: Dict[str, str]
    verification_note: str = "Only verified disasters shown on public map"


class LiveMapDataResponse(BaseModel):
    """Response for complete live map data."""
    
    base_map: MapTilesResponse
    disasters: List[DisasterResponse]
    traffic: TrafficResponse
    metadata: LiveMapMetadataResponse


class PendingDisasterResponse(BaseModel):
    """Single pending disaster data."""
    
    id: str
    tracking_id: str
    type: str
    severity: str
    status: str
    report_status: str
    location: DisasterLocationResponse
    affected_area: Optional[str] = None
    description: Optional[str] = None
    created_at: str
    is_user_reported: bool
    reporter_id: Optional[str] = None
    photo_count: int = 0


class PendingDisastersResponse(BaseModel):
    """Response for pending disasters query."""
    
    pending_disasters: List[PendingDisasterResponse]
    count: int
    note: str = "These disasters require verification before appearing on the live map"
    actions: Dict[str, str]