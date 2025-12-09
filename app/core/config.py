from pydantic_settings import BaseSettings
from pydantic import field_validator, ValidationInfo

class Settings(BaseSettings):
    PROJECT_NAME: str = "DRS Backend"
    
    # ----------------------------------------------------------------
    # DATABASE SETTINGS (Postgres / SQL Server)
    # ----------------------------------------------------------------
    # We default to "" to satisfy Pylance, but validate it below
    DATABASE_URL: str = ""

    # ----------------------------------------------------------------
    # REDIS SETTINGS (For OTP Caching)
    # ----------------------------------------------------------------
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0  # Default DB

    # ----------------------------------------------------------------
    # TWILIO SETTINGS (For SMS)
    # ----------------------------------------------------------------
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""
    
    # If True, prints OTP to console instead of sending SMS (Save money in dev)
    MOCK_SMS_MODE: bool = True 

    # Validate that these fields are not empty (i.e. loaded from .env)
    @field_validator("DATABASE_URL", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER")
    def check_required_fields(cls, v: str, info: ValidationInfo) -> str:
        if not v:
            raise ValueError(f"{info.field_name} cannot be empty. Please set it in your .env file.")
        return v

    class Config:
        # Pydantic will read this file to populate the variables above
        env_file = ".env"
        case_sensitive = True

# Initialize the settings once to be imported elsewhere
settings = Settings()