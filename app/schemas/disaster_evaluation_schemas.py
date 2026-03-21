"""
Pydantic schemas for the Disaster Evaluation API.

The output schema is fixed and must not change between strategy versions.
Other services depend on this exact shape.
"""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# Valid uppercase severity literals — enforced at the API boundary
SeverityLiteral = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Valid flag literals
FlagLiteral = Literal[
    "NORMAL", "LIMITED_DATA", "FALSE_ALARM", "PENDING_REVIEW",
    "DUPLICATE", "ESCALATED", "CORROBORATED"
]

# Valid recommended service strings
ServiceLiteral = Literal["fire", "medical", "police"]


class EvaluationResponse(BaseModel):
    """
    Fixed-schema response for a disaster evaluation.

    This schema MUST NOT change — other services depend on it.
    Strategy swaps (Phase 2 XGBoost) produce this same shape.
    """

    disaster_id: str = Field(
        description="The report_id that was evaluated"
    )
    severity: SeverityLiteral = Field(
        description="Evaluated severity (always uppercase)"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score [0.0, 1.0]",
    )
    recommended_services: List[str] = Field(
        description="Ordered list of recommended emergency services",
        min_length=1,
    )
    trigger_deploy: bool = Field(
        description="Whether to auto-deploy response teams"
    )
    trigger_reroute: bool = Field(
        description="Whether to trigger traffic rerouting"
    )
    trigger_evacuation: bool = Field(
        description="Whether evacuation is recommended"
    )
    flag: FlagLiteral = Field(
        description="Evaluation quality flag"
    )
    strategy_used: str = Field(
        description="Strategy that produced this result, e.g. 'rules_v1'"
    )
    evaluated_at: datetime = Field(
        description="ISO timestamp of when this evaluation was produced"
    )
    # Impact assessment — None when evaluation was retrieved from cache/DB
    impact_radius_km: Optional[float] = Field(
        default=None,
        description="Estimated impact radius in kilometres",
    )
    estimated_population: Optional[int] = Field(
        default=None,
        description="Estimated number of people affected within impact radius",
    )
    affected_roads: Optional[List[str]] = Field(
        default=None,
        description="Road segments identified within the impact radius",
    )
    affected_facilities: Optional[List[str]] = Field(
        default=None,
        description="Critical facilities (hospitals, fire stations, schools, police) within the impact radius",
    )

    @field_validator("severity")
    @classmethod
    def severity_must_be_uppercase(cls, v: str) -> str:
        if v != v.upper():
            raise ValueError(f"severity must be uppercase, got '{v}'")
        return v

    model_config = {"from_attributes": True}


class RankedDisasterItem(BaseModel):
    """One entry in the active-ranked disaster list (Alternative Flow 1)."""

    rank: int = Field(description="Position in the severity-ranked list (1 = most critical)")
    disaster_id: str
    tracking_id: Optional[str] = None
    type: Optional[str] = None
    severity: SeverityLiteral
    status: Optional[str] = None
    location_address: Optional[str] = None
    people_affected: Optional[int] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    flag: Optional[FlagLiteral] = None
    recommended_services: List[str] = Field(default_factory=list)
    trigger_deploy: bool = False
    created_at: Optional[str] = None
    resource_note: Optional[str] = Field(
        default=None,
        description="Human-readable note about resource contention when multiple high-severity incidents are active",
    )

    model_config = {"from_attributes": True}


class ReviewRequest(BaseModel):
    """Request body for ERT approval/rejection of a PENDING_REVIEW disaster."""

    approved: bool = Field(
        description="True to approve and deploy, False to reject as false alarm"
    )
    reviewed_by_id: str = Field(
        description="UUID of the ERT member submitting the review"
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional review notes (required when rejecting)",
    )

    @model_validator(mode="after")
    def notes_required_on_rejection(self) -> "ReviewRequest":
        if not self.approved and not self.notes:
            raise ValueError("notes are required when rejecting a disaster report")
        return self


class ReviewResponse(BaseModel):
    """Response returned after an ERT review decision is recorded."""

    disaster_id: str
    approved: bool
    reviewed_by_id: str
    reviewed_at: datetime
    action_taken: str = Field(
        description="Human-readable summary of what the system did"
    )

    model_config = {"from_attributes": True}
