#!/usr/bin/env python3
"""
eero Business Dashboard - Email Notifications
Sends alert emails via SMTP when critical events occur.
"""
import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

logger = logging.getLogger(__name__)

# SMTP configuration from environment variables
SMTP_HOST = os.environ.get('EERO_SMTP_HOST', '')
SMTP_PORT = int(os.environ.get('EERO_SMTP_PORT', '587'))
SMTP_USER = os.environ.get('EERO_SMTP_USER', '')
SMTP_PASS = os.environ.get('EERO_SMTP_PASS', '')
SMTP_FROM = os.environ.get('EERO_SMTP_FROM', 'noreply@eero-dashboard.local')
NOTIFY_ENABLED = os.environ.get('EERO_NOTIFY_ENABLED', 'false').lower() == 'true'


def is_configured() -> bool:
    """Check if SMTP is configured and notifications are enabled."""
    return NOTIFY_ENABLED and bool(SMTP_HOST and SMTP_USER)


def send_alert_email(to_email: str, subject: str, body_html: str) -> bool:
    """
    Send an alert email. Returns True on success, False on failure.
    """
    if not is_configured():
        logger.debug("Email notifications disabled or not configured")
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = SMTP_FROM
        msg['To'] = to_email
        msg.attach(MIMEText(body_html, 'html'))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

        logger.info("Alert email sent to %s: %s", to_email, subject)
        return True
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to_email, e)
        return False


def format_alert_email(alert: dict) -> tuple[str, str]:
    """
    Format an alert dict into (subject, html_body) for email.
    """
    severity = alert.get('severity', 'info').upper()
    message = alert.get('message', 'Unknown alert')
    alert_type = alert.get('alert_type', 'unknown')
    network_id = alert.get('network_id', 'N/A')

    subject = f"[eero Dashboard] {severity}: {alert_type} — Network {network_id}"

    color = '#F44336' if severity == 'CRITICAL' else '#FFC107'
    html = f"""
    <div style="font-family: 'Segoe UI', sans-serif; max-width: 500px; margin: 0 auto;">
        <div style="background: #003D5C; color: #fff; padding: 15px 20px; border-radius: 8px 8px 0 0;">
            <h2 style="margin: 0; font-size: 18px;">eero Business Dashboard Alert</h2>
        </div>
        <div style="background: #f8f9fa; padding: 20px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 8px 8px;">
            <div style="background: {color}20; border-left: 4px solid {color}; padding: 12px; border-radius: 4px; margin-bottom: 15px;">
                <strong style="color: {color};">{severity}</strong>
                <p style="margin: 5px 0 0; color: #333;">{message}</p>
            </div>
            <p style="color: #666; font-size: 13px;">Network ID: {network_id}<br>Type: {alert_type}</p>
        </div>
    </div>
    """
    return subject, html


def notify_alert(alert: dict, recipient_email: Optional[str] = None) -> bool:
    """
    Send notification for an alert. Uses recipient_email or falls back to SMTP_USER.
    """
    if not is_configured():
        return False

    to = recipient_email or SMTP_USER
    subject, html = format_alert_email(alert)
    return send_alert_email(to, subject, html)
