# File: app/models/enums.py
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
    
    Types:
    - FLOOD: Flooding events
    - FIRE: Fire emergencies
    - EARTHQUAKE: Seismic events
    - HURRICANE: Hurricane/cyclone
    - TORNADO: Tornado events
    - TSUNAMI: Tsunami warnings/events
    - DROUGHT: Drought conditions
    - HEATWAVE: Extreme heat
    - COLDWAVE: Extreme cold
    - STORM: Storm events
    - OTHER: Other disaster types
    """
    FLOOD = "flood"
    FIRE = "fire"
    EARTHQUAKE = "earthquake"
    HURRICANE = "hurricane"
    TORNADO = "tornado"
    TSUNAMI = "tsunami"
    DROUGHT = "drought"
    HEATWAVE = "heatwave"
    COLDWAVE = "coldwave"
    STORM = "storm"
    OTHER = "other"


class DisasterSeverity(str, enum.Enum):
    """
    Severity levels for disasters.
    
    Levels:
    - LOW: Minor impact, limited response needed
    - MEDIUM: Moderate impact, standard response
    - HIGH: Significant impact, elevated response
    - CRITICAL: Severe impact, maximum response
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DisasterStatus(str, enum.Enum):
    """
    Status of disaster tracking and response.
    
    States:
    - ACTIVE: Disaster is currently ongoing
    - MONITORING: Situation being monitored
    - RESOLVED: Disaster resolved/contained
    - ARCHIVED: Historical record (no longer relevant)
    """
    ACTIVE = "active"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


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




class UnitType(str, enum.Enum):
    """
    Type of emergency unit / vehicle.

    Types:
    - AMBULANCE:       Medical response vehicle
    - FIRE_ENGINE:     Fire suppression truck
    - PATROL_CAR:      Police patrol vehicle
    - RAPID_RESPONSE:  Fast-response unit (any department)
    - HAZMAT:          Hazardous materials response unit
    - RESCUE:          Technical rescue unit (water, confined space, etc.)
    - COMMAND:         Mobile command / coordination vehicle
    """
    AMBULANCE       = "ambulance"
    FIRE_ENGINE     = "fire_engine"
    PATROL_CAR      = "patrol_car"
    RAPID_RESPONSE  = "rapid_response"
    HAZMAT          = "hazmat"
    RESCUE          = "rescue"
    COMMAND         = "command"


class UnitStatus(str, enum.Enum):
    """
    Operational status of an emergency unit.

    Lifecycle:
        AVAILABLE → DEPLOYED → ON_SCENE → RETURNING → AVAILABLE
                                                    ↘ MAINTENANCE
                                                    ↘ OFFLINE

    States:
    - AVAILABLE:    Ready to be dispatched
    - DEPLOYED:     Dispatched, en route to incident
    - ON_SCENE:     Actively working at the incident site
    - RETURNING:    Returning to base after an incident
    - MAINTENANCE:  Vehicle / equipment under maintenance
    - OFFLINE:      Unit decommissioned or out of service
    """
    AVAILABLE   = "available"
    DEPLOYED    = "deployed"
    ON_SCENE    = "on_scene"
    RETURNING   = "returning"
    MAINTENANCE = "maintenance"
    OFFLINE     = "offline"


class EvaluationFlag(str, enum.Enum):
    """
    Flag assigned to a disaster evaluation result.

    States:
    - NORMAL: Evaluation completed with sufficient data and no anomalies
    - LIMITED_DATA: Missing enrichment context reduced confidence
    - FALSE_ALARM: Low severity, no flags, zero casualties, low confidence
    - PENDING_REVIEW: High/critical severity but missing supporting evidence
    """
    NORMAL = "normal"
    LIMITED_DATA = "limited_data"
    FALSE_ALARM = "false_alarm"
    PENDING_REVIEW = "pending_review"
    DUPLICATE = "duplicate"
    ESCALATED = "escalated"
    CORROBORATED = "corroborated"


class RecommendedService(str, enum.Enum):
    """
    Recommended emergency services for dispatch.

    These are operational service categories, distinct from the
    Department enum used for team assignment.
    """
    FIRE_BRIGADE = "fire_brigade"
    AMBULANCE = "ambulance"
    POLICE = "police"
    RESCUE = "rescue"
