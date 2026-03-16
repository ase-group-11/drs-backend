
"""

Enumerations for the ASE Emergency Services system.

Defines:
- User account status
- Emergency team roles
- Emergency service departments
- OTP verification status
- Emergency request status
- Disaster types, severity, and status

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
    Types of disasters tracked in the live map system.
    Values must match PostgreSQL enum exactly (uppercase).
    """
    FLOOD = "FLOOD"
    FIRE = "FIRE"
    EARTHQUAKE = "EARTHQUAKE"
    HURRICANE = "HURRICANE"
    TORNADO = "TORNADO"
    TSUNAMI = "TSUNAMI"
    DROUGHT = "DROUGHT"
    HEATWAVE = "HEATWAVE"
    COLDWAVE = "COLDWAVE"
    STORM = "STORM"
    OTHER = "OTHER"


class DisasterSeverity(str, enum.Enum):
    """
    Severity levels for disasters.
    Values must match PostgreSQL enum exactly (uppercase).
    """
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DisasterStatus(str, enum.Enum):
    """
    Status of disaster tracking and response.
    Values must match PostgreSQL enum exactly (uppercase).
    """
    ACTIVE = "ACTIVE"
    MONITORING = "MONITORING"
    RESOLVED = "RESOLVED"
    ARCHIVED = "ARCHIVED"


class DisasterReportStatus(str, enum.Enum):
    """
    Validation status for disaster reports (especially user-reported).
    
    Flow: PENDING → VERIFIED/REJECTED/DUPLICATE
    
    States:
    - PENDING: Report submitted, awaiting verification
    - VERIFIED: Report verified by emergency team, disaster is legitimate
    - REJECTED: Report rejected (false alarm, insufficient evidence)
    - DUPLICATE: Duplicate of existing disaster report
    """
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"

class OverrideType(str, enum.Enum):
    """
    Operator override types for manual traffic control.

    Types:
    - CLOSE_LANE:         Force a road segment closed
    - OPEN_LANE:          Force a road segment open (overrides auto-close)
    - PIN_DETOUR:         Lock a specific route as the preferred detour
    - CORRIDOR_PRIORITY:  Reserve a corridor for emergency vehicles only
    """
    CLOSE_LANE = "close_lane"
    OPEN_LANE = "open_lane"
    PIN_DETOUR = "pin_detour"
    CORRIDOR_PRIORITY = "corridor_priority"


class RoadSegmentStatus(str, enum.Enum):
    """
    Operational status of a road segment.

    States:
    - OPEN:       Normal traffic flow
    - CLOSED:     Road blocked, no traffic permitted
    - RESTRICTED: Limited access (emergency vehicles only, reduced lanes)
    """
    OPEN = "open"
    CLOSED = "closed"
    RESTRICTED = "restricted"


class ReroutePlanStatus(str, enum.Enum):
    """
    Lifecycle status of a reroute plan.

    States:
    - ACTIVE:      Currently in effect
    - SUPERSEDED:  Replaced by a newer plan (congestion recalc / override)
    - CLEARED:     Disaster resolved, plan retired
    """
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    CLEARED = "cleared"


class OverridePriority(str, enum.Enum):
    """
    Priority level for operator overrides.

    Levels:
    - LOW:      Advisory only
    - MEDIUM:   Standard operator instruction
    - HIGH:     Urgent, takes precedence over auto-routing
    - CRITICAL: Emergency corridor, overrides all other routing
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"