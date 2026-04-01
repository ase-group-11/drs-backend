# File: app/schemas/evacuation.py
"""
Evacuation schemas — UC8: Plan Evacuation (v2)

v2 changes:
  - RouteBlockageRequest: removed affected_zone_ids (no more zones)
  - EscalationRequest: increased_radius_km + additional_roads replace new_zone_ids
  - UpdateProgressRequest: uses generic keys (e.g. "impact_area") not zone IDs
"""

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional


class PlanEvacuationRequest(BaseModel):
    disaster_id: str = Field(..., description="ID of the active disaster")
    auto_approve: bool = Field(False, description="Skip approval step if True")


class ApproveEvacuationRequest(BaseModel):
    approved_by: str = Field(..., description="Name or employee ID of approving officer")
    notes: Optional[str] = Field(None, description="Optional approval notes")


class UpdateProgressRequest(BaseModel):
    completion_metrics: Dict[str, Any] = Field(
        ...,
        description=(
            'Keyed by "impact_area". '
            'E.g. {"impact_area": {"percentage": 45, "evacuated": 5726, "remaining": 6997}}'
        ),
    )


class RouteBlockageRequest(BaseModel):
    blocked_roads: List[str] = Field(..., description="Names of newly blocked roads")


class EscalationRequest(BaseModel):
    increased_radius_km: Optional[float] = Field(
        None,
        description="New impact radius in km (must be larger than current)",
    )
    additional_roads: Optional[List[str]] = Field(
        None,
        description="Road names to add to the affected area",
    )
    reason: str = Field(..., description="Plain-text reason for escalation")