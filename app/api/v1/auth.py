import logging
from fastapi import APIRouter, Depends, status, BackgroundTasks
from sqlalchemy.orm import Session

from app.schemas.user_schemas import UserCreate, UserResponse, UserVerify
from app.dependencies import get_db
from app.services.auth_service import AuthService
from app.services.twilio_service import TwilioService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

@router.post("/signup/request-otp", status_code=status.HTTP_200_OK)
def request_signup_otp(
    user_data: UserCreate, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db)
) -> dict:
    """
    Initiates the registration process by sending an OTP.
    Fails if the user already exists.
    """
    logger.info(f"📩 Requesting OTP for mobile: {user_data.mobile_number}")
    
    service = AuthService(db)
    
    # Logic specifically for checking if user is NEW
    otp_code = service.initiate_registration(user_data.mobile_number)
    
    background_tasks.add_task(
        TwilioService.send_otp_sms, 
        user_data.mobile_number, 
        otp_code
    )
    
    logger.info(f"✅ OTP request accepted for {user_data.mobile_number}")
    return {"message": "OTP sent successfully. Please verify to complete registration."}

@router.post("/signup/verify", status_code=status.HTTP_201_CREATED)
def verify_signup_otp(
    verify_data: UserVerify,
    db: Session = Depends(get_db)
) -> dict:
    """
    Verifies the OTP and creates the new user account.
    Returns user data with session token for authentication.
    """
    logger.info(f"🔐 Verifying OTP for mobile: {verify_data.mobile_number}")

    service = AuthService(db)

    # Logic specifically for creating a NEW user
    user = service.verify_and_create(
        mobile_number=verify_data.mobile_number,
        otp_code=verify_data.otp_code
    )

    logger.info(f"🎉 User created successfully: ID {user.user_id}")

    # Return user data with session token
    return {
        "user_id": user.user_id,
        "mobile_number": user.mobile_number,
        "role": user.role,
        "is_verified": user.is_verified,
        "created_at": user.created_at,
        "session_token": getattr(user, 'session_token', None)
    }