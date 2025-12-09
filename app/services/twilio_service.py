import logging
import random
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from app.core.config import settings

logger = logging.getLogger(__name__)

class TwilioService:
    @staticmethod
    def generate_otp() -> str:
        """Generates a random 6-digit OTP"""
        return str(random.randint(100000, 999999))

    @staticmethod
    def send_otp_sms(to_number: str, otp_code: str) -> bool:
        """
        Sends the OTP via Twilio or logs it if in Mock mode.
        """
        if settings.MOCK_SMS_MODE:
            logger.info(f"🧪 MOCK SMS to {to_number}: Your OTP is {otp_code}")
            return True

        try:
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            message = client.messages.create(
                body=f"Your DRS Verification Code is: {otp_code}",
                from_=settings.TWILIO_PHONE_NUMBER,
                to=to_number
            )
            logger.info(f"✅ SMS Sent to {to_number}. SID: {message.sid}")
            return True
        except TwilioRestException as e:
            logger.error(f"❌ Twilio Error: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected Error sending SMS: {e}")
            return False