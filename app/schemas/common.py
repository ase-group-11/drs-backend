# File: app/schemas/common.py
"""
Common schemas shared across the application.

Provides base response types used throughout the API.
"""

from typing import Optional
from pydantic import BaseModel, Field


class ResponseBase(BaseModel):
    """
    Base response schema with a simple message.
    
    Used for simple success responses.
    """
    message: str = Field(..., description="Response message")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {"message": "Operation completed successfully"}
            ]
        }
    }


class ErrorResponse(BaseModel):
    """
    Standard error response schema.
    
    Provides consistent error formatting across the API.
    """
    error: str = Field(..., description="Error type/code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[dict] = Field(None, description="Additional error details")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "error": "ValidationError",
                    "message": "Invalid phone number format",
                    "details": {
                        "field": "phone_number",
                        "constraint": "E.164 format required"
                    }
                }
            ]
        }
    }


class TokenResponse(BaseModel):
    """
    JWT token response schema.
    
    Returned after successful authentication.
    """
    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiry in seconds")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "token_type": "bearer",
                    "expires_in": 1800
                }
            ]
        }
    }


class OTPResponse(BaseModel):
    """
    OTP response schema (for testing/development only).
    
    In production, OTP should only be sent via SMS, never in API response.
    """
    otp: str = Field(..., description="Generated OTP code")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {"otp": "123456"}
            ]
        }
    }