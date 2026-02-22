# File: app/models/__init__.py
"""
SQLAlchemy models package.
Exports all models and enums for easy importing.
"""
from app.db.models.base import Base
from app.db.models.enums import (
    UserStatus,
    UserRole,
    EmergencyTeamRole,
    Department,
    OTPStatus,
    EmergencyRequestStatus,
    DisasterType,
    DisasterSeverity,
    DisasterStatus,
    DisasterReportStatus,
    UnitType,
    UnitStatus,
    EvaluationFlag,
    RecommendedService,
)
from app.db.models.user import User
from app.db.models.emergency_team import EmergencyTeam       
from app.db.models.disaster import Disaster, DisasterPhoto   
from app.db.models.disaster_report import DisasterReport
from app.db.models.emergency_unit import EmergencyUnit
from app.db.models.disaster_evaluation import DisasterEvaluation

__all__ = [
    "Base",
    "User",
    "EmergencyTeam",
    "Disaster",
    "DisasterReport",
    "DisasterPhoto",
    "EmergencyUnit",
    "DisasterEvaluation",
    "UserStatus",
    "UserRole",
    "EmergencyTeamRole",
    "Department",
    "OTPStatus",
    "EmergencyRequestStatus",
    "DisasterType",
    "DisasterSeverity",
    "DisasterStatus",
    "DisasterReportStatus",
    "UnitType",
    "UnitStatus",
    "EvaluationFlag",
    "RecommendedService",
]
