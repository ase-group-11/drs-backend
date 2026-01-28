# # File: app/schemas/auth.py
# """
# Authentication schemas (DTOs) for API requests and responses.

# Defines Pydantic models for:
# - User registration
# - OTP verification
# - Login
# - Token responses
# """

# from pydantic import BaseModel, Field, field_validator
# from typing import Optional
# import re

# try:
#     # Pydantic v2
#     from pydantic import field_validator
# except ImportError:
#     # Pydantic v1 fallback
#     from pydantic import validator as field_validator


# class UserRegisterRequest(BaseModel):
#     """
#     User registration request schema.
    
#     User provides phone number and basic info.
#     OTP will be sent for verification.
#     """
#     phone_number: str = Field(
#         ...,
#         description="Phone number in E.164 format (+1234567890)",
#         min_length=10,
#         max_length=15
#     )
#     full_name: str = Field(
#         ...,
#         description="User's full name",
#         min_length=2,
#         max_length=255
#     )
#     email: Optional[str] = Field(
#         None,
#         description="Optional email address"
#     )
    
#     @field_validator("phone_number")
#     @classmethod
#     def validate_phone_number(cls, v: str) -> str:
#         """Validate phone number is in E.164 format."""
#         # E.164 format: +[country code 1-3 digits][number 7-14 digits]
#         # Total: 8-15 digits after the +
#         if not re.match(r'^\+[1-9]\d{7,14}$', v):
#             raise ValueError(
#                 "Phone number must be in E.164 format (e.g., +1234567890)"
#             )
#         return v
    
#     @field_validator("email")
#     @classmethod
#     def validate_email(cls, v: Optional[str]) -> Optional[str]:
#         """Validate email format if provided."""
#         if v is None:
#             return v
#         # Basic email validation
#         if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
#             raise ValueError("Invalid email format")
#         return v.lower()
    
#     model_config = {
#         "json_schema_extra": {
#             "examples": [
#                 {
#                     "phone_number": "+1234567890",
#                     "full_name": "John Doe",
#                     "email": "john.doe@example.com"
#                 }
#             ]
#         }
#     }


# class OTPVerifyRequest(BaseModel):
#     """
#     OTP verification request schema.
    
#     Used for both registration and login verification.
#     """
#     phone_number: str = Field(
#         ...,
#         description="Phone number that received the OTP"
#     )
#     otp: str = Field(
#         ...,
#         description="6-digit OTP code",
#         min_length=6,
#         max_length=6
#     )
    
#     @field_validator("otp")
#     @classmethod
#     def validate_otp(cls, v: str) -> str:
#         """Validate OTP is 6 digits."""
#         if not v.isdigit():
#             raise ValueError("OTP must contain only digits")
#         if len(v) != 6:
#             raise ValueError("OTP must be exactly 6 digits")
#         return v
    
#     model_config = {
#         "json_schema_extra": {
#             "examples": [
#                 {
#                     "phone_number": "+1234567890",
#                     "otp": "123456"
#                 }
#             ]
#         }
#     }


# class UserLoginRequest(BaseModel):
#     """
#     User login request schema.
    
#     User provides phone number, OTP will be sent.
#     """
#     phone_number: str = Field(
#         ...,
#         description="Registered phone number"
#     )
    
#     @field_validator("phone_number")
#     @classmethod
#     def validate_phone_number(cls, v: str) -> str:
#         """Validate phone number format."""
#         if not re.match(r'^\+[1-9]\d{7,14}$', v):
#             raise ValueError(
#                 "Phone number must be in E.164 format (e.g., +1234567890)"
#             )
#         return v
    
#     model_config = {
#         "json_schema_extra": {
#             "examples": [
#                 {
#                     "phone_number": "+1234567890"
#                 }
#             ]
#         }
#     }


# class TokenResponse(BaseModel):
#     """
#     JWT token response schema.
    
