# # File: app/core/config.py
# """
# Configuration management using Pydantic Settings.

# Loads configuration from environment variables with validation.
# Supports multiple environments (development, testing, production).
# """

# from functools import lru_cache
# from typing import Literal
# from pydantic import Field, field_validator
# from pydantic_settings import BaseSettings, SettingsConfigDict


# class Settings(BaseSettings):
#     """
#     Application settings loaded from environment variables.
    
#     Uses Pydantic Settings for type-safe configuration with validation.
#     """
    
#     # Application
#     APP_NAME: str = Field(default="ASE Emergency Services", description="Application name")
#     APP_VERSION: str = Field(default="1.0.0", description="Application version")
    
#     # Environment
#     ENVIRONMENT: Literal["development", "testing", "production"] = Field(
#         default="development",
#         description="Application environment"
#     )
#     DEBUG: bool = Field(default=True, description="Debug mode")
    
#     # Database
#     DATABASE_URL: str = Field(
#         ...,  # Required field
#         description="PostgreSQL database URL (async driver)"
#     )
    
#     # Redis
#     REDIS_URL: str = Field(
#         ...,  # Required field
#         description="Redis connection URL"
#     )
    
#     # JWT Configuration
#     JWT_SECRET_KEY: str = Field(
#         ...,  # Required field
#         min_length=32,
#         description="Secret key for JWT token signing (min 32 chars)"
#     )
#     JWT_ALGORITHM: str = Field(
#         default="HS256",
#         description="JWT signing algorithm"
#     )
#     ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
#         default=30,
#         description="Access token expiration in minutes"
#     )
#     REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
#         default=7,
#         description="Refresh token expiration in days"
#     )
    
#     # Twilio Configuration
#     TWILIO_ACCOUNT_SID: str = Field(
#         ...,  # Required field
#         description="Twilio Account SID"
#     )
#     TWILIO_AUTH_TOKEN: str = Field(
#         ...,  # Required field
#         description="Twilio Auth Token"
#     )
#     TWILIO_PHONE_NUMBER: str = Field(
#         ...,  # Required field
#         description="Twilio phone number for sending SMS"
#     )
    
#     # OTP Configuration
#     OTP_EXPIRY_SECONDS: int = Field(
#         default=300,  # 5 minutes
#         description="OTP expiration time in seconds"
#     )
#     OTP_LENGTH: int = Field(
#         default=6,
#         description="Length of OTP code"
#     )
    
#     # Application Info
#     APP_NAME: str = Field(
#         default="ASE Emergency Services API",
#         description="Application name"
#     )
#     APP_VERSION: str = Field(
#         default="1.0.0",
#         description="Application version"
#     )
    
#     # Pydantic Settings Configuration
#     model_config = SettingsConfigDict(
#         env_file=".env",
#         env_file_encoding="utf-8",
#         case_sensitive=True,
#         extra="ignore"  # Ignore extra fields in .env
#     )
    
#     @field_validator("JWT_SECRET_KEY")
#     @classmethod
#     def validate_jwt_secret(cls, v: str) -> str:
#         """Validate JWT secret key length for security."""
#         if len(v) < 32:
#             raise ValueError("JWT_SECRET_KEY must be at least 32 characters long")
#         return v
    
#     def model_post_init(self, __context) -> None:
#         """Post-initialization hook to set DEBUG based on ENVIRONMENT."""
#         # If DEBUG wasn't explicitly set via environment and is default True
#         # but ENVIRONMENT is not development, update DEBUG
#         if self.ENVIRONMENT != "development":
#             object.__setattr__(self, 'DEBUG', False)


# @lru_cache()
# def get_settings() -> Settings:
#     """
#     Get cached settings instance (singleton pattern).
    
#     Using lru_cache ensures settings are loaded only once.
#     This improves performance and ensures consistency.
    
#     Returns:
#         Settings: Application settings instance
#     """
#     return Settings()


# # Global settings instance for easy import
# settings = get_settings()



# File: app/core/config.py
"""
Configuration management using Pydantic Settings.

UPDATED:
- Access token expires in 1 YEAR (525600 minutes)
- Refresh token expires in 2 YEARS (730 days)
"""

from functools import lru_cache
from typing import Literal
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    Uses Pydantic Settings for type-safe configuration with validation.
    """
    
    # Application
    APP_NAME: str = Field(default="ASE Emergency Services", description="Application name")
    APP_VERSION: str = Field(default="1.0.0", description="Application version")
    
    # Environment
    ENVIRONMENT: Literal["development", "testing", "production"] = Field(
        default="development",
        description="Application environment"
    )
    DEBUG: bool = Field(default=True, description="Debug mode")
    
    # Database
    DATABASE_URL: str = Field(
        ...,  # Required field
        description="PostgreSQL database URL (async driver)"
    )
    
    # Redis
    REDIS_URL: str = Field(
        ...,  # Required field
        description="Redis connection URL"
    )
    
    # JWT Configuration
    JWT_SECRET_KEY: str = Field(
        ...,  # Required field
        min_length=32,
        description="Secret key for JWT token signing (min 32 chars)"
    )
    JWT_ALGORITHM: str = Field(
        default="HS256",
        description="JWT signing algorithm"
    )
    
    # UPDATED: 1 YEAR token expiry
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=525600,  # 365 days * 24 hours * 60 minutes = 525600 minutes (1 YEAR)
        description="Access token expiration in minutes (default: 1 year)"
    )
    
    # UPDATED: 2 YEARS refresh token
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
        default=730,  # 2 years
        description="Refresh token expiration in days (default: 2 years)"
    )
    
    # Twilio Configuration
    TWILIO_ACCOUNT_SID: str = Field(
        ...,  # Required field
        description="Twilio Account SID"
    )
    TWILIO_AUTH_TOKEN: str = Field(
        ...,  # Required field
        description="Twilio Auth Token"
    )
    TWILIO_PHONE_NUMBER: str = Field(
        ...,  # Required field
        description="Twilio phone number for sending SMS"
    )
    TWILIO_SERVICE_SID: str = Field(
        default="",
        description="Twilio Verify Service SID (optional)"
    )
    
    # OTP Configuration
    OTP_EXPIRY_SECONDS: int = Field(
        default=300,  # 5 minutes
        description="OTP expiration time in seconds"
    )
    OTP_LENGTH: int = Field(
        default=6,
        description="Length of OTP code"
    )
    
    # Logging Configuration
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )
    LOG_FILE: str = Field(
        default="logs/app.log",
        description="Log file path"
    )
    
    # Pydantic Settings Configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"  # Ignore extra fields in .env
    )
    
    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        """Validate JWT secret key length for security."""
        if len(v) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters long")
        return v
    
    def model_post_init(self, __context) -> None:
        """Post-initialization hook to set DEBUG based on ENVIRONMENT."""
        # If DEBUG wasn't explicitly set via environment and is default True
        # but ENVIRONMENT is not development, update DEBUG
        if self.ENVIRONMENT != "development":
            object.__setattr__(self, 'DEBUG', False)


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance (singleton pattern).
    
    Using lru_cache ensures settings are loaded only once.
    This improves performance and ensures consistency.
    
    Returns:
        Settings: Application settings instance
    """
    return Settings()


# Global settings instance for easy import
settings = get_settings()