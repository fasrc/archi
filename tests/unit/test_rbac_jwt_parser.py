"""Tests for JWT role extraction — extract_roles_from_token and get_user_roles."""

from unittest.mock import patch

import jwt as pyjwt
import pytest
from src.utils.rbac.registry import RBACRegistry
from src.utils.rbac.jwt_parser import extract_roles_from_token, get_user_roles


# ---------------------------------------------------------------------------
# Shared config + registry
# ---------------------------------------------------------------------------

ROLES = {
    "admin": {"description": "Full access", "permissions": ["*"]},
    "base-user": {"description": "Basic", "permissions": ["chat:query"]},
}

CONFIG = {
    "app_name": "test-app",
    "default_role": "base-user",
    "sso": {"allow_anonymous": False},
    "roles": ROLES,
    "permissions": {},
}


@pytest.fixture(autouse=True)
def _mock_registry():
    registry = RBACRegistry(CONFIG)
    with patch("src.utils.rbac.jwt_parser.get_registry", return_value=registry):
        yield registry


# ---------------------------------------------------------------------------
# 4.2  extract_roles_from_token
# ---------------------------------------------------------------------------


class TestExtractRolesFromToken:
    def test_roles_in_resource_access(self):
        token = {
            "resource_access": {
                "test-app": {"roles": ["admin", "base-user"]}
            }
        }
        assert extract_roles_from_token(token) == ["admin", "base-user"]

    def test_no_resource_access(self):
        token = {"sub": "user@test.com"}
        assert extract_roles_from_token(token) == []

    def test_wrong_app_name(self):
        token = {
            "resource_access": {
                "other-app": {"roles": ["admin"]}
            }
        }
        assert extract_roles_from_token(token) == []

    def test_id_token_fallback(self):
        """When access_token has no resource_access, roles come from id_token."""
        access_payload = {"sub": "user@test.com"}
        id_payload = {
            "sub": "user@test.com",
            "resource_access": {
                "test-app": {"roles": ["admin"]}
            },
        }
        token = {
            "access_token": pyjwt.encode(access_payload, "secret", algorithm="HS256"),
            "id_token": pyjwt.encode(id_payload, "secret", algorithm="HS256"),
        }
        assert extract_roles_from_token(token) == ["admin"]


# ---------------------------------------------------------------------------
# 4.3  get_user_roles
# ---------------------------------------------------------------------------


class TestGetUserRoles:
    @patch("src.utils.rbac.jwt_parser.log_role_assignment")
    def test_valid_roles_returned(self, _mock_log):
        token = {
            "resource_access": {
                "test-app": {"roles": ["admin", "base-user"]}
            }
        }
        result = get_user_roles(token, "user@test.com")
        assert result == ["admin", "base-user"]

    @patch("src.utils.rbac.jwt_parser.log_role_assignment")
    def test_no_valid_roles_returns_default(self, _mock_log):
        token = {
            "resource_access": {
                "test-app": {"roles": ["unknown-role"]}
            }
        }
        result = get_user_roles(token, "user@test.com")
        assert result == ["base-user"]
