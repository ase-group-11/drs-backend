from pydantic import BaseModel, Field, field_validator, ConfigDict
from datetime import datetime
import re

# Regex for Indian Mobile Numbers (E.164 format)
PHONE_REGEX = r"^\+91\d{10}$"

# Input: For the /auth/signup/request-otp endpoint
class UserCreate(BaseModel):
    mobile_number: str = Field(..., description="Mobile number in E.164 format (e.g. +919876543210)")

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        if not re.match(PHONE_REGEX, v):
            raise ValueError("Invalid mobile number. Must be an Indian mobile number in E.164 format (e.g. +919876543210)")
        return v

# Input: For the /auth/signup/verify endpoint
class UserVerify(BaseModel):
    mobile_number: str
    # Enforce exactly 6 digits for OTP
    otp_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")

# Output: Returned after successful verification/creation
class UserResponse(BaseModel):
    user_id: int
    mobile_number: str
    is_verified: bool
    created_at: datetime

    # Pydantic V2 Configuration (Replaces 'class Config')
    model_config = ConfigDict(from_attributes=True)