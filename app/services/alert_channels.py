

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

# SMS_EMAIL_SEVERITIES = {"CRITICAL", "HIGH"}
SMS_EMAIL_SEVERITIES = set()

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
    
# ── Forgot Password Email ─────────────────────────────────────
 
def send_forgot_password_email(
    to_email: str,
    full_name: str,
    temp_password: str,
) -> bool:
    """
    Send forgot-password email with temporary password via SendGrid.
 
    Uses the same send_email() helper — no extra config needed.
    """
    plain = (
        f"Hi {full_name},\n\n"
        f"You requested a password reset for your Dublin Disaster Response account.\n\n"
        f"Temporary password: {temp_password}\n\n"
        f"This password expires in 15 minutes and can only be used once.\n\n"
        f"Steps to reset:\n"
        f"1. Go to the Reset Password page\n"
        f"2. Enter your email and the temporary password above\n"
        f"3. Set your new password\n\n"
        f"If you did not request this, please ignore this email.\n\n"
        f"Dublin City Disaster Response System"
    )
 
    html = _build_forgot_password_html(full_name, temp_password)
 
    return send_email(
        to_email=to_email,
        subject="Your Temporary Password — Dublin Disaster Response",
        plain=plain,
        html=html,
    )
 
 
def _build_forgot_password_html(full_name: str, temp_password: str) -> str:
    """Clean, professional HTML email for the forgot-password flow."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Password Reset</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
 
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
 
          <!-- Header -->
          <tr>
            <td style="background:#1d4ed8;padding:24px 32px;border-radius:8px 8px 0 0;">
              <p style="margin:0;color:#93c5fd;font-size:12px;letter-spacing:2px;text-transform:uppercase;">
                Dublin City Disaster Response
              </p>
              <h1 style="margin:8px 0 0;color:#ffffff;font-size:22px;font-weight:700;">
                🔐 Password Reset Request
              </h1>
            </td>
          </tr>
 
          <!-- Body -->
          <tr>
            <td style="background:#ffffff;padding:32px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
              <p style="margin:0 0 16px;font-size:15px;color:#374151;">
                Hi <strong>{full_name}</strong>,
              </p>
              <p style="margin:0 0 24px;font-size:14px;color:#6b7280;line-height:1.7;">
                We received a request to reset your password. Use the temporary password
                below to access the Reset Password page and set a new password.
              </p>
 
              <!-- Temp password box -->
              <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
                <tr>
                  <td style="background:#eff6ff;border:2px dashed #1d4ed8;border-radius:8px;padding:20px;text-align:center;">
                    <p style="margin:0 0 8px;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:2px;">
                      Temporary Password
                    </p>
                    <p style="margin:0;font-size:28px;font-weight:700;color:#1d4ed8;letter-spacing:4px;font-family:Courier,monospace;">
                      {temp_password}
                    </p>
                  </td>
                </tr>
              </table>
 
              <!-- Expiry warning -->
              <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
                <tr>
                  <td style="background:#fef3c7;border-left:4px solid #f59e0b;border-radius:4px;padding:12px 16px;">
                    <p style="margin:0;font-size:13px;color:#92400e;">
                      ⏱ <strong>Expires in 15 minutes</strong> &nbsp;·&nbsp;
                      🔒 <strong>Single use only</strong> — this code becomes invalid after use.
                    </p>
                  </td>
                </tr>
              </table>
 
              <!-- Steps -->
              <p style="margin:0 0 12px;font-size:14px;font-weight:700;color:#374151;">
                How to reset your password:
              </p>
              <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
                <tr>
                  <td style="background:#f9fafb;border-radius:6px;padding:16px 20px;">
                    <p style="margin:0 0 8px;font-size:13px;color:#4b5563;">
                      <span style="display:inline-block;width:22px;height:22px;background:#1d4ed8;color:#fff;border-radius:50%;text-align:center;line-height:22px;font-size:12px;font-weight:700;margin-right:8px;">1</span>
                      Go to the <strong>Forgot Password</strong> page in the app
                    </p>
                    <p style="margin:0 0 8px;font-size:13px;color:#4b5563;">
                      <span style="display:inline-block;width:22px;height:22px;background:#1d4ed8;color:#fff;border-radius:50%;text-align:center;line-height:22px;font-size:12px;font-weight:700;margin-right:8px;">2</span>
                      Enter your email address
                    </p>
                    <p style="margin:0 0 8px;font-size:13px;color:#4b5563;">
                      <span style="display:inline-block;width:22px;height:22px;background:#1d4ed8;color:#fff;border-radius:50%;text-align:center;line-height:22px;font-size:12px;font-weight:700;margin-right:8px;">3</span>
                      Enter the temporary password shown above
                    </p>
                    <p style="margin:0;font-size:13px;color:#4b5563;">
                      <span style="display:inline-block;width:22px;height:22px;background:#1d4ed8;color:#fff;border-radius:50%;text-align:center;line-height:22px;font-size:12px;font-weight:700;margin-right:8px;">4</span>
                      Choose and confirm your new password
                    </p>
                  </td>
                </tr>
              </table>
 
              <p style="margin:0;font-size:13px;color:#9ca3af;line-height:1.6;">
                Didn't request this? You can safely ignore this email —
                your account password has <strong>not</strong> been changed.
              </p>
            </td>
          </tr>
 
          <!-- Footer -->
          <tr>
            <td style="background:#f9fafb;padding:16px 32px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;text-align:center;">
              <p style="margin:0;font-size:11px;color:#9ca3af;">
                Dublin City Disaster Response System &nbsp;·&nbsp;
                Automated message — do not reply to this email
              </p>
            </td>
          </tr>
 
        </table>
      </td>
    </tr>
  </table>
 
</body>
</html>"""