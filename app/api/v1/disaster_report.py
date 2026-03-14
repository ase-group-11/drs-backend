# File: app/api/v1/disaster_report.py
"""
Disaster Report API endpoints — with Bearer token auth.

Auth rules:
  - Citizen endpoints → get_current_user (any logged-in user)
  - Admin endpoints → get_current_team_member (emergency team only)
"""

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any
from pydantic import BaseModel, Field

from app.db.session import get_db
from app.auth.dependencies import get_current_user, get_current_team_member
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
# STATIC ROUTES FIRST
# ══════════════════════════════════════════════

@router.post(
    "/submit",
    response_model=DisasterReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit disaster report (all-in-one)",
)
async def submit_disaster_report(
    user_id: str = Form(...),
    location_address: str = Form(...),
    disaster_type: str = Form(...),
    severity: str = Form(...),
    description: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    people_affected: int = Form(0),
    multiple_casualties: bool = Form(False),
    structural_damage: bool = Form(False),
    road_blocked: bool = Form(False),
    files: List[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Citizen submits disaster report. Requires Bearer token."""
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


@router.post(
    "/upload-media",
    response_model=BlobUploadBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload disaster media",
)
async def upload_media(
    files: List[UploadFile] = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Upload photos. Requires Bearer token."""
    result = await upload_multiple_files(files)
    return BlobUploadBatchResponse(**result)


@router.post(
    "/",
    response_model=DisasterReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create disaster report",
)
async def create_disaster_report(
    data: DisasterReportCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Create report with photo URLs. Requires Bearer token."""
    service = DisasterReportService(db)
    report = await service.create_report(data)
    return DisasterReportResponse(**report)


@router.get(
    "/pending/all",
    summary="Get all pending reports (Admin)",
)
async def get_pending_reports(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """Admin: get pending reports. Requires emergency team Bearer token."""
    service = DisasterReportService(db)
    reports = await service.get_pending_reports(limit=limit)
    return {"pending_reports": reports, "count": len(reports)}


@router.get(
    "/pending/clustered",
    summary="Get clustered pending reports (Admin)",
)
async def get_clustered_pending_reports(
    radius_meters: int = 500,
    time_window_hours: int = 1,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """Admin: smart grouped view. Requires emergency team Bearer token."""
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


class ClusterReviewRequest(BaseModel):
    report_ids: List[str] = Field(...)
    reviewed_by_id: str = Field(...)
    action: str = Field(...)
    rejection_reason: str = Field(None)


@router.post(
    "/cluster/review",
    summary="Bulk review cluster (Admin)",
)
async def review_cluster(
    data: ClusterReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """Admin: bulk approve/reject cluster. Requires emergency team Bearer token."""
    if not data.report_ids:
        raise HTTPException(status_code=400, detail="report_ids cannot be empty.")

    review = AdminReviewRequest(
        reviewed_by_id=data.reviewed_by_id,
        action=data.action,
        rejection_reason=data.rejection_reason,
    )
    service = DisasterReportService(db)
    return await service.review_cluster(data.report_ids, review)


@router.get(
    "/user/{user_id}",
    summary="Get reports by user",
)
async def get_user_reports(
    user_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Get user's reports. Requires Bearer token."""
    service = DisasterReportService(db)
    reports = await service.get_user_reports(user_id, limit=limit)
    return {"reports": reports, "count": len(reports), "user_id": user_id}


# ══════════════════════════════════════════════
# DYNAMIC ROUTES LAST
# ══════════════════════════════════════════════

@router.get(
    "/{report_id}",
    response_model=DisasterReportResponse,
    summary="Get report by ID",
)
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Get single report. Requires Bearer token."""
    service = DisasterReportService(db)
    report = await service.get_report(report_id)
    return DisasterReportResponse(**report)


@router.post(
    "/{report_id}/review",
    response_model=AdminReviewResponse,
    summary="Review single report (Admin)",
)
async def review_report(
    report_id: str,
    review: AdminReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """Admin: approve/reject report. Requires emergency team Bearer token."""
    service = DisasterReportService(db)
    result = await service.review_report(report_id, review)
    return AdminReviewResponse(**result)