"""Tests for RBAC audit logging — log_authentication_event, log_permission_check, log_role_assignment."""

from unittest.mock import patch

import pytest
from src.utils.rbac.audit import (
    log_authentication_event,
    log_permission_check,
    log_role_assignment,
)


@pytest.fixture(autouse=True)
def mock_audit_logger():
    """Patch the audit_logger used by all three functions."""
    with patch("src.utils.rbac.audit.audit_logger") as mock_logger:
        yield mock_logger


# ---------------------------------------------------------------------------
# 3.6  log_authentication_event: success / failure
# ---------------------------------------------------------------------------


class TestLogAuthenticationEvent:
    def test_success_logs_info(self, mock_audit_logger):
        log_authentication_event("user@test.com", "login", success=True, method="sso")

        # Should log at info level for success
        info_calls = [
            str(c) for c in mock_audit_logger.info.call_args_list
        ]
        assert any("AUTH" in c and "login" in c and "user@test.com" in c and "SUCCESS" in c for c in info_calls)

    def test_failure_logs_warning(self, mock_audit_logger):
        log_authentication_event(
            "unknown", "api_token_auth", success=False,
            method="bearer_token", details="No token",
        )

        # Should log at warning level for failure
        warning_calls = [
            str(c) for c in mock_audit_logger.warning.call_args_list
        ]
        assert any("FAILURE" in c for c in warning_calls)

    def test_success_message_format(self, mock_audit_logger):
        log_authentication_event("user@test.com", "login", success=True, method="sso")

        info_calls = [
            str(c) for c in mock_audit_logger.info.call_args_list
        ]
        assert any("method: sso" in c for c in info_calls)


# ---------------------------------------------------------------------------
# 3.7  log_permission_check: granted (debug) / denied (warning + JSON)
# ---------------------------------------------------------------------------


class TestLogPermissionCheck:
    def test_granted_logs_debug(self, mock_audit_logger):
        log_permission_check(
            user="user@test.com",
            permission="chat:query",
            granted=True,
            endpoint="/chat",
            roles=["base-user"],
        )

        mock_audit_logger.debug.assert_called()
        debug_msg = str(mock_audit_logger.debug.call_args_list[0])
        assert "GRANTED" in debug_msg

    def test_denied_logs_warning(self, mock_audit_logger):
        log_permission_check(
            user="user@test.com",
            permission="config:modify",
            granted=False,
            endpoint="/config",
            roles=["base-user"],
            missing=["config:modify"],
        )

        mock_audit_logger.warning.assert_called()
        warning_msg = str(mock_audit_logger.warning.call_args_list[0])
        assert "DENIED" in warning_msg

    def test_denied_includes_structured_json_with_missing(self, mock_audit_logger):
        log_permission_check(
            user="user@test.com",
            permission="config:modify",
            granted=False,
            endpoint="/config",
            roles=["base-user"],
            missing=["config:modify"],
        )

        # The structured JSON is logged at info level
        info_calls = [
            str(c) for c in mock_audit_logger.info.call_args_list
        ]
        # Find the AUDIT JSON entry
        audit_json_calls = [c for c in info_calls if "AUDIT" in c]
        assert len(audit_json_calls) > 0
        # Verify missing_permissions is in the JSON
        assert any("missing_permissions" in c for c in audit_json_calls)


# ---------------------------------------------------------------------------
# 3.8  log_role_assignment: jwt (info) / default (warning)
# ---------------------------------------------------------------------------


class TestLogRoleAssignment:
    def test_jwt_assignment_logs_info(self, mock_audit_logger):
        log_role_assignment(
            user="user@test.com",
            roles=["admin"],
            source="jwt",
            is_default=False,
        )

        mock_audit_logger.info.assert_called()
        info_msg = str(mock_audit_logger.info.call_args_list[0])
        assert "jwt" in info_msg

    def test_default_assignment_logs_warning(self, mock_audit_logger):
        log_role_assignment(
            user="user@test.com",
            roles=["base-user"],
            source="default",
            is_default=True,
        )

        mock_audit_logger.warning.assert_called()
        warning_msg = str(mock_audit_logger.warning.call_args_list[0])
        assert "Default role" in warning_msg or "default" in warning_msg.lower()