#     Returned after successful OTP verification.
#     """
#     access_token: str = Field(
#         ...,
#         description="JWT access token (short-lived)"
#     )
#     refresh_token: str = Field(
#         ...,
#         description="JWT refresh token (long-lived)"
#     )
#     token_type: str = Field(
#         default="bearer",
#         description="Token type (always 'bearer')"
#     )
#     expires_in: int = Field(
#         ...,
#         description="Access token expiry in seconds"
#     )
    
#     model_config = {
#         "json_schema_extra": {
#             "examples": [
#                 {
#                     "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
#                     "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
#                     "token_type": "bearer",
#                     "expires_in": 1800
#                 }
#             ]
#         }
#     }


# class UserResponse(BaseModel):
#     """
#     User data response schema.
    
#     Public user information returned in API responses.
#     """
#     id: str = Field(..., description="User ID (UUID)")
#     phone_number: str = Field(..., description="Phone number")
#     full_name: str = Field(..., description="Full name")
#     email: Optional[str] = Field(None, description="Email address")
#     status: str = Field(..., description="Account status")
#     created_at: str = Field(..., description="Account creation timestamp")
    
#     model_config = {
#         "from_attributes": True,  # Allow conversion from ORM models
#         "json_schema_extra": {
#             "examples": [
#                 {
#                     "id": "550e8400-e29b-41d4-a716-446655440000",
#                     "phone_number": "+1234567890",
#                     "full_name": "John Doe",
#                     "email": "john.doe@example.com",
#                     "status": "active",
#                     "created_at": "2024-01-15T10:30:00Z"
#                 }
#             ]
#         }
#     }


# class AuthResponse(BaseModel):
#     """
#     Complete authentication response.
    
#     Combines token and user data.
#     """
#     user: UserResponse
#     tokens: TokenResponse
    
#     model_config = {
#         "json_schema_extra": {
#             "examples": [
#                 {
#                     "user": {
#                         "id": "550e8400-e29b-41d4-a716-446655440000",
#                         "phone_number": "+1234567890",
#                         "full_name": "John Doe",
#                         "email": "john.doe@example.com",
#                         "status": "active",
#                         "created_at": "2024-01-15T10:30:00Z"
#                     },
#                     "tokens": {
#                         "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
#                         "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
#                         "token_type": "bearer",
#                         "expires_in": 1800
#                     }
#                 }
#             ]
#         }
#     }


# class MessageResponse(BaseModel):
#     """
#     Simple message response.
    
#     Used for OTP sending confirmation, etc.
#     """
#     message: str = Field(..., description="Response message")
    
#     model_config = {
#         "json_schema_extra": {
#             "examples": [
#                 {
#                     "message": "OTP sent successfully to +1234567890"
#                 }
#             ]
#         }
#     }


# class ErrorResponse(BaseModel):
#     """
#     Error response schema.
    
#     Standard error format for API.
#     """
#     error: str = Field(..., description="Error type")
#     message: str = Field(..., description="Error message")
#     details: Optional[dict] = Field(None, description="Additional error details")
    
#     model_config = {
#         "json_schema_extra": {
#             "examples": [
#                 {
#                     "error": "ValidationError",
#                     "message": "Invalid phone number format",
#                     "details": {
#                         "field": "phone_number",
#                         "constraint": "E.164 format required"
#                     }
#                 }
#             ]
#         }
#     }






