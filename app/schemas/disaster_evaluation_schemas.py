"""
Pydantic schemas for the Disaster Evaluation API.

The output schema is fixed and must not change between strategy versions.
Other services depend on this exact shape.
"""

from datetime import datetime
from typing import List, Literal
from pydantic import BaseModel, Field, field_validator


# Valid uppercase severity literals — enforced at the API boundary
SeverityLiteral = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Valid flag literals
FlagLiteral = Literal["NORMAL", "LIMITED_DATA", "FALSE_ALARM", "PENDING_REVIEW"]

# Valid recommended service strings
ServiceLiteral = Literal["fire_brigade", "ambulance", "police", "rescue"]


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

    @field_validator("severity")
    @classmethod
    def severity_must_be_uppercase(cls, v: str) -> str:
        if v != v.upper():
            raise ValueError(f"severity must be uppercase, got '{v}'")
        return v

    model_config = {"from_attributes": True}
