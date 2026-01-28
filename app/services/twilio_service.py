# # File: app/services/twilio_service.py
# """
# Twilio SMS service for sending OTP codes.

# Provides:
# - SMS sending via Twilio API
# - Mock mode for testing (when ENVIRONMENT=testing)
# - Error handling and retries
# """

# from typing import Optional
# from twilio.rest import Client
# from twilio.base.exceptions import TwilioRestException

# from app.core.config import settings


# def _get_twilio_client() -> Optional[Client]:
#     """
#     Get Twilio client instance.
    
#     Returns None in testing environment to avoid actual SMS.
    
#     Returns:
#         Optional[Client]: Twilio client or None in testing
#     """
#     if settings.ENVIRONMENT == "testing":
#         return None
    
#     return Client(
#         settings.TWILIO_ACCOUNT_SID,
#         settings.TWILIO_AUTH_TOKEN
#     )


# async def send_sms(phone_number: str, message: str) -> bool:
#     """
#     Send SMS via Twilio.
    
#     In testing environment, this function mocks the SMS sending
#     and returns True without actually sending.
    
#     Args:
#         phone_number: Recipient phone number (E.164 format)
#         message: SMS message content
        
#     Returns:
#         bool: True if sent successfully, False otherwise
        
#     Example:
#         >>> success = await send_sms("+1234567890", "Your OTP is 123456")
#         >>> if success:
#         >>>     print("SMS sent!")
        
#     Note:
#         Phone numbers must be in E.164 format (+1234567890).
#         Twilio charges apply for each SMS sent in production.
#     """
#     # Mock mode for testing
#     if settings.ENVIRONMENT == "testing":
#         print(f"[MOCK SMS] To: {phone_number}, Message: {message}")
#         return True
    
#     try:
#         client = _get_twilio_client()
#         if not client:
#             return False
        
#         # Send SMS via Twilio
#         message_response = client.messages.create(
#             body=message,
#             from_=settings.TWILIO_PHONE_NUMBER,
#             to=phone_number
#         )
        
#         # Check if message was queued/sent
#         if message_response.sid:
#             return True
        
#         return False
        
#     except TwilioRestException as e:
#         # Log Twilio-specific errors
#         print(f"Twilio error: {e.code} - {e.msg}")
#         return False
        
#     except Exception as e:
#         # Log general errors
#         print(f"SMS sending error: {str(e)}")
#         return False


# async def send_otp_sms(phone_number: str, otp: str) -> bool:
#     """
#     Send OTP via SMS (convenience function).
    
#     Formats the OTP message and sends it.
    
#     Args:
#         phone_number: Recipient phone number
#         otp: OTP code
        
#     Returns:
#         bool: True if sent successfully
        
#     Example:
#         >>> await send_otp_sms("+1234567890", "123456")
#     """
#     message = (
#         f"Your ASE Emergency Services verification code is: {otp}. "
#         f"Valid for {settings.OTP_EXPIRY_SECONDS // 60} minutes. "
#         f"Do not share this code."
#     )
    
#     return await send_sms(phone_number, message)


# async def send_welcome_sms(phone_number: str, full_name: str) -> bool:
#     """
#     Send welcome SMS after successful registration.
    
#     Args:
#         phone_number: User's phone number
#         full_name: User's full name
        
#     Returns:
#         bool: True if sent successfully
#     """
#     message = (
#         f"Welcome to ASE Emergency Services, {full_name}! "
#         f"Your account has been activated. "
#         f"You can now submit and track emergency requests."
#     )
    
#     return await send_sms(phone_number, message)


# async def send_otp_via_verify(phone_number: str) -> bool:
#     """
#     Send OTP using Twilio Verify API.
    
#     More reliable than raw SMS for OTP delivery.
#     """
#     if settings.ENVIRONMENT == "testing":
#         print(f"[MOCK] Sending OTP to {phone_number}")
#         return True
    
#     try:
#         client = _get_twilio_client()
#         if not client:
#             return False
        
#         # Use Twilio Verify API
#         verification = client.verify \
#             .v2 \
#             .services(settings.TWILIO_SERVICE_SID) \
#             .verifications \
#             .create(to=phone_number, channel='sms')
        
#         return verification.status == 'pending'
        
#     except Exception as e:
#         print(f"Twilio Verify error: {str(e)}")
#         return False

# async def verify_otp_via_verify(phone_number: str, code: str) -> bool:
#     """
#     Verify OTP using Twilio Verify API.
#     """
#     if settings.ENVIRONMENT == "testing":
#         return True
    
#     try:
#         client = _get_twilio_client()
#         if not client:
#             return False
        
#         verification_check = client.verify \
#             .v2 \
#             .services(settings.TWILIO_SERVICE_SID) \
#             .verification_checks \
#             .create(to=phone_number, code=code)
        
#         return verification_check.status == 'approved'
        
#     except Exception as e:
#         print(f"Twilio Verify error: {str(e)}")
#         return False


# File: app/services/twilio_service.py
"""
Twilio SMS service for sending OTP codes.

ENHANCED VERSION with detailed logging and error handling.
"""

from typing import Optional
import logging

from app.core.config import settings

# Setup logging
logger = logging.getLogger(__name__)


def _get_twilio_client():
    """
    Get Twilio client instance.
    
    Returns None in testing environment to avoid actual SMS.
    """
    if settings.ENVIRONMENT == "testing":
        logger.info("🧪 TESTING mode - Using mock SMS (no Twilio)")
        return None
    
    logger.info("📡 PRODUCTION mode - Using real Twilio SMS")
    logger.debug(f"Twilio Account SID: {settings.TWILIO_ACCOUNT_SID[:10]}...")
    
    try:
        from twilio.rest import Client
        
        client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN
        )
        logger.debug("✅ Twilio client created")
        return client
        
    except Exception as e:
        logger.error(f"❌ Failed to create Twilio client: {e}")
        raise


