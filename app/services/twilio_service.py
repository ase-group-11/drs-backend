# File: app/services/twilio_service.py
"""
Twilio SMS service for sending OTP codes.

Provides:
- SMS sending via Twilio API
- Mock mode for testing (when ENVIRONMENT=testing)
- Error handling and retries
"""

from typing import Optional
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

from app.core.config import settings


def _get_twilio_client() -> Optional[Client]:
    """
    Get Twilio client instance.
    
    Returns None in testing environment to avoid actual SMS.
    
    Returns:
        Optional[Client]: Twilio client or None in testing
    """
    if settings.ENVIRONMENT == "testing":
        return None
    
    return Client(
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_AUTH_TOKEN
    )


async def send_sms(phone_number: str, message: str) -> bool:
    """
    Send SMS via Twilio.
    
    In testing environment, this function mocks the SMS sending
    and returns True without actually sending.
    
    Args:
        phone_number: Recipient phone number (E.164 format)
        message: SMS message content
        
    Returns:
        bool: True if sent successfully, False otherwise
        
    Example:
        >>> success = await send_sms("+1234567890", "Your OTP is 123456")
        >>> if success:
        >>>     print("SMS sent!")
        
    Note:
        Phone numbers must be in E.164 format (+1234567890).
        Twilio charges apply for each SMS sent in production.
    """
    # Mock mode for testing
    if settings.ENVIRONMENT == "testing":
        print(f"[MOCK SMS] To: {phone_number}, Message: {message}")
        return True
    
    try:
        client = _get_twilio_client()
        if not client:
            return False
        
        # Send SMS via Twilio
        message_response = client.messages.create(
            body=message,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=phone_number
        )
        
        # Check if message was queued/sent
        if message_response.sid:
            return True
        
        return False
        
    except TwilioRestException as e:
        # Log Twilio-specific errors
        print(f"Twilio error: {e.code} - {e.msg}")
        return False
        
    except Exception as e:
        # Log general errors
        print(f"SMS sending error: {str(e)}")
        return False


async def send_otp_sms(phone_number: str, otp: str) -> bool:
    """
    Send OTP via SMS (convenience function).
    
    Formats the OTP message and sends it.
    
    Args:
        phone_number: Recipient phone number
        otp: OTP code
        
    Returns:
        bool: True if sent successfully
        
    Example:
        >>> await send_otp_sms("+1234567890", "123456")
    """
    message = (
        f"Your ASE Emergency Services verification code is: {otp}. "
        f"Valid for {settings.OTP_EXPIRY_SECONDS // 60} minutes. "
        f"Do not share this code."
    )
    
    return await send_sms(phone_number, message)


async def send_welcome_sms(phone_number: str, full_name: str) -> bool:
    """
    Send welcome SMS after successful registration.
    
    Args:
        phone_number: User's phone number
        full_name: User's full name
        
    Returns:
        bool: True if sent successfully
    """
    message = (
        f"Welcome to ASE Emergency Services, {full_name}! "
        f"Your account has been activated. "
        f"You can now submit and track emergency requests."
    )
    
    return await send_sms(phone_number, message)