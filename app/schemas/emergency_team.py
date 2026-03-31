# File: app/schemas/emergency_team.py
"""
Emergency team schemas (DTOs) for API requests and responses.

Defines Pydantic models for:
- Emergency team registration
- Emergency team login (2-step: password → OTP)
- Team member responses
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional
import re


class EmergencyTeamRegisterRequest(BaseModel):
    """
    Emergency team registration request schema.

    Team members register with password (not OTP).
    """
    phone_number: str = Field(
        ...,
        description="Phone number in E.164 format (+1234567890)",
        min_length=10,
        max_length=15
    )
    password: str = Field(
        ...,
        description="Password (min 8 characters, must include uppercase, lowercase, number)",
        min_length=8,
        max_length=128
    )
    full_name: str = Field(
        ...,
        description="Full name",
        min_length=2,
        max_length=255
    )
    email: str = Field(
        ...,
        description="Email address"
    )
    role: str = Field(
        ...,
        description="Role: admin, manager, or staff"
    )
    department: str = Field(
        ...,
        description="Department: medical, police, fire, or it"
    )
    employee_id: Optional[str] = Field(
        None,
        description="Optional employee ID",
        max_length=50
    )

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, v: str) -> str:
        """Validate phone number is in E.164 format."""
        if not re.match(r'^\+[1-9]\d{7,14}$', v):
            raise ValueError(
                "Phone number must be in E.164 format (e.g., +1234567890)"
            )
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Validate email format."""
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        return v.lower()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """
        Validate password strength.

        Requirements:
        - At least 8 characters
        - Contains uppercase letter
        - Contains lowercase letter
        - Contains digit
        """
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")

        if not re.search(r'[A-Z]', v):
            raise ValueError("Password must contain at least one uppercase letter")

        if not re.search(r'[a-z]', v):
            raise ValueError("Password must contain at least one lowercase letter")

        if not re.search(r'\d', v):
            raise ValueError("Password must contain at least one digit")

        return v

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        """Validate role case-insensitively and return canonical enum value."""
        from app.utils.enum_utils import normalize_enum_value
        from app.db.models.enums import EmergencyTeamRole
        try:
            return normalize_enum_value(EmergencyTeamRole, v)
        except ValueError:
            valid = [m.value for m in EmergencyTeamRole]
            raise ValueError(f"Role must be one of: {valid} (case-insensitive)")

    @field_validator("department")
    @classmethod
    def validate_department(cls, v: str) -> str:
        """Validate department case-insensitively and return canonical enum value."""
        from app.utils.enum_utils import normalize_enum_value
        from app.db.models.enums import Department
        try:
            return normalize_enum_value(Department, v)
        except ValueError:
            valid = [m.value for m in Department]
            raise ValueError(f"Department must be one of: {valid} (case-insensitive)")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "phone_number": "+1234567890",
                    "password": "SecurePass123",
                    "full_name": "John Doe",
                    "email": "john.doe@emergency.ie",
                    "role": "staff",
                    "department": "medical",
                    "employee_id": "EMP001"
                }
            ]
        }
    }


class EmergencyTeamLoginRequest(BaseModel):
    """
    Emergency team login — Step 1 request schema.

    Team members initiate login with email + password.
    On success an OTP is dispatched to their registered phone number.
    """
    email: str = Field(
        ...,
        description="Registered email address"
    )
    password: str = Field(
        ...,
        description="Account password"
    )

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Validate email format."""
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        return v.lower()

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "email": "john.doe@emergency.ie",
                    "password": "SecurePass123"
                }
            ]
        }
    }


class EmergencyTeamLoginInitResponse(BaseModel):
    """
    Emergency team login — Step 1 response schema.

    Returns a short-lived login_token the client must present in step 2.
    The phone number is never exposed to the client.
    """
    message: str
    login_token: str = Field(
        ...,
        description="Short-lived token (TTL = OTP expiry) used to identify this login session in step 2"
    )


class EmergencyTeamLoginVerifyRequest(BaseModel):
    """
    Emergency team login — Step 2 request schema.

    The client submits the login_token received from step 1 and the OTP
    received via SMS. No phone number required — the server resolves it
    internally from the login_token.
    """
    login_token: str = Field(
        ...,
        description="Token returned by step 1 (POST /emergency-team/login)"
    )
    otp: str = Field(
        ...,
        description="6-digit OTP received via SMS",
        min_length=6,
        max_length=6
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "login_token": "a3f8c2d1-...",
                    "otp": "482931"
                }
            ]
        }
    }


class EmergencyTeamResponse(BaseModel):
    """
    Emergency team member data response schema.

    Public information returned in API responses.
    """
    id: str = Field(..., description="Team member ID (UUID)")
    phone_number: str = Field(..., description="Phone number")
    full_name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    role: str = Field(..., description="Role (admin, manager, staff)")
    department: str = Field(..., description="Department")
    employee_id: Optional[str] = Field(None, description="Employee ID")
    status: str = Field(..., description="Account status")
    created_at: str = Field(..., description="Account creation timestamp")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "examples": [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "phone_number": "+1234567890",
                    "full_name": "John Doe",
                    "email": "john.doe@emergency.ie",
                    "role": "staff",
                    "department": "medical",
                    "employee_id": "EMP001",
                    "status": "active",
                    "created_at": "2024-01-15T10:30:00Z"
                }
            ]
        }
    }

class EmergencyTeamLoginResendOTPRequest(BaseModel):
    """
        Emergency team login - Resend OTP request schema.
        Used when the OTP SMS was not received within the 5-minute window.
    
    """

    login_token: str = Field(
        ..., 
        description = "The login_token received from step 1 (POST /emergency-team/login)"
    )

    model_config = {
        "json_schema_extra" : {
            "examples" : [
                {
                    "login_token" : "a3f8c2d1-7b4e-...."
                }
            ]
        }
    }

class EmergencyTeamAuthResponse(BaseModel):
    """
    Complete emergency team authentication response.

    Combines team member and token data.
    Returned only after full 2-step login is complete.
    """
    team_member: EmergencyTeamResponse
    tokens: dict  # TokenResponse from common schemas

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "team_member": {
                        "id": "550e8400-e29b-41d4-a716-446655440000",
                        "phone_number": "+1234567890",
                        "full_name": "John Doe",
                        "email": "john.doe@emergency.ie",
                        "role": "staff",
                        "department": "medical",
                        "employee_id": "EMP001",
                        "status": "active",
                        "created_at": "2024-01-15T10:30:00Z"
                    },
                    "tokens": {
                        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "token_type": "bearer",
                        "expires_in": 1800
                    }
                }
            ]
        }
    }


class ChangePasswordRequest(BaseModel):
    """
    Password change request schema (requires current password).
    Used by authenticated team members to update their password.
    """
    old_password: str = Field(
        ...,
        description="Current password"
    )
    new_password: str = Field(
        ...,
        description="New password (min 8 characters)",
        min_length=8
    )

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        """Validate new password strength."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not re.search(r'[A-Z]', v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r'[a-z]', v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r'\d', v):
            raise ValueError("Password must contain at least one digit")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "old_password": "OldPassword123",
                    "new_password": "NewSecurePass456"
                }
            ]
        }
    }