async def send_sms(phone_number: str, message: str) -> bool:
    """
    Send SMS via Twilio.
    
    ENHANCED with detailed logging.
    """
    logger.info(f"📱 Sending SMS to {phone_number}")
    logger.debug(f"📝 Message length: {len(message)} characters")
    
    # Mock mode for testing
    if settings.ENVIRONMENT == "testing":
        logger.info("🧪 MOCK MODE - No real SMS sent")
        logger.info(f"[MOCK SMS] To: {phone_number}")
        logger.info(f"[MOCK SMS] Message: {message}")
        print(f"\n{'='*70}")
        print(f"🧪 MOCK SMS")
        print(f"{'='*70}")
        print(f"📱 To: {phone_number}")
        print(f"📝 Message: {message}")
        print(f"{'='*70}\n")
        return True
    
    # Real Twilio mode
    logger.info("📡 Attempting real SMS via Twilio...")
    
    try:
        from twilio.rest import Client
        from twilio.base.exceptions import TwilioRestException
        
        # Create client
        logger.debug("🔧 Creating Twilio client...")
        client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN
        )
        logger.debug("✅ Twilio client created")
        
        # Send SMS
        logger.debug(f"📤 Sending message from {settings.TWILIO_PHONE_NUMBER}...")
        message_response = client.messages.create(
            body=message,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=phone_number
        )
        
        # Check response
        logger.info(f"✅ SMS sent successfully!")
        logger.debug(f"Message SID: {message_response.sid}")
        logger.debug(f"Status: {message_response.status}")
        
        if message_response.sid:
            return True
        
        logger.error("❌ No SID returned from Twilio")
        return False
        
    except TwilioRestException as e:
        logger.error(f"❌ Twilio error: {e.code} - {e.msg}")
        logger.error(f"   Status: {e.status}")
        logger.error(f"   More info: {e.uri}")
        
        # Common error codes
        if e.code == 20003:
            logger.error("   ⚠️  Authentication failed - check Account SID and Auth Token")
        elif e.code == 21211:
            logger.error("   ⚠️  Invalid 'To' phone number")
        elif e.code == 21606:
            logger.error("   ⚠️  Invalid 'From' phone number (check TWILIO_PHONE_NUMBER)")
        elif e.code == 21608:
            logger.error("   ⚠️  Trial account cannot send to unverified numbers")
        
        return False
        
    except Exception as e:
        logger.exception(f"❌ Unexpected SMS error: {type(e).__name__}")
        return False


async def send_otp_sms(phone_number: str, otp: str) -> bool:
    """
    Send OTP via SMS (convenience function).
    
    ENHANCED with detailed logging.
    """
    logger.info(f"📱 Preparing OTP SMS for {phone_number}")
    
    message = (
        f"Your ASE Emergency Services verification code is: {otp}. "
        f"Valid for {settings.OTP_EXPIRY_SECONDS // 60} minutes. "
        f"Do not share this code."
    )
    
    logger.debug(f"📝 OTP message prepared: {len(message)} chars")
    return await send_sms(phone_number, message)


async def send_welcome_sms(phone_number: str, full_name: str) -> bool:
    """Send welcome SMS after successful registration."""
    logger.info(f"📱 Sending welcome SMS to {phone_number}")
    
    message = (
        f"Welcome to ASE Emergency Services, {full_name}! "
        f"Your account has been activated. "
        f"You can now submit and track emergency requests."
    )
    
    return await send_sms(phone_number, message)


async def send_otp_via_verify(phone_number: str) -> bool:
    """
    Send OTP using Twilio Verify API.
    
    ENHANCED with detailed logging.
    """
    logger.info(f"📱 Sending OTP via Twilio Verify API to {phone_number}")
    
    if settings.ENVIRONMENT == "testing":
        logger.info("🧪 MOCK MODE - Verify API")
        print(f"[MOCK] Sending OTP via Verify API to {phone_number}")
        return True
    
    try:
        from twilio.rest import Client
        
        logger.debug("🔧 Creating Twilio client...")
        client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN
        )
        
        logger.debug(f"📤 Sending verification to {phone_number}...")
        verification = client.verify \
            .v2 \
            .services(settings.TWILIO_SERVICE_SID) \
            .verifications \
            .create(to=phone_number, channel='sms')
        
        logger.info(f"✅ Verification sent: {verification.status}")
        return verification.status == 'pending'
        
    except Exception as e:
        logger.exception(f"❌ Twilio Verify error")
        return False


async def verify_otp_via_verify(phone_number: str, code: str) -> bool:
    """
    Verify OTP using Twilio Verify API.
    
    ENHANCED with detailed logging.
    """
    logger.info(f"🔍 Verifying OTP via Twilio Verify API for {phone_number}")
    
    if settings.ENVIRONMENT == "testing":
        logger.info("🧪 MOCK MODE - Auto-approve")
        return True
    
    try:
        from twilio.rest import Client
        
        client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN
        )
        
        verification_check = client.verify \
            .v2 \
            .services(settings.TWILIO_SERVICE_SID) \
            .verification_checks \
            .create(to=phone_number, code=code)
        
        logger.info(f"Verification status: {verification_check.status}")
        return verification_check.status == 'approved'
        
    except Exception as e:
        logger.exception(f"❌ Twilio Verify check error")
        return False