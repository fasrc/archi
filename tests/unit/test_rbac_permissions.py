"""Tests for RBAC permission utilities — has_permission, is_admin, is_expert with explicit roles."""

from unittest.mock import patch

import pytest

from src.utils.rbac.permissions import has_permission, is_admin, is_expert
from src.utils.rbac.registry import RBACRegistry

# ---------------------------------------------------------------------------
# Shared config + mock registry
# ---------------------------------------------------------------------------

ROLES = {
    "base-user": {
        "description": "Basic user",
        "permissions": ["chat:query", "chat:history"],
    },
    "power-user": {
        "description": "Power user",
        "inherits": ["base-user"],
        "permissions": ["upload:documents", "config:modify"],
    },
    "admin": {
        "description": "Full access",
        "inherits": ["power-user"],
        "permissions": ["*"],
    },
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
    """Patch get_registry in the permissions module to return a test registry."""
    registry = RBACRegistry(CONFIG)
    with patch("src.utils.rbac.permissions.get_registry", return_value=registry):
        yield registry


# ---------------------------------------------------------------------------
# 3.2  has_permission: granted / denied / empty roles
# ---------------------------------------------------------------------------


class TestHasPermission:
    def test_granted(self):
        assert has_permission("chat:query", roles=["base-user"]) is True

    def test_denied(self):
        assert has_permission("config:modify", roles=["base-user"]) is False

    def test_empty_roles(self):
        assert has_permission("chat:query", roles=[]) is False


# ---------------------------------------------------------------------------
# 3.3  is_admin: wildcard role / non-admin
# ---------------------------------------------------------------------------


class TestIsAdmin:
    def test_admin_with_wildcard(self):
        assert is_admin(roles=["admin"]) is True

    def test_non_admin(self):
        assert is_admin(roles=["base-user"]) is False


# ---------------------------------------------------------------------------
# 3.4  is_expert: via config:modify / via admin / non-expert
# ---------------------------------------------------------------------------


class TestIsExpert:
    def test_expert_via_config_modify(self):
        assert is_expert(roles=["power-user"]) is True

    def test_expert_via_admin(self):
        assert is_expert(roles=["admin"]) is True

    def test_non_expert(self):
        assert is_expert(roles=["base-user"]) is False
