"""Tests for RBACRegistry — permission resolution, config validation, filtering, and properties."""

import pytest
from src.utils.rbac.registry import RBACRegistry, RBACConfigError


# ---------------------------------------------------------------------------
# Fixtures: inline config dicts
# ---------------------------------------------------------------------------

def _make_config(
    roles,
    default_role="base-user",
    app_name="test-app",
    allow_anonymous=False,
    pass_descriptions_to_agent=False,
):
    """Build a minimal valid config dict for RBACRegistry."""
    return {
        "app_name": app_name,
        "default_role": default_role,
        "sso": {"allow_anonymous": allow_anonymous},
        "pass_descriptions_to_agent": pass_descriptions_to_agent,
        "roles": roles,
        "permissions": {},
    }


BASIC_ROLES = {
    "base-user": {
        "description": "Basic authenticated user",
        "permissions": ["chat:query", "chat:history"],
    },
    "power-user": {
        "description": "Power user with uploads",
        "inherits": ["base-user"],
        "permissions": ["upload:documents"],
    },
    "admin": {
        "description": "Full access",
        "inherits": ["power-user"],
        "permissions": ["*"],
    },
}


@pytest.fixture
def registry():
    """Registry built from BASIC_ROLES — covers direct, inherited, and wildcard perms."""
    return RBACRegistry(_make_config(BASIC_ROLES))


# ---------------------------------------------------------------------------
# 2.2  Permission resolution: direct, inherited, multi-level
# ---------------------------------------------------------------------------


class TestPermissionResolution:
    def test_direct_permission_grant(self, registry):
        assert registry.has_permission(["base-user"], "chat:query") is True

    def test_permission_via_inheritance(self, registry):
        """power-user inherits base-user → gets chat:query."""
        assert registry.has_permission(["power-user"], "chat:query") is True

    def test_direct_permission_on_child(self, registry):
        """power-user's own permission."""
        assert registry.has_permission(["power-user"], "upload:documents") is True

    def test_multi_level_inheritance(self, registry):
        """admin inherits power-user inherits base-user → gets chat:query."""
        assert registry.has_permission(["admin"], "chat:query") is True

    # ------------------------------------------------------------------
    # 2.3  Wildcard grants all, permission not granted
    # ------------------------------------------------------------------

    def test_wildcard_grants_any_permission(self, registry):
        assert registry.has_permission(["admin"], "any:permission") is True

    def test_permission_not_granted(self, registry):
        assert registry.has_permission(["base-user"], "upload:documents") is False


# ---------------------------------------------------------------------------
# 2.4  Config validation errors
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def test_circular_inheritance_raises(self):
        roles = {
            "a": {"permissions": ["x"], "inherits": ["b"]},
            "b": {"permissions": ["y"], "inherits": ["a"]},
        }
        with pytest.raises(RBACConfigError, match="[Cc]ircular"):
            RBACRegistry(_make_config(roles, default_role="a"))

    def test_no_roles_raises(self):
        with pytest.raises(RBACConfigError, match="[Nn]o roles"):
            RBACRegistry(_make_config(roles={}))

    def test_undefined_parent_raises(self):
        roles = {
            "child": {"permissions": ["x"], "inherits": ["nonexistent"]},
        }
        with pytest.raises(RBACConfigError, match="undefined role"):
            RBACRegistry(_make_config(roles, default_role="child"))


# ---------------------------------------------------------------------------
# 2.5  filter_valid_roles
# ---------------------------------------------------------------------------


class TestFilterValidRoles:
    def test_mixed_valid_and_invalid(self, registry):
        result = registry.filter_valid_roles(["admin", "unknown-role", "base-user"])
        assert result == ["admin", "base-user"]

    def test_all_invalid(self, registry):
        result = registry.filter_valid_roles(["foo", "bar"])
        assert result == []


# ---------------------------------------------------------------------------
# 2.6  Properties: default_role, allow_anonymous, app_name
# ---------------------------------------------------------------------------


class TestRegistryProperties:
    def test_default_role(self):
        reg = RBACRegistry(_make_config(BASIC_ROLES, default_role="base-user"))
        assert reg.default_role == "base-user"

    def test_default_role_custom(self):
        reg = RBACRegistry(_make_config(BASIC_ROLES, default_role="admin"))
        assert reg.default_role == "admin"

    def test_allow_anonymous_true(self):
        reg = RBACRegistry(_make_config(BASIC_ROLES, allow_anonymous=True))
        assert reg.allow_anonymous is True

    def test_allow_anonymous_false(self):
        reg = RBACRegistry(_make_config(BASIC_ROLES, allow_anonymous=False))
        assert reg.allow_anonymous is False

    def test_app_name_from_config(self):
        reg = RBACRegistry(_make_config(BASIC_ROLES, app_name="my-app"))
        assert reg.app_name == "my-app"

    def test_app_name_override_via_constructor(self):
        reg = RBACRegistry(_make_config(BASIC_ROLES, app_name="config-app"), app_name="override-app")
        assert reg.app_name == "override-app"
