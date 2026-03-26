# File: app/schemas/evacuation.py
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
        description='Zone-keyed data. E.g. {"zone_city_centre": {"percentage": 45, "evacuated": 11250, "remaining": 13750}}'
    )


class RouteBlockageRequest(BaseModel):
    blocked_roads: List[str] = Field(..., description="Names of newly blocked roads")
    affected_zone_ids: List[str] = Field(..., description="Zone IDs whose route uses blocked roads")


class EscalationRequest(BaseModel):
    new_zone_ids: List[str] = Field(
        ...,
        description=(
            "Zone IDs to add. Valid values: zone_city_centre, zone_northside, zone_southside, "
            "zone_docklands, zone_rathmines, zone_ballsbridge, zone_cabra, "
            "zone_crumlin, zone_clontarf, zone_ringsend"
        ),
    )
    reason: str = Field(..., description="Plain-text reason for escalation")
