# File: app/services/blob_service.py
"""
Azure Blob Storage service for uploading disaster media.

Uses:
  - ASYNC Azure SDK (non-blocking)
  - PRIVATE container (no public access)
  - SAS URLs for secure, time-limited access to files

Workflow:
  1. Upload file to private Azure Blob container
  2. Generate SAS URL with 24-hour expiry
  3. Return SAS URL — frontend uses this to display images
  4. SAS URL expires after 24 hours — regenerate if needed
"""

import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import List
from urllib.parse import quote

from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient
from azure.storage.blob import (
    ContentSettings,
    generate_blob_sas,
    BlobSasPermissions,
)
from fastapi import UploadFile, HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

# Allowed file types
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/x-msvideo"}
ALLOWED_MIME_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_VIDEO_TYPES

MAX_FILE_SIZE_MB = 50
MAX_FILES_PER_REPORT = 10

# SAS URL expiry time
SAS_EXPIRY_HOURS = 24


def _get_account_name_and_key():
    """Extract account name and key from connection string."""
    conn_str = settings.AZURE_STORAGE_CONNECTION_STRING
    parts = dict(part.split("=", 1) for part in conn_str.split(";") if "=" in part)
    return parts.get("AccountName"), parts.get("AccountKey")


def _generate_sas_url(blob_name: str, account_name: str, account_key: str) -> str:
    """
    Generate a SAS URL for a blob with read-only access.
    
    SAS = Shared Access Signature
    - Grants temporary, read-only access to a private blob
    - Expires after SAS_EXPIRY_HOURS (default 24 hours)
    - No need to make container public
    """
    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=settings.AZURE_CONTAINER_NAME,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=SAS_EXPIRY_HOURS),
    )

    sas_url = (
        f"https://{account_name}.blob.core.windows.net/"
        f"{settings.AZURE_CONTAINER_NAME}/{quote(blob_name)}?{sas_token}"
    )

    return sas_url

def refresh_sas_url(stored_url: str, refresh_threshold_hours: int = 2) -> str:
    """
    Return a fresh SAS URL only if the existing one expires within
    refresh_threshold_hours (default 2 hours). Otherwise return as-is.

    Logic:
      - Parse the 'se' (signed expiry) query param from the stored URL
      - If expiry > now + threshold → still valid, return original
      - If expiry <= now + threshold → regenerate fresh SAS URL
      - If URL has no 'se' param (not a SAS URL) → return original unchanged
    """
    try:
        from urllib.parse import urlparse, unquote, parse_qs
        from datetime import timezone

        parsed = urlparse(stored_url)
        qs = parse_qs(parsed.query)

        # Not a SAS URL — return as-is
        if 'se' not in qs:
            return stored_url

        # Parse expiry from SAS token — format: 2026-03-23T10%3A30%3A00Z
        expiry_str = qs['se'][0]
        expiry_dt = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
        threshold_dt = datetime.now(timezone.utc) + timedelta(hours=refresh_threshold_hours)

        # Still valid with comfortable margin — return original
        if expiry_dt > threshold_dt:
            return stored_url

        # Expiring soon or already expired — regenerate
        account_name, account_key = _get_account_name_and_key()
        container = settings.AZURE_CONTAINER_NAME
        path = unquote(parsed.path)
        prefix = f"/{container}/"
        if prefix not in path:
            return stored_url
        blob_name = path.split(prefix, 1)[1]
        new_url = _generate_sas_url(blob_name, account_name, account_key)
        logger.info(f"refresh_sas_url: refreshed expiring SAS for {blob_name[:40]}...")
        return new_url

    except Exception as e:
        logger.warning(f"refresh_sas_url: could not refresh — returning original. Error: {e}")
        return stored_url


async def upload_single_file(file: UploadFile) -> dict:
    """
    Upload one file to Azure Blob Storage (async, private container).

    Returns:
        dict with image_url (SAS URL), file_size, mime_type, original_filename
    """
    # Validate mime type
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{file.content_type}' not allowed. "
                   f"Allowed types: {', '.join(ALLOWED_MIME_TYPES)}"
        )

    # Read file content FIRST (before any DB/network operations)
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
        # Upload to private container using async SDK
        async with AsyncBlobServiceClient.from_connection_string(
            settings.AZURE_STORAGE_CONNECTION_STRING
        ) as blob_service_client:
            blob_client = blob_service_client.get_blob_client(
                container=settings.AZURE_CONTAINER_NAME,
                blob=blob_name
            )

            await blob_client.upload_blob(
                content,
                overwrite=True,
                content_settings=ContentSettings(content_type=file.content_type),
            )

        # Generate SAS URL for secure access
        account_name, account_key = _get_account_name_and_key()
        sas_url = _generate_sas_url(blob_name, account_name, account_key)

        logger.info(f"Uploaded file to blob: {blob_name} ({file_size} bytes) — SAS URL generated")

        return {
            "image_url": sas_url,
            "blob_name": blob_name,
            "file_size": file_size,
            "mime_type": file.content_type,
            "original_filename": file.filename,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Blob upload failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file to storage: {str(e)}"
        )


async def upload_multiple_files(files: List[UploadFile]) -> dict:
    """
    Upload a batch of files. ALL files share one reference_id.

    Returns:
        dict with uploaded_files list (each with SAS URL) and shared reference_id
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


async def regenerate_sas_url(blob_name: str) -> str:
    """
    Regenerate a SAS URL for an existing blob.
    
    Use when the old SAS URL has expired (after 24 hours).
    Frontend can call this to get a fresh URL.
    """
    account_name, account_key = _get_account_name_and_key()
    return _generate_sas_url(blob_name, account_name, account_key)