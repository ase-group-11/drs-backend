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
)
from app.db.models.user import User
from app.db.models.emergency_team import EmergencyTeam
from app.db.models.disaster import Disaster

__all__ = [
    "Base",
    "User",
    "EmergencyTeam",
    "Disaster",
    "UserStatus",
    "UserRole",
    "EmergencyTeamRole",
    "Department",
    "OTPStatus",
    "EmergencyRequestStatus",
    "DisasterType",
    "DisasterSeverity",
    "DisasterStatus",
]