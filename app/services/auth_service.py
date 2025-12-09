from sqlalchemy.orm import Session
from fastapi import HTTPException, status
import logging

from app.models import User
from app.services.twilio_service import TwilioService
from app.core.cache import redis_client

logger = logging.getLogger(__name__)

class AuthService:
    def __init__(self, db: Session):
        self.db = db
        # OTP Expiration time in seconds (e.g., 5 minutes)
        self.OTP_EXPIRY_SECONDS = 300 

    def initiate_registration(self, mobile_number: str) -> str:
        """
        1. Check User Existence
        2. Generate OTP
        3. Store in Redis (Auto-expires)
        """
        logger.info(f"Checking existence of user {mobile_number}")
        
        # 1. Check if user already exists
        existing_user = self.db.query(User).filter(
            User.mobile_number == mobile_number
        ).first()
        
        if existing_user:
            logger.warning(f"Registration aborted: User {mobile_number} already exists.")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="User already exists and is verified."
            )

        # 2. Generate OTP
        generated_otp = TwilioService.generate_otp()

        # 3. Store in Redis
        redis_key = f"otp:{mobile_number}"
        redis_client.setex(redis_key, self.OTP_EXPIRY_SECONDS, generated_otp)
        
        logger.info(f"OTP generated and stored in Redis for {mobile_number} (Expires in {self.OTP_EXPIRY_SECONDS}s)")

        return generated_otp

    def verify_and_create(self, mobile_number: str, otp_code: str) -> User:
        """
        1. Check Redis for OTP
        2. Validate
        3. Create User in Postgres
        """
        redis_key = f"otp:{mobile_number}"
        
        # 1. Get OTP from Redis
        stored_otp = redis_client.get(redis_key)

        # If None, it means the key expired or never existed
        if not stored_otp:
            logger.warning(f"OTP verification failed: Token expired/missing for {mobile_number}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="OTP has expired or request is invalid."
            )

        # 2. Validate Code
        if stored_otp != otp_code:
            logger.warning(f"OTP verification failed: Invalid code for {mobile_number}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="Invalid OTP Code"
            )

        # 3. Create User (Idempotent check)
        existing_user = self.db.query(User).filter(
            User.mobile_number == mobile_number
        ).first()

        if existing_user:
            logger.info(f"User {mobile_number} already exists in DB. Returning existing profile.")
            # Cleanup Redis even if user existed, to prevent replay attacks
            redis_client.delete(redis_key)
            return existing_user

        logger.info(f"Creating new user record for {mobile_number}")
        new_user = User(mobile_number=mobile_number)
        self.db.add(new_user)
        self.db.commit()
        self.db.refresh(new_user)
        
        # 4. Cleanup: Delete the OTP so it can't be used again
        redis_client.delete(redis_key)
        
        return new_user