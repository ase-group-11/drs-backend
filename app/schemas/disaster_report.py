# File: app/schemas/disaster_report.py
"""
Disaster Report schemas (DTOs) for API requests and responses.

Defines Pydantic models for:
- Blob upload responses
- Disaster report creation
- Admin review (approve/reject)

NOTE: All enum values are stored as UPPERCASE in PostgreSQL.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime


# ========== Blob Upload ==========

class BlobUploadFileResponse(BaseModel):
    """Single file upload result from Azure Blob Storage."""
    image_url: str = Field(..., description="Azure Blob Storage URL")
    file_size: int = Field(..., description="File size in bytes")
    mime_type: str = Field(..., description="MIME type (e.g., image/jpeg)")
    original_filename: str = Field(..., description="Original filename")


class BlobUploadBatchResponse(BaseModel):
    """Response after uploading multiple files — all share one reference_id."""
    uploaded_files: List[BlobUploadFileResponse]
    reference_id: str = Field(..., description="Shared grouping ID for all files in this batch")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "uploaded_files": [
                        {
                            "image_url": "https://account.blob.core.windows.net/disaster-media/photo1.jpg",
                            "file_size": 245000,
                            "mime_type": "image/jpeg",
                            "original_filename": "photo1.jpg"
                        }
                    ],
                    "reference_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                }
            ]
        }
    }


# ========== Disaster Report Creation ==========

# These match the ACTUAL PostgreSQL enum values (UPPERCASE)
VALID_DISASTER_TYPES = [
    "ACCIDENT", "BUILDING_COLLAPSE", "CRIME", "EARTHQUAKE", "EXPLOSION",
    "FIRE", "FLOOD", "GAS_LEAK", "HAZMAT", "LANDSLIDE",
    "MEDICAL_EMERGENCY", "OTHER", "POWER_OUTAGE", "RIOT", "STORM",
    "TERRORIST_ATTACK", "WATER_CONTAMINATION"
]

VALID_SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class PhotoInput(BaseModel):
    """Photo/video data from blob upload (frontend sends this)."""
    image_url: str = Field(..., description="Blob URL from upload step")
    caption: Optional[str] = Field(None, description="User's caption for this photo")
    file_size: Optional[int] = Field(None, description="File size in bytes")
    mime_type: Optional[str] = Field(None, description="MIME type")


class DisasterReportCreateRequest(BaseModel):
    """
    Create a new disaster report.

    Frontend sends this AFTER uploading media to blob storage.
    Report starts with status='PENDING' until admin reviews it.
    
    NOTE: disaster_type and severity are case-insensitive — they get uppercased automatically.
    """
    user_id: str = Field(..., description="ID of the user submitting the report")
    location_address: str = Field(..., description="Address of the disaster")
    disaster_type: str = Field(..., description="Type: FLOOD, FIRE, EARTHQUAKE, ACCIDENT, etc.")
    severity: str = Field(..., description="Severity: LOW, MEDIUM, HIGH, CRITICAL")
    description: str = Field(..., description="Description of the disaster")
    latitude: float = Field(..., ge=-90, le=90, description="Latitude")
    longitude: float = Field(..., ge=-180, le=180, description="Longitude")
    people_affected: Optional[int] = Field(0, ge=0, description="Estimated people affected")
    multiple_casualties: Optional[bool] = Field(False, description="Multiple casualties?")
    structural_damage: Optional[bool] = Field(False, description="Structural damage?")
    road_blocked: Optional[bool] = Field(False, description="Road blocked?")

    # Photos from blob upload step
    photos: Optional[List[PhotoInput]] = Field([], description="Photos uploaded to blob")
    reference_id: Optional[str] = Field(None, description="Shared reference_id from blob upload")

    @field_validator("disaster_type")
    @classmethod
    def validate_disaster_type(cls, v: str) -> str:
        upper_v = v.upper()
        if upper_v not in VALID_DISASTER_TYPES:
            raise ValueError(f"disaster_type must be one of: {', '.join(VALID_DISASTER_TYPES)}")
        return upper_v  # Always return UPPERCASE

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        upper_v = v.upper()
        if upper_v not in VALID_SEVERITIES:
            raise ValueError(f"severity must be one of: {', '.join(VALID_SEVERITIES)}")
        return upper_v  # Always return UPPERCASE

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "user_id": "550e8400-e29b-41d4-a716-446655440000",
                    "location_address": "O'Connell Street, Dublin 1",
                    "disaster_type": "FLOOD",
                    "severity": "HIGH",
                    "description": "Major flooding near the Liffey",
                    "latitude": 53.3498,
                    "longitude": -6.2603,
                    "people_affected": 150,
                    "multiple_casualties": False,
                    "structural_damage": True,
                    "road_blocked": True,
                    "reference_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "photos": [
                        {
                            "image_url": "https://account.blob.core.windows.net/.../photo1.jpg",
                            "caption": "Flooding near bridge",
                            "file_size": 245000,
                            "mime_type": "image/jpeg"
                        }
                    ]
                }
            ]
        }
    }


class DisasterReportResponse(BaseModel):
    """Response after creating or fetching a disaster report."""
    id: str
    user_id: str
    disaster_type: str
    severity: str
    description: Optional[str]
    location: Dict[str, Any]
    location_address: Optional[str]
    people_affected: int
    multiple_casualties: bool
    structural_damage: bool
    road_blocked: bool
    report_status: str
    disaster_id: Optional[str] = None
    reviewed_by_id: Optional[str] = None
    reviewed_at: Optional[str] = None
    rejection_reason: Optional[str] = None
    created_at: Optional[str] = None
    photo_count: int = 0

    model_config = {"from_attributes": True}


# ========== Admin Review ==========

class AdminReviewRequest(BaseModel):
    """
    Admin approves or rejects a pending disaster report.

    If approved → creates entry in 'disasters' table.
    If rejected → marks report as rejected with reason.
    """
    reviewed_by_id: str = Field(..., description="Admin/team member ID who is reviewing")
    action: str = Field(..., description="'verified' or 'rejected'")
    rejection_reason: Optional[str] = Field(None, description="Required if action is 'rejected'")

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        valid = ["verified", "rejected"]
        if v.lower() not in valid:
            raise ValueError(f"action must be one of: {', '.join(valid)}")
        return v.lower()

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "reviewed_by_id": "550e8400-e29b-41d4-a716-446655440000",
                    "action": "verified"
                },
                {
                    "reviewed_by_id": "550e8400-e29b-41d4-a716-446655440000",
                    "action": "rejected",
                    "rejection_reason": "Duplicate report"
                }
            ]
        }
    }


class AdminReviewResponse(BaseModel):
    """Response after admin reviews a report."""
    report_id: str
    report_status: str
    disaster_id: Optional[str] = None
    tracking_id: Optional[str] = None
    reviewed_by_id: str
    reviewed_at: str
    assigned_to: Optional[str] = None
    assigned_department: Optional[str] = None
    message: str