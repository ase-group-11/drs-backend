# File: app/api/v1/disaster_report.py
"""
Disaster Report API endpoints.

IMPORTANT: Static routes (/submit, /pending/all, /cluster/review) MUST be
defined BEFORE dynamic routes (/{report_id}) otherwise FastAPI will match
"cluster" or "pending" as a report_id.
"""

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from pydantic import BaseModel, Field

from app.db.session import get_db
from app.services.blob_service import upload_multiple_files
from app.services.disaster_report_service import DisasterReportService
from app.schemas.disaster_report import (
    BlobUploadBatchResponse,
    DisasterReportCreateRequest,
    DisasterReportResponse,
    AdminReviewRequest,
    AdminReviewResponse,
)

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/disaster-reports", tags=["Disaster Reports"])


# ══════════════════════════════════════════════
# STATIC ROUTES FIRST (before /{report_id})
# ══════════════════════════════════════════════


# ──────────────────────────────────────────────
# ALL-IN-ONE: Upload photos + Create report
# ──────────────────────────────────────────────
@router.post(
    "/submit",
    response_model=DisasterReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit disaster report (all-in-one)",
    description=(
        "Combined endpoint: uploads photos to Azure Blob + creates disaster report + saves photos in DB. "
        "Frontend sends ONE request with files + form data."
    )
)
async def submit_disaster_report(
    user_id: str = Form(..., description="User ID"),
    location_address: str = Form(..., description="Address of the disaster"),
    disaster_type: str = Form(..., description="Type: FLOOD, FIRE, EARTHQUAKE, etc."),
    severity: str = Form(..., description="Severity: LOW, MEDIUM, HIGH, CRITICAL"),
    description: str = Form(..., description="Description of the disaster"),
    latitude: float = Form(..., description="Latitude"),
    longitude: float = Form(..., description="Longitude"),
    people_affected: int = Form(0, description="Estimated people affected"),
    multiple_casualties: bool = Form(False, description="Multiple casualties?"),
    structural_damage: bool = Form(False, description="Structural damage?"),
    road_blocked: bool = Form(False, description="Road blocked?"),
    files: List[UploadFile] = File(None, description="Photos/videos (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """All-in-one disaster report submission."""
    uploaded_files = []
    if files and files[0].filename:
        blob_result = await upload_multiple_files(files)
        uploaded_files = blob_result["uploaded_files"]

    service = DisasterReportService(db)
    report = await service.submit_report(
        user_id=user_id,
        location_address=location_address,
        disaster_type=disaster_type,
        severity=severity,
        description=description,
        latitude=latitude,
        longitude=longitude,
        people_affected=people_affected,
        multiple_casualties=multiple_casualties,
        structural_damage=structural_damage,
        road_blocked=road_blocked,
        uploaded_files=uploaded_files,
    )
    return DisasterReportResponse(**report)


# ──────────────────────────────────────────────
# Upload media to Azure Blob Storage
# ──────────────────────────────────────────────
@router.post(
    "/upload-media",
    response_model=BlobUploadBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload disaster media to blob storage",
    description="Upload photos/videos. Returns blob URLs + shared reference_id."
)
async def upload_media(files: List[UploadFile] = File(...)):
    """Upload photos/videos to Azure Blob Storage."""
    result = await upload_multiple_files(files)
    return BlobUploadBatchResponse(**result)


# ──────────────────────────────────────────────
# Create disaster report with photo URLs
# ──────────────────────────────────────────────
@router.post(
    "/",
    response_model=DisasterReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create disaster report",
    description="Submit a disaster report (status=PENDING). Photos saved with shared reference_id."
)
async def create_disaster_report(
    data: DisasterReportCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a disaster report with photo URLs from upload-media step."""
    service = DisasterReportService(db)
    report = await service.create_report(data)
    return DisasterReportResponse(**report)


# ──────────────────────────────────────────────
# GET: All pending reports (raw list)
# ──────────────────────────────────────────────
@router.get(
    "/pending/all",
    summary="Get all pending reports",
    description="Admin: get all reports awaiting review (oldest first)."
)
async def get_pending_reports(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Get all pending disaster reports for admin review."""
    service = DisasterReportService(db)
    reports = await service.get_pending_reports(limit=limit)
    return {
        "pending_reports": reports,
        "count": len(reports),
    }


# ──────────────────────────────────────────────
# GET: CLUSTERED pending reports (Smart admin view)
# ──────────────────────────────────────────────
@router.get(
    "/pending/clustered",
    summary="Get clustered pending reports (Smart admin view)",
    description=(
        "Groups nearby pending reports of the same disaster type into clusters. "
        "Uses PostGIS spatial clustering (default 500m radius, 1h time window)."
    )
)
async def get_clustered_pending_reports(
    radius_meters: int = 500,
    time_window_hours: int = 1,
    db: AsyncSession = Depends(get_db),
):
    """Smart admin dashboard view — groups duplicate/nearby reports."""
    service = DisasterReportService(db)
    clusters = await service.get_clustered_pending_reports(
        radius_meters=radius_meters,
        time_window_hours=time_window_hours,
    )
    return {
        "clusters": clusters,
        "cluster_count": len(clusters),
        "radius_meters": radius_meters,
        "time_window_hours": time_window_hours,
    }


# ──────────────────────────────────────────────
# Bulk review entire cluster
# ──────────────────────────────────────────────
class ClusterReviewRequest(BaseModel):
    """Request to approve/reject an entire cluster of reports."""
    report_ids: List[str] = Field(..., description="All report IDs in the cluster")
    reviewed_by_id: str = Field(..., description="Admin/team member ID")
    action: str = Field(..., description="'verified' or 'rejected'")
    rejection_reason: str = Field(None, description="Required if action is 'rejected'")


@router.post(
    "/cluster/review",
    summary="Bulk review entire cluster",
    description="Approve or reject ALL reports in a cluster at once."
)
async def review_cluster(
    data: ClusterReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Bulk approve/reject a cluster of reports."""
    if not data.report_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="report_ids cannot be empty."
        )

    review = AdminReviewRequest(
        reviewed_by_id=data.reviewed_by_id,
        action=data.action,
        rejection_reason=data.rejection_reason,
    )

    service = DisasterReportService(db)
    result = await service.review_cluster(data.report_ids, review)
    return result


# ──────────────────────────────────────────────
# GET: Reports by user
# ──────────────────────────────────────────────
@router.get(
    "/user/{user_id}",
    summary="Get reports by user",
    description="Get all disaster reports submitted by a specific user."
)
async def get_user_reports(
    user_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Get all reports submitted by a specific user."""
    service = DisasterReportService(db)
    reports = await service.get_user_reports(user_id, limit=limit)
    return {
        "reports": reports,
        "count": len(reports),
        "user_id": user_id,
    }


# ══════════════════════════════════════════════
# DYNAMIC ROUTES LAST (/{report_id})
# ══════════════════════════════════════════════


# ──────────────────────────────────────────────
# GET: Single report by ID
# ──────────────────────────────────────────────
@router.get(
    "/{report_id}",
    response_model=DisasterReportResponse,
    summary="Get disaster report by ID",
    description="Fetch a single disaster report with photo count."
)
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a single disaster report by ID."""
    service = DisasterReportService(db)
    report = await service.get_report(report_id)
    return DisasterReportResponse(**report)


# ──────────────────────────────────────────────
# Admin review single report
# ──────────────────────────────────────────────
@router.post(
    "/{report_id}/review",
    response_model=AdminReviewResponse,
    summary="Review single disaster report",
    description="Admin approves (→ creates disaster) or rejects a single report."
)
async def review_report(
    report_id: str,
    review: AdminReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Admin reviews a pending disaster report."""
    service = DisasterReportService(db)
    result = await service.review_report(report_id, review)
    return AdminReviewResponse(**result)