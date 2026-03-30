# File: app/api/v1/disaster_report.py
"""
Disaster Report API — UC2: Citizen Disaster Reporting

Auth rules:
  - Citizen endpoints → get_current_user  (any logged-in user)
  - Admin endpoints   → get_current_team_member (ERT only)

Two submission flows:
  1. All-in-one  POST /submit          — multipart form + file upload in one request
  2. Two-step    POST /upload-media    — upload photos first, get URLs
                 POST /              — send report JSON with photo URLs

RabbitMQ: disaster.reported published AFTER DB commit via BackgroundTasks.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, status, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/disaster-reports", tags=["Disaster Reports — UC2"])


# ─────────────────────────────────────────────────────────────────────────────
# RabbitMQ helper
# ─────────────────────────────────────────────────────────────────────────────

def _publish_report_event(payload: dict) -> None:
    """
    Publish disaster.reported to RabbitMQ.
    Called as a BackgroundTask AFTER get_db() commits.
    """
    try:
        from app.services.rabbitmq_service import publish_disaster_reported
        publish_disaster_reported(payload)
    except Exception as exc:
        logger.error(f"disaster_report.py: RabbitMQ publish failed (non-fatal): {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Static routes first
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/submit",
    response_model=DisasterReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit disaster report with photos (all-in-one, multipart)",
)
async def submit_disaster_report(
    background_tasks: BackgroundTasks,
    user_id:              str         = Form(...),
    location_address:     str         = Form(...),
    disaster_type:        str         = Form(...),
    severity:             str         = Form(...),
    description:          str         = Form(...),
    latitude:             float       = Form(...),
    longitude:            float       = Form(...),
    people_affected:      int         = Form(0),
    multiple_casualties:  bool        = Form(False),
    structural_damage:    bool        = Form(False),
    road_blocked:         bool        = Form(False),
    files: List[UploadFile]           = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Citizen submits a disaster report as a single multipart request.
    Photos (optional) are uploaded to Azure Blob Storage inline.
    Report starts with status=PENDING until reviewed by ERT.
    Requires: citizen Bearer token.
    """
    uploaded_files = []
    if files and files[0].filename:
        blob_result    = await upload_multiple_files(files)
        uploaded_files = blob_result["uploaded_files"]

    service = DisasterReportService(db)
    report  = await service.submit_report(
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
    background_tasks.add_task(_publish_report_event, {"report_id": report["id"]})
    return DisasterReportResponse(**report)


@router.post(
    "/upload-media",
    response_model=BlobUploadBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload disaster photos to blob storage (step 1 of two-step flow)",
)
async def upload_media(
    files: List[UploadFile] = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Upload photos/videos to Azure Blob Storage.
    Returns URLs to include in the subsequent POST / request.
    Requires: citizen Bearer token.
    """
    result = await upload_multiple_files(files)
    return BlobUploadBatchResponse(**result)


@router.get(
    "/pending",
    summary="List pending reports awaiting ERT review (Admin)",
)
async def list_pending_reports(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Returns all reports with status=PENDING sorted by created_at desc.
    Requires: emergency team Bearer token.
    """
    service = DisasterReportService(db)
    reports = await service.get_pending_reports(limit=limit)
    return {"pending_reports": reports, "count": len(reports)}


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic routes last
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=DisasterReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create disaster report with pre-uploaded photo URLs (step 2 of two-step flow)",
)
async def create_disaster_report(
    data: DisasterReportCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Create a disaster report with photo URLs from a prior /upload-media call.
    Use this when the frontend uploads photos first, then submits the form.
    Requires: citizen Bearer token.
    """
    service = DisasterReportService(db)
    report  = await service.create_report(
        user_id=data.user_id,
        location_address=data.location_address,
        disaster_type=data.disaster_type,
        severity=data.severity,
        description=data.description,
        latitude=data.latitude,
        longitude=data.longitude,
        people_affected=data.people_affected,
        multiple_casualties=data.multiple_casualties,
        structural_damage=data.structural_damage,
        road_blocked=data.road_blocked,
        photos=data.photos,
        reference_id=data.reference_id,
    )
    background_tasks.add_task(_publish_report_event, {"report_id": report["id"]})
    return DisasterReportResponse(**report)


@router.get(
    "/{report_id}",
    response_model=DisasterReportResponse,
    summary="Get a single disaster report",
)
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Fetch a report by ID. Citizens can view their own reports;
    ERT members can view any report.
    Requires: any valid Bearer token.
    """
    service = DisasterReportService(db)
    report  = await service.get_report(report_id)
    return DisasterReportResponse(**report)


@router.post(
    "/{report_id}/review",
    response_model=AdminReviewResponse,
    summary="Review a disaster report — approve or reject (Admin)",
)
async def review_report(
    report_id: str,
    review: AdminReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    ERT admin approves or rejects a pending disaster report.
    Approval triggers disaster creation and evaluation pipeline.
    Requires: emergency team Bearer token.
    """
    service = DisasterReportService(db)
    result  = await service.review_report(report_id, review)
    return AdminReviewResponse(**result)