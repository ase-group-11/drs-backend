# File: app/models/enums.py
"""
Enumerations for the ASE Emergency Services system.

Defines:
- User account status
- User roles
- Emergency team roles
- Emergency service departments
- OTP verification status
- Emergency request status
- Disaster types
- Severity levels
- Report status
"""

import enum


class UserStatus(str, enum.Enum):
    """
    User account status.
    
    States:
    - PENDING: Account created, awaiting OTP verification
    - ACTIVE: Account verified and active
    - INACTIVE: Account deactivated (can be reactivated)
    - SUSPENDED: Account suspended (security/policy violation)
    - DELETED: Soft deleted (can be recovered)
    """
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class UserRole(str, enum.Enum):
    """
    User roles in the system.
    
    Roles:
    - RESIDENT: Standard user with access to emergency services
    - PRIVILEGED: System administrator/ERT with elevated privileges
    - ADMIN: IT administrator with full system access
    """
    RESIDENT = "RESIDENT"
    PRIVILEGED = "PRIVILEGED"
    ADMIN = "ADMIN"


class EmergencyTeamRole(str, enum.Enum):
    """
    Emergency team member roles.
    
    Roles (hierarchical):
    - ADMIN: Full system access, can manage all teams
    - MANAGER: Can manage team members and view all data
    - STAFF: Standard emergency responder access
    """
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    STAFF = "STAFF"


class Department(str, enum.Enum):
    """
    Emergency service departments.
    
    Departments:
    - MEDICAL: Ambulance, paramedics, hospitals
    - POLICE: Law enforcement
    - FIRE: Fire department
    - IT: Technical support and system administration
    """
    MEDICAL = "MEDICAL"
    POLICE = "POLICE"
    FIRE = "FIRE"
    IT = "IT"


class OTPStatus(str, enum.Enum):
    """
    OTP verification status.
    
    States:
    - PENDING: OTP sent, awaiting verification
    - VERIFIED: OTP successfully verified
    - EXPIRED: OTP expired (TTL exceeded)
    - FAILED: OTP verification failed (wrong code)
    - REVOKED: OTP manually revoked/invalidated
    """
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    REVOKED = "REVOKED"


class EmergencyRequestStatus(str, enum.Enum):
    """
    Emergency request lifecycle status.
    
    States:
    - SUBMITTED: Request created by user
    - ASSIGNED: Request assigned to emergency team
    - IN_PROGRESS: Team responding to emergency
    - RESOLVED: Emergency resolved
    - CANCELLED: Request cancelled by user
    - REJECTED: Request rejected (false alarm, duplicate, etc.)
    """
    SUBMITTED = "SUBMITTED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


# ============================================================================
# NEW ENUMS FOR DISASTER REPORTS
# ============================================================================

class DisasterType(str, enum.Enum):
    """
    Types of disasters/emergencies that can be reported.
    """
    FIRE = "FIRE"
    FLOOD = "FLOOD"
    EARTHQUAKE = "EARTHQUAKE"
    MEDICAL_EMERGENCY = "MEDICAL_EMERGENCY"
    ACCIDENT = "ACCIDENT"
    CRIME = "CRIME"
    BUILDING_COLLAPSE = "BUILDING_COLLAPSE"
    GAS_LEAK = "GAS_LEAK"
    POWER_OUTAGE = "POWER_OUTAGE"
    WATER_CONTAMINATION = "WATER_CONTAMINATION"
    LANDSLIDE = "LANDSLIDE"
    STORM = "STORM"
    HAZMAT = "HAZMAT"  # Hazardous materials
    EXPLOSION = "EXPLOSION"
    RIOT = "RIOT"
    TERRORIST_ATTACK = "TERRORIST_ATTACK"
    OTHER = "OTHER"


class Severity(str, enum.Enum):
    """
    Severity levels for emergency reports.
    
    Levels:
    - LOW: Minor incidents, no immediate danger
    - MEDIUM: Significant incidents requiring attention
    - HIGH: Serious incidents requiring urgent response
    - CRITICAL: Life-threatening emergencies requiring immediate action
    """
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ReportStatus(str, enum.Enum):
    """
    Status of disaster reports throughout their lifecycle.
    
    States:
    - SUBMITTED: Report created by user, awaiting review
    - UNDER_REVIEW: Being reviewed by emergency team
    - ASSIGNED: Assigned to emergency team/department
    - IN_PROGRESS: Emergency team responding to the report
    - RESOLVED: Report has been addressed and closed
    - CANCELLED: Report cancelled by user or marked invalid
    - REJECTED: Report rejected (false alarm, duplicate, etc.)
    """
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"