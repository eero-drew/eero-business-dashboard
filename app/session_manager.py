"""
Session management module for eero Business Dashboard.

Handles email-based authentication with verification codes,
session creation/validation, and data isolation by email.
"""
import os
import secrets
import string
import logging
import json
from datetime import datetime, timedelta

# Session and verification code expiration
SESSION_EXPIRY_HOURS = 24
VERIFICATION_CODE_EXPIRY_MINUTES = 10
VERIFICATION_CODE_LENGTH = 6


class SessionManager:
    """
    Manages user sessions for the eero Business Dashboard.

    Uses in-memory storage for sessions and pending verification codes.
    Suitable for single-server deployment.
    """

    def __init__(self):
        # {session_token: {"email": str, "created_at": datetime, "expires_at": datetime}}
        self._sessions = {}
        # {email: {"code": str, "created_at": datetime, "expires_at": datetime}}
        self._pending_codes = {}

    def generate_code(self):
        """
        Generate a 6-digit numeric verification code.

        Returns:
            str: A 6-digit string code (e.g. '048271').
        """
        return ''.join(secrets.choice(string.digits) for _ in range(VERIFICATION_CODE_LENGTH))

    def send_verification(self, email):
        """
        Create and store a verification code for the given email.

        In this implementation, the code is stored and returned.
        Actual email delivery is handled by a separate module (future task).

        Args:
            email: The email address to send the verification code to.

        Returns:
            str: The generated verification code.
        """
        code = self.generate_code()
        now = datetime.utcnow()
        self._pending_codes[email] = {
            'code': code,
            'created_at': now,
            'expires_at': now + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES),
        }
        logging.info("Verification code generated for %s", email)
        return code

    def verify_code(self, email, code):
        """
        Validate a verification code and create a session on success.

        Args:
            email: The email address that requested the code.
            code: The verification code to validate.

        Returns:
            tuple: (success: bool, result: str)
                On success, result is the session token.
                On failure, result is an error message.
        """
        pending = self._pending_codes.get(email)
        if not pending:
            return False, 'No verification code pending for this email'

        now = datetime.utcnow()
        if now > pending['expires_at']:
            del self._pending_codes[email]
            return False, 'Verification code has expired'

        if pending['code'] != code:
            return False, 'Invalid verification code'

        # Code is valid — remove it and create a session
        del self._pending_codes[email]
        session_token = secrets.token_urlsafe(32)
        self._sessions[session_token] = {
            'email': email,
            'created_at': now,
            'expires_at': now + timedelta(hours=SESSION_EXPIRY_HOURS),
        }
        logging.info("Session created for %s", email)
        return True, session_token

    def validate_session(self, session_token):
        """
        Check whether a session token is valid and not expired.

        Args:
            session_token: The token to validate.

        Returns:
            bool: True if the session is valid, False otherwise.
        """
        session = self._sessions.get(session_token)
        if not session:
            return False
        if datetime.utcnow() > session['expires_at']:
            del self._sessions[session_token]
            return False
        return True

    def get_session_email(self, session_token):
        """
        Return the email address associated with a valid session.

        Args:
            session_token: The session token to look up.

        Returns:
            str or None: The email if the session is valid, None otherwise.
        """
        if not self.validate_session(session_token):
            return None
        return self._sessions[session_token]['email']

    def cleanup_expired(self):
        """
        Remove all expired sessions and pending verification codes.

        Returns:
            dict: Counts of removed items, e.g.
                  {"sessions_removed": 2, "codes_removed": 1}
        """
        now = datetime.utcnow()

        expired_sessions = [
            token for token, data in self._sessions.items()
            if now > data['expires_at']
        ]
        for token in expired_sessions:
            del self._sessions[token]

        expired_codes = [
            email for email, data in self._pending_codes.items()
            if now > data['expires_at']
        ]
        for email in expired_codes:
            del self._pending_codes[email]

        removed = {
            'sessions_removed': len(expired_sessions),
            'codes_removed': len(expired_codes),
        }
        if expired_sessions or expired_codes:
            logging.info(
                "Cleanup: removed %d sessions, %d codes",
                removed['sessions_removed'],
                removed['codes_removed'],
            )
        return removed

    @staticmethod
    def get_networks_for_email(email, config):
        """
        Filter networks from config that belong to the given email.

        This enforces data isolation — each business owner only sees
        networks associated with their email address.

        Args:
            email: The authenticated user's email.
            config: The application config dict (with a 'networks' key).

        Returns:
            list: Network dicts whose 'email' field matches.
        """
        networks = config.get('networks', [])
        return [n for n in networks if n.get('email') == email]
