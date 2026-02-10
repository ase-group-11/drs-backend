# File: app/models/enums.py
"""
Enumerations for the ASE Emergency Services system.

Defines:
- User account status
- Emergency team roles
- Emergency service departments
- OTP verification status
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
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    DELETED = "deleted"

class UserRole(str, enum.Enum):
    """
    User roles in the system.
    
    Roles:
    - USER: Standard user with access to emergency services
    - PRIVILEGED: System administrator/ERT with elevated privileges
    """
    RESIDENT = "user"
    PRIVILEGED = "privileged"
    ADMIN = "IT"


class EmergencyTeamRole(str, enum.Enum):
    """
    Emergency team member roles.
    
    Roles (hierarchical):
    - ADMIN: Full system access, can manage all teams
    - MANAGER: Can manage team members and view all data
    - STAFF: Standard emergency responder access
    """
    ADMIN = "admin"
    MANAGER = "manager"
    STAFF = "staff"


class Department(str, enum.Enum):
    """
    Emergency service departments.
    
    Departments:
    - MEDICAL: Ambulance, paramedics, hospitals
    - POLICE: Law enforcement
    - FIRE: Fire department
    - IT: Technical support and system administration
    """
    MEDICAL = "medical"
    POLICE = "police"
    FIRE = "fire"
    IT = "it"


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
    PENDING = "pending"
    VERIFIED = "verified"
    EXPIRED = "expired"
    FAILED = "failed"
    REVOKED = "revoked"


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
    SUBMITTED = "submitted"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class DisasterType(str, enum.Enum):
    """
    Types of disasters that can be reported.

    Types:
    - FLOOD: Flooding, water-related disasters
    - FIRE: Fire emergencies
    - ACCIDENT: Vehicle accidents, collisions
    - MEDICAL: Medical emergencies
    - OTHER: Other types of disasters not categorized above
    """
    FLOOD = "flood"
    FIRE = "fire"
    ACCIDENT = "accident"
    MEDICAL = "medical"
    OTHER = "other"


class DisasterSeverity(str, enum.Enum):
    """
    Severity levels for disaster reports.

    Levels (from least to most severe):
    - LOW: Minor incident, no immediate danger
    - MEDIUM: Moderate incident, some risk
    - HIGH: Serious incident, significant risk
    - CRITICAL: Life-threatening situation, immediate response required
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DisasterStatus(str, enum.Enum):
    """
    Lifecycle status of disaster reports.

    States:
    - PENDING: Report submitted, awaiting verification/assignment
    - ASSIGNED: Report assigned to emergency team
    - IN_PROGRESS: Team actively responding to disaster
    - RESOLVED: Disaster situation resolved
    - CLOSED: Report closed (resolved and verified)
    """
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"