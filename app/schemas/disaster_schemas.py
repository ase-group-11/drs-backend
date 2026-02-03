from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List
from datetime import datetime

from app.models.disaster import DisasterType, DisasterSeverity, DisasterStatus

# Dublin bounding box constants
DUBLIN_LAT_MIN = 53.2
DUBLIN_LAT_MAX = 53.5
DUBLIN_LNG_MIN = -6.5
DUBLIN_LNG_MAX = -6.0

# Input: For POST /disasters/report
class DisasterCreate(BaseModel):
    location_lat: float = Field(
        ...,
        description="Latitude of disaster location",
        ge=-90,
        le=90
    )
    location_lng: float = Field(
        ...,
        description="Longitude of disaster location",
        ge=-180,
        le=180
    )
    disaster_type: DisasterType = Field(..., description="Type of disaster")
    severity: DisasterSeverity = Field(..., description="Severity level")
    description: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Detailed description of the disaster"
    )
    image_urls: Optional[List[str]] = Field(
        default=[],
        max_items=5,
        description="List of image URLs (max 5)"
    )

    @field_validator("location_lat")
    @classmethod
    def validate_dublin_latitude(cls, v: float) -> float:
        if not (DUBLIN_LAT_MIN <= v <= DUBLIN_LAT_MAX):
            raise ValueError(
                f"Latitude must be within Dublin bounds "
                f"({DUBLIN_LAT_MIN} to {DUBLIN_LAT_MAX})"
            )
        return v

    @field_validator("location_lng")
    @classmethod
    def validate_dublin_longitude(cls, v: float) -> float:
        if not (DUBLIN_LNG_MIN <= v <= DUBLIN_LNG_MAX):
            raise ValueError(
                f"Longitude must be within Dublin bounds "
                f"({DUBLIN_LNG_MIN} to {DUBLIN_LNG_MAX})"
            )
        return v

    @field_validator("image_urls")
    @classmethod
    def validate_image_urls(cls, v: Optional[List[str]]) -> List[str]:
        if v is None:
            return []

        # Validate each URL is properly formatted
        for url in v:
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"Invalid URL format: {url}")

        return v

# Output: Returned after successful creation
class DisasterResponse(BaseModel):
    disaster_id: int
    location_lat: float
    location_lng: float
    disaster_type: DisasterType
    severity: DisasterSeverity
    description: str
    reporter_id: int
    status: DisasterStatus
    image_urls: List[str]
    created_at: datetime

    # ERT notification flag (populated by service layer)
    ert_notified: Optional[bool] = Field(default=None)

    model_config = ConfigDict(from_attributes=True)
