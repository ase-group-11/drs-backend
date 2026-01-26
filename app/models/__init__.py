# File: app/models/__init__.py
"""
SQLAlchemy models package.

Exports all models and enums for easy importing.
"""

from app.models.base import Base
from app.models.enums import (
    UserStatus,
    EmergencyTeamRole,
    Department,
    OTPStatus,
    EmergencyRequestStatus,
)
from app.models.user import User
from app.models.emergency_team import EmergencyTeam

__all__ = [
    "Base",
    "User",
    "EmergencyTeam",
    "UserStatus",
    "EmergencyTeamRole",
    "Department",
    "OTPStatus",
    "EmergencyRequestStatus",
]