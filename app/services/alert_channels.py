# # File: app/services/alert_channels.py
# """SMS (Twilio) + Email (SMTP). HIGH/CRITICAL only. Silent failure."""

# import logging, os, smtplib
# from email.mime.multipart import MIMEMultipart
# from email.mime.text import MIMEText
# from typing import Optional

# logger = logging.getLogger("alert_channels")

# SMS_EMAIL_SEVERITIES = {"CRITICAL", "HIGH"}

# TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID", "")
# TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN", "")
# TWILIO_FROM_NUMBER  = os.getenv("TWILIO_FROM_NUMBER", "")
# SMTP_HOST       = os.getenv("SMTP_HOST", "smtp.gmail.com")
# SMTP_PORT       = int(os.getenv("SMTP_PORT", "587"))
# SMTP_USER       = os.getenv("SMTP_USER", "")
# SMTP_PASSWORD   = os.getenv("SMTP_PASSWORD", "")
# SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USER)


# def should_send_external(severity: str) -> bool:
#     return severity.upper() in SMS_EMAIL_SEVERITIES


# def send_sms(to_number: str, body: str) -> bool:
#     if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER]):
#         logger.warning("Twilio not configured — SMS skipped")
#         return False
#     if not to_number:
#         return False
#     try:
#         from twilio.rest import Client
#         msg = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN).messages.create(
#             body=body, from_=TWILIO_FROM_NUMBER, to=to_number)
#         logger.info(f"SMS sent to {to_number} SID={msg.sid}")
#         return True
#     except Exception as exc:
#         logger.error(f"SMS failed to {to_number}: {exc}")
#         return False


# def send_email(to_email: str, subject: str, plain: str,
#                html: Optional[str] = None) -> bool:
#     if not all([SMTP_USER, SMTP_PASSWORD]):
#         logger.warning("SMTP not configured — Email skipped")
#         return False
#     if not to_email:
#         return False
#     try:
#         msg = MIMEMultipart("alternative")
#         msg["Subject"] = subject
#         msg["From"]    = SMTP_FROM_EMAIL
#         msg["To"]      = to_email
#         msg.attach(MIMEText(plain, "plain"))
#         if html:
#             msg.attach(MIMEText(html, "html"))
#         with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
#             s.ehlo(); s.starttls(); s.login(SMTP_USER, SMTP_PASSWORD)
#             s.sendmail(SMTP_FROM_EMAIL, to_email, msg.as_string())
#         logger.info(f"Email sent to {to_email}")
#         return True
#     except Exception as exc:
#         logger.error(f"Email failed to {to_email}: {exc}")
#         return False


# def build_html(title: str, message: str, tracking_id: str,
#                severity: str, location: str = "") -> str:
#     colours = {"CRITICAL": "#dc2626", "HIGH": "#ea580c",
#                "MEDIUM": "#d97706", "LOW": "#2563eb", "INFO": "#16a34a"}
#     c   = colours.get(severity.upper(), "#2563eb")
#     loc = f'<p style="color:#6b7280;font-size:14px;">Location: {location}</p>' if location else ""
#     return (f'<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">'
#             f'<div style="background:{c};color:#fff;padding:16px 24px;border-radius:8px 8px 0 0;">'
#             f'<h2 style="margin:0;">{title}</h2>'
#             f'<p style="margin:4px 0 0;opacity:.85;">Severity: {severity} | {tracking_id}</p></div>'
#             f'<div style="background:#f9fafb;padding:24px;border:1px solid #e5e7eb;border-radius:0 0 8px 8px;">'
#             f'<p style="font-size:16px;color:#374151;">{message}</p>{loc}'
#             f'<hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0;">'
#             f'<p style="color:#9ca3af;font-size:12px;">Dublin City Disaster Response System</p>'
#             f'</div></body></html>')


# File: app/services/alert_channels.py
"""
SMS (Twilio) + Email (SendGrid).

SendGrid is cleaner than SMTP — one API key, no SMTP config,
no Gmail App Passwords, higher deliverability, free tier = 100 emails/day.

Required .env entries:
    SENDGRID_API_KEY   = SG.xxxxxxxxxxxxxxxxxxxx
    SENDGRID_FROM_EMAIL = noreply@yourdomain.com  (must be verified in SendGrid)
    SENDGRID_FROM_NAME  = Dublin Disaster Response  (optional, shown in From header)

Twilio (SMS) — already configured in your .env:
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_PHONE_NUMBER
"""

import logging
from typing import Optional

logger = logging.getLogger("alert_channels")

SMS_EMAIL_SEVERITIES = {"CRITICAL", "HIGH"}


def _cfg():
    """Lazy-load settings so .env is already parsed."""
    from app.core.config import settings
    return settings