# File: app/schemas/auth.py (UPDATED for Frontend)
"""
Authentication schemas (DTOs) for API requests and responses.

UPDATED: Now accepts first_name + last_name OR full_name
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional
import re

try:
    from pydantic import field_validator
except ImportError:
    from pydantic import validator as field_validator


class UserRegisterRequest(BaseModel):
    """
    User registration request schema.
    
    UPDATED: Accepts either:
    - full_name (single field)
    - first_name + last_name (separate fields)
    """
    phone_number: str = Field(
        ...,
        description="Phone number in E.164 format (+1234567890)",
        min_length=10,
        max_length=15
    )
    
    # Option 1: Single full_name field
    full_name: Optional[str] = Field(
        None,
        description="User's full name (use this OR first_name + last_name)",
        min_length=2,
        max_length=255
    )
    
    # Option 2: Separate first and last name
    first_name: Optional[str] = Field(
        None,
        description="First name",
        min_length=1,
        max_length=100
    )
    
    last_name: Optional[str] = Field(
        None,
        description="Last name",
        min_length=1,
        max_length=100
    )
    
    email: Optional[str] = Field(
        None,
        description="Optional email address"
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
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        """Validate email format if provided."""
        if v is None:
            return v
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        return v.lower()
    
    @model_validator(mode='after')
    def validate_name_fields(self):
        """Ensure either full_name OR (first_name + last_name) is provided."""
        if not self.full_name and not (self.first_name and self.last_name):
            raise ValueError(
                "Either 'full_name' or both 'first_name' and 'last_name' must be provided"
            )
        
        # If first_name and last_name provided, combine into full_name
        if self.first_name and self.last_name and not self.full_name:
            self.full_name = f"{self.first_name} {self.last_name}"
        
        return self
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "phone_number": "+353891234567",
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "john.doe@example.com"
                },
                {
                    "phone_number": "+353891234567",
                    "full_name": "John Doe",
                    "email": "john.doe@example.com"
                }
            ]
        }
    }


class OTPVerifyRequest(BaseModel):
    """
    OTP verification request schema.
    """
    phone_number: str = Field(
        ...,
        description="Phone number that received the OTP"
    )
    otp: str = Field(
        ...,
        description="6-digit OTP code",
        min_length=6,
        max_length=6
    )
    
    @field_validator("otp")
    @classmethod
    def validate_otp(cls, v: str) -> str:
        """Validate OTP is 6 digits."""
        if not v.isdigit():
            raise ValueError("OTP must contain only digits")
        if len(v) != 6:
            raise ValueError("OTP must be exactly 6 digits")
        return v
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "phone_number": "+353891234567",
                    "otp": "123456"
                }
            ]
        }
    }


class UserLoginRequest(BaseModel):
    """
    User login request schema.
    """
    phone_number: str = Field(
        ...,
        description="Registered phone number"
    )
    
    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, v: str) -> str:
        """Validate phone number format."""
        if not re.match(r'^\+[1-9]\d{7,14}$', v):
            raise ValueError(
                "Phone number must be in E.164 format (e.g., +1234567890)"
            )
        return v
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "phone_number": "+353891234567"
                }
            ]
        }
    }


class TokenResponse(BaseModel):
    """
    JWT token response schema.
    """
    access_token: str = Field(
        ...,
        description="JWT access token (short-lived)"
    )
    refresh_token: str = Field(
        ...,
        description="JWT refresh token (long-lived)"
    )
    token_type: str = Field(
        default="bearer",
        description="Token type (always 'bearer')"
    )
    expires_in: int = Field(
        ...,
        description="Access token expiry in seconds"
    )


class UserResponse(BaseModel):
    """
    User data response schema.
    """
    id: str = Field(..., description="User ID (UUID)")
    phone_number: str = Field(..., description="Phone number")
    full_name: str = Field(..., description="Full name")
    email: Optional[str] = Field(None, description="Email address")
    status: str = Field(..., description="Account status")
    created_at: str = Field(..., description="Account creation timestamp")
    
    model_config = {
        "from_attributes": True,
    }


class AuthResponse(BaseModel):
    """
    Complete authentication response.
    """
    user: UserResponse
    tokens: TokenResponse


class MessageResponse(BaseModel):
    """
    Simple message response.
    """
    message: str = Field(..., description="Response message")


class ErrorResponse(BaseModel):
    """
    Error response schema.
    """
    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    details: Optional[dict] = Field(None, description="Additional error details")