# File: app/api/v1/incident_log.py
"""
Incident Timeline API — Disaster Logs / Activity History

Endpoint:
  GET /disasters/{disaster_id}/timeline

Requires Bearer token (any authenticated user).
No new table — queries existing tables only.
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
    summary="Get incident timeline",
    description=(
        "Returns the full activity timeline for a disaster. "
        "13 events from existing tables — no new table required. "
        "Ordered newest first."
    ),
)
async def get_incident_timeline(
    disaster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    **Response example:**
    ```json
    {
      "disaster_id":   "abc-123",
      "tracking_id":   "DIS-2026-A66F411A",
      "total_entries": 5,
      "entries": [
        {
          "event_type": "EVACUATION_ACTIVATED",
          "title":      "Evacuation Activated",
          "actor":      "System",
          "badge":      "System",
          "time":       "14:45",
          "timestamp":  "2026-03-27T14:45:00"
        },
        {
          "event_type": "UNITS_ARRIVED",
          "title":      "Units Arrived on Scene",
          "actor":      "System",
          "badge":      "System",
          "time":       "14:35",
          "timestamp":  "2026-03-27T14:35:00"
        },
        {
          "event_type": "UNITS_DEPLOYED",
          "title":      "Units Deployed",
          "actor":      "Admin User",
          "badge":      "Admin",
          "time":       "14:20",
          "timestamp":  "2026-03-27T14:20:00"
        },
        {
          "event_type": "DISASTER_REPORTED",
          "title":      "Disaster Reported",
          "actor":      "Citizen",
          "badge":      "Citizen",
          "time":       "14:10",
          "timestamp":  "2026-03-27T14:10:00"
        }
      ]
    }
    ```
    """
    service = IncidentLogService(db)
    return await service.get_timeline(disaster_id=disaster_id)