def should_send_external(severity: str) -> bool:
    return severity.upper() in SMS_EMAIL_SEVERITIES


# ── SMS via Twilio ────────────────────────────────────────────

def send_sms(to_number: str, body: str) -> bool:
    """Send SMS via Twilio. Silent failure — never raises."""
    cfg   = _cfg()
    sid   = getattr(cfg, "TWILIO_ACCOUNT_SID",  "")
    token = getattr(cfg, "TWILIO_AUTH_TOKEN",    "")
    from_ = getattr(cfg, "TWILIO_PHONE_NUMBER",  "") or getattr(cfg, "TWILIO_FROM_NUMBER", "")

    if not all([sid, token, from_]):
        logger.warning("Twilio not configured — SMS skipped")
        return False
    if not to_number:
        return False
    try:
        from twilio.rest import Client
        msg = Client(sid, token).messages.create(body=body, from_=from_, to=to_number)
        logger.info(f"SMS sent → {to_number}  SID={msg.sid}")
        return True
    except Exception as exc:
        logger.error(f"SMS failed → {to_number}: {exc}")
        return False


# ── Email via SendGrid ────────────────────────────────────────

def send_email(
    to_email: str,
    subject: str,
    plain: str,
    html: Optional[str] = None,
) -> bool:
    """
    Send email via SendGrid API. Silent failure — never raises.

    Uses settings from .env:
        SENDGRID_API_KEY      SG.xxxxxxxxxx  (required)
        SENDGRID_FROM_EMAIL   verified sender address (required)
        SENDGRID_FROM_NAME    display name in From header (optional)
    """
    cfg       = _cfg()
    api_key   = getattr(cfg, "SENDGRID_API_KEY",    "")
    from_addr = getattr(cfg, "SENDGRID_FROM_EMAIL",  "")
    from_name = getattr(cfg, "SENDGRID_FROM_NAME",   "Dublin Disaster Response")

    if not api_key:
        logger.warning(
            "SendGrid not configured — Email skipped. "
            "Add SENDGRID_API_KEY to your .env file."
        )
        return False
    if not from_addr:
        logger.warning(
            "SENDGRID_FROM_EMAIL not set — Email skipped. "
            "Add a verified sender address to your .env file."
        )
        return False
    if not to_email:
        return False

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content, MimeType

        message = Mail()
        message.from_email    = Email(from_addr, from_name)
        message.to            = [To(to_email)]
        message.subject       = subject

        # Always include plain text fallback
        message.add_content(Content(MimeType.text, plain))

        # HTML version if provided
        if html:
            message.add_content(Content(MimeType.html, html))

        sg = SendGridAPIClient(api_key)
        response = sg.send(message)

        if response.status_code in (200, 202):
            logger.info(f"Email sent → {to_email}  status={response.status_code}")
            return True
        else:
            logger.error(
                f"SendGrid unexpected status {response.status_code} "
                f"for {to_email}: {response.body}"
            )
            return False

    except Exception as exc:
        logger.error(f"Email failed → {to_email}: {exc}")
        return False


# ── HTML email template ───────────────────────────────────────

def build_html(
    title: str,
    message: str,
    tracking_id: str,
    severity: str,
    location: str = "",
) -> str:
    colours = {
        "CRITICAL": "#dc2626",
        "HIGH":     "#ea580c",
        "MEDIUM":   "#d97706",
        "LOW":      "#2563eb",
        "INFO":     "#16a34a",
    }
    c   = colours.get(severity.upper(), "#2563eb")
    loc = (
        f'<p style="color:#6b7280;font-size:14px;margin:8px 0 0;">📍 {location}</p>'
        if location else ""
    )
    return (
        '<html>'
        '<body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#f3f4f6;padding:24px;">'
        f'<div style="background:{c};color:#fff;padding:20px 24px;border-radius:8px 8px 0 0;">'
        f'<h2 style="margin:0;font-size:18px;">🚨 {title}</h2>'
        f'<p style="margin:6px 0 0;opacity:.85;font-size:13px;">'
        f'Severity: <strong>{severity}</strong> &nbsp;·&nbsp; Ref: {tracking_id}'
        f'</p>'
        '</div>'
        '<div style="background:#ffffff;padding:24px;border:1px solid #e5e7eb;border-radius:0 0 8px 8px;">'
        f'<p style="font-size:15px;color:#374151;line-height:1.6;margin:0 0 12px;">{message}</p>'
        f'{loc}'
        '<hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">'
        '<p style="color:#9ca3af;font-size:11px;margin:0;">'
        'Dublin City Disaster Response System &nbsp;·&nbsp; '
        'This is an automated alert &nbsp;·&nbsp; Do not reply'
        '</p>'
        '</div>'
        '</body>'
        '</html>'
    )