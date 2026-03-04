# File: app/services/blob_service.py
"""
Azure Blob Storage service for uploading disaster media.

STEP 1 of workflow:
  User uploads photos/videos → Azure Blob → returns URLs + reference_id.
  Frontend then sends these URLs when creating the disaster report.
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import List

from azure.storage.blob import BlobServiceClient, ContentSettings
from fastapi import UploadFile, HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

# Allowed file types
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/x-msvideo"}
ALLOWED_MIME_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_VIDEO_TYPES

MAX_FILE_SIZE_MB = 50
MAX_FILES_PER_REPORT = 10


def _get_blob_service_client():
    """Create Azure Blob Service client."""
    return BlobServiceClient.from_connection_string(
        settings.AZURE_STORAGE_CONNECTION_STRING
    )


async def upload_single_file(file: UploadFile) -> dict:
    """
    Upload one file to Azure Blob Storage.

    Returns:
        dict with image_url, file_size, mime_type, original_filename
    """
    # Validate mime type
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{file.content_type}' not allowed. "
                   f"Allowed types: {', '.join(ALLOWED_MIME_TYPES)}"
        )

    # Read and validate size
    content = await file.read()
    file_size = len(content)

    if file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size exceeds {MAX_FILE_SIZE_MB}MB limit."
        )

    # Generate unique blob path: year/month/day/uuid_filename
    now = datetime.now(timezone.utc)
    blob_name = (
        f"{now.year}/{now.month:02d}/{now.day:02d}/"
        f"{uuid.uuid4()}_{file.filename}"
    )

    try:
        blob_service_client = _get_blob_service_client()
        blob_client = blob_service_client.get_blob_client(
            container=settings.AZURE_CONTAINER_NAME,
            blob=blob_name
        )

        blob_client.upload_blob(
            content,
            overwrite=True,
            content_settings=ContentSettings(content_type=file.content_type),
        )

        image_url = blob_client.url
        logger.info(f"Uploaded file to blob: {blob_name} ({file_size} bytes)")

        return {
            "image_url": image_url,
            "file_size": file_size,
            "mime_type": file.content_type,
            "original_filename": file.filename,
        }

    except Exception as e:
        logger.error(f"Blob upload failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload file to storage."
        )


async def upload_multiple_files(files: List[UploadFile]) -> dict:
    """
    Upload a batch of files. ALL files share one reference_id.

    Example: Pratik uploads 3 images →
      All 3 go to blob, all 3 share reference_id = "abc-123"

    Returns:
        dict with uploaded_files list and shared reference_id
    """
    if len(files) > MAX_FILES_PER_REPORT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {MAX_FILES_PER_REPORT} files per report."
        )

    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided."
        )

    # One reference_id for the entire batch
    reference_id = str(uuid.uuid4())

    uploaded_files = []
    for file in files:
        result = await upload_single_file(file)
        uploaded_files.append(result)

    logger.info(f"Batch upload complete: {len(uploaded_files)} files, reference_id={reference_id}")

    return {
        "uploaded_files": uploaded_files,
        "reference_id": reference_id,
    }