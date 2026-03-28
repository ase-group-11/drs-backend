# File: app/api/v1/incident_log.py
"""
Incident Timeline API

Single endpoint that builds a chronological activity log for a disaster
by querying existing tables — no separate audit table required.

Sources queried (up to 13 event types):
  - disaster_reports  → DISASTER_REPORTED
  - disasters         → DISASTER_CREATED, DISASTER_ESCALATED, DISASTER_RESOLVED
  - evacuation_plans  → EVACUATION_PLANNED, EVACUATION_APPROVED, EVACUATION_ACTIVATED
  - deployments       → UNITS_DEPLOYED, UNITS_ARRIVED, UNITS_COMPLETED
  - reroute_plans     → REROUTE_TRIGGERED, REROUTE_RESTORED
  - disaster_evaluation → EVALUATION_COMPLETED

Ordered newest-first. Requires any valid Bearer token.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.services.incident_log_service import IncidentLogService

router = APIRouter(tags=["Incident Timeline"])


@router.get(
    "/disasters/{disaster_id}/timeline",
    summary="Get the full activity timeline for a disaster",
)
async def get_incident_timeline(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Returns a unified activity log for a disaster assembled from existing tables.
    Each entry has: event_type, title, actor, badge, time (HH:MM), and timestamp (ISO-8601).
    Ordered newest first.

    Example response:
    ```json
    {
      "disaster_id":   "abc-123",
      "tracking_id":   "DRS-2026-A66F411A",
      "total_entries": 4,
      "entries": [
        { "event_type": "EVACUATION_ACTIVATED", "title": "Evacuation Activated",
          "actor": "System",     "badge": "System", "time": "14:45" },
        { "event_type": "UNITS_ARRIVED",        "title": "Units Arrived on Scene",
          "actor": "System",     "badge": "System", "time": "14:35" },
        { "event_type": "UNITS_DEPLOYED",       "title": "Units Deployed",
          "actor": "Admin User", "badge": "Admin",  "time": "14:20" },
        { "event_type": "DISASTER_REPORTED",    "title": "Disaster Reported",
          "actor": "Citizen",    "badge": "Citizen","time": "14:10" }
      ]
    }
    ```
    Requires: any valid Bearer token.
    """
    service = IncidentLogService(db)
    return await service.get_timeline(disaster_id=disaster_id)