"""
DisasterEvaluation model — persists the output of the evaluation service.

One record per evaluation run. Multiple evaluations can exist for a single
report (re-evaluation, strategy comparison, etc.).
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SQLEnum,
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    CheckConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.db.models.base import Base
from app.db.models.enums import DisasterSeverity, EvaluationFlag


class DisasterEvaluation(Base):
    """
    Persisted result of a disaster report evaluation.

    Fields:
    - report_id: FK to disaster_reports.id (the report being evaluated)
    - severity: Evaluated severity level (may differ from reported)
    - confidence: Model confidence [0.0, 1.0]
    - recommended_services: JSON list of service strings
    - trigger_deploy: Whether to auto-deploy response teams
    - trigger_reroute: Whether to trigger traffic rerouting
    - trigger_evacuation: Whether evacuation is recommended
    - flag: Evaluation quality flag
    - strategy_used: Identifier of the strategy that produced this result
    - enrichment_context: JSON snapshot of traffic/weather data used
    - evaluated_at: When this evaluation was produced
    """

    __tablename__ = "disaster_evaluations"

    # FK to disaster_reports — cascade delete when report is removed
    report_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("disaster_reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Report this evaluation belongs to",
    )

    # Evaluated severity (may be refined from user-reported value)
    severity: Mapped[DisasterSeverity] = mapped_column(
        SQLEnum(DisasterSeverity, name="disaster_severity"),
        nullable=False,
        comment="Evaluated severity level",
    )

    # Confidence score [0.0, 1.0]
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Confidence score [0.0, 1.0]",
    )

    # JSON array of recommended service strings, e.g. ["fire_brigade", "ambulance"]
    recommended_services: Mapped[List] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        comment="Recommended emergency services",
    )

    # Deployment triggers
    trigger_deploy: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Auto-deploy response teams",
    )

    trigger_reroute: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Trigger traffic rerouting",
    )

    trigger_evacuation: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Evacuation recommended",
    )

    # Evaluation quality flag
    flag: Mapped[EvaluationFlag] = mapped_column(
        SQLEnum(EvaluationFlag, name="evaluation_flag"),
        nullable=False,
        default=EvaluationFlag.NORMAL,
        comment="Evaluation quality flag",
    )

    # Audit trail: which strategy produced this result
    strategy_used: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Strategy identifier, e.g. 'rules_v1' or 'xgboost_v1'",
    )

    # Snapshot of enrichment data used during evaluation
    enrichment_context: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Traffic and weather context used during evaluation",
    )

    # Explicit timestamp for when evaluation was produced
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="When this evaluation was produced",
    )

    __table_args__ = (
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_eval_confidence"),
        Index("idx_eval_report_id", "report_id"),
        Index("idx_eval_flag", "flag"),
        Index("idx_eval_evaluated_at", "evaluated_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<DisasterEvaluation(id={self.id}, report_id={self.report_id}, "
            f"severity={self.severity}, confidence={self.confidence:.2f})>"
        )
