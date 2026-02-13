"""
Unit tests for the SessionManager module.
"""
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.session_manager import (
    SessionManager,
    SESSION_EXPIRY_HOURS,
    VERIFICATION_CODE_EXPIRY_MINUTES,
    VERIFICATION_CODE_LENGTH,
)


@pytest.fixture
def sm():
    """Fresh SessionManager for each test."""
    return SessionManager()


# ── generate_code ──────────────────────────────────────────────────────────

class TestGenerateCode:
    def test_returns_string_of_correct_length(self, sm):
        code = sm.generate_code()
        assert isinstance(code, str)
        assert len(code) == VERIFICATION_CODE_LENGTH

    def test_code_is_all_digits(self, sm):
        code = sm.generate_code()
        assert code.isdigit()

    def test_codes_are_not_always_identical(self, sm):
        codes = {sm.generate_code() for _ in range(20)}
        assert len(codes) > 1


# ── send_verification ──────────────────────────────────────────────────────

class TestSendVerification:
    def test_returns_six_digit_code(self, sm):
        code = sm.send_verification("user@example.com")
        assert len(code) == 6 and code.isdigit()

    def test_stores_pending_code(self, sm):
        sm.send_verification("user@example.com")
        assert "user@example.com" in sm._pending_codes

    def test_new_code_replaces_old(self, sm):
        code1 = sm.send_verification("user@example.com")
        code2 = sm.send_verification("user@example.com")
        assert sm._pending_codes["user@example.com"]["code"] == code2


# ── verify_code ────────────────────────────────────────────────────────────

class TestVerifyCode:
    def test_correct_code_returns_session_token(self, sm):
        code = sm.send_verification("user@example.com")
        success, token = sm.verify_code("user@example.com", code)
        assert success is True
        assert isinstance(token, str) and len(token) > 0

    def test_incorrect_code_rejected(self, sm):
        sm.send_verification("user@example.com")
        success, msg = sm.verify_code("user@example.com", "000000")
        assert success is False
        assert "Invalid" in msg

    def test_no_pending_code_rejected(self, sm):
        success, msg = sm.verify_code("nobody@example.com", "123456")
        assert success is False
        assert "No verification" in msg

    def test_expired_code_rejected(self, sm):
        code = sm.send_verification("user@example.com")
        # Manually expire the code
        sm._pending_codes["user@example.com"]["expires_at"] = datetime.utcnow() - timedelta(seconds=1)
        success, msg = sm.verify_code("user@example.com", code)
        assert success is False
        assert "expired" in msg.lower()

    def test_code_consumed_after_success(self, sm):
        code = sm.send_verification("user@example.com")
        sm.verify_code("user@example.com", code)
        # Second attempt with same code should fail
        success, msg = sm.verify_code("user@example.com", code)
        assert success is False


# ── validate_session ───────────────────────────────────────────────────────

class TestValidateSession:
    def test_valid_session(self, sm):
        code = sm.send_verification("user@example.com")
        _, token = sm.verify_code("user@example.com", code)
        assert sm.validate_session(token) is True

    def test_invalid_token(self, sm):
        assert sm.validate_session("bogus-token") is False

    def test_expired_session(self, sm):
        code = sm.send_verification("user@example.com")
        _, token = sm.verify_code("user@example.com", code)
        # Manually expire the session
        sm._sessions[token]["expires_at"] = datetime.utcnow() - timedelta(seconds=1)
        assert sm.validate_session(token) is False


# ── get_session_email ──────────────────────────────────────────────────────

class TestGetSessionEmail:
    def test_returns_email_for_valid_session(self, sm):
        code = sm.send_verification("user@example.com")
        _, token = sm.verify_code("user@example.com", code)
        assert sm.get_session_email(token) == "user@example.com"

    def test_returns_none_for_invalid_session(self, sm):
        assert sm.get_session_email("bogus") is None

    def test_returns_none_for_expired_session(self, sm):
        code = sm.send_verification("user@example.com")
        _, token = sm.verify_code("user@example.com", code)
        sm._sessions[token]["expires_at"] = datetime.utcnow() - timedelta(seconds=1)
        assert sm.get_session_email(token) is None


# ── cleanup_expired ────────────────────────────────────────────────────────

class TestCleanupExpired:
    def test_removes_expired_sessions(self, sm):
        code = sm.send_verification("user@example.com")
        _, token = sm.verify_code("user@example.com", code)
        sm._sessions[token]["expires_at"] = datetime.utcnow() - timedelta(seconds=1)
        result = sm.cleanup_expired()
        assert result["sessions_removed"] == 1
        assert token not in sm._sessions

    def test_removes_expired_codes(self, sm):
        sm.send_verification("user@example.com")
        sm._pending_codes["user@example.com"]["expires_at"] = datetime.utcnow() - timedelta(seconds=1)
        result = sm.cleanup_expired()
        assert result["codes_removed"] == 1
        assert "user@example.com" not in sm._pending_codes

    def test_keeps_valid_sessions(self, sm):
        code = sm.send_verification("user@example.com")
        _, token = sm.verify_code("user@example.com", code)
        result = sm.cleanup_expired()
        assert result["sessions_removed"] == 0
        assert token in sm._sessions


# ── get_networks_for_email ─────────────────────────────────────────────────

class TestGetNetworksForEmail:
    def test_filters_by_email(self):
        config = {
            "networks": [
                {"id": "1", "name": "Office A", "email": "alice@example.com"},
                {"id": "2", "name": "Office B", "email": "bob@example.com"},
                {"id": "3", "name": "Office C", "email": "alice@example.com"},
            ]
        }
        result = SessionManager.get_networks_for_email("alice@example.com", config)
        assert len(result) == 2
        assert all(n["email"] == "alice@example.com" for n in result)

    def test_returns_empty_for_unknown_email(self):
        config = {
            "networks": [
                {"id": "1", "name": "Office A", "email": "alice@example.com"},
            ]
        }
        result = SessionManager.get_networks_for_email("nobody@example.com", config)
        assert result == []

    def test_handles_empty_config(self):
        result = SessionManager.get_networks_for_email("alice@example.com", {})
        assert result == []

    def test_handles_missing_email_field(self):
        config = {
            "networks": [
                {"id": "1", "name": "Office A"},
            ]
        }
        result = SessionManager.get_networks_for_email("alice@example.com", config)
        assert result == []
