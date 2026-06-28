"""Tests for RBAC decorators — require_permission, require_any_permission, check_sso_required."""

from unittest.mock import patch

import pytest
from flask import Flask

from src.utils.rbac.decorators import (
    check_sso_required,
    require_any_permission,
    require_permission,
)
from src.utils.rbac.registry import RBACRegistry

# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

ROLES = {
    "base-user": {
        "description": "Basic",
        "permissions": ["chat:query"],
    },
    "power-user": {
        "description": "Power user",
        "inherits": ["base-user"],
        "permissions": ["config:view", "config:modify"],
    },
    "admin": {
        "description": "Full access",
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

ANON_CONFIG = {
    "app_name": "test-app",
    "default_role": "base-user",
    "sso": {"allow_anonymous": True},
    "roles": ROLES,
    "permissions": {},
}


# ---------------------------------------------------------------------------
# Flask test app + client
# ---------------------------------------------------------------------------


def _create_app(registry):
    """Build a minimal Flask app with decorated test routes."""
    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.route("/login")
    def login():
        return "login page", 200

    @app.route("/need-perm")
    @require_permission("config:view")
    def need_perm():
        return "ok", 200

    @app.route("/need-any")
    @require_any_permission(["config:view", "config:modify"])
    def need_any():
        return "ok", 200

    @app.route("/sso-gate")
    @check_sso_required()
    def sso_gate():
        return "ok", 200

    return app


@pytest.fixture
def _registry():
    return RBACRegistry(CONFIG)


@pytest.fixture
def client(_registry):
    app = _create_app(_registry)
    with patch("src.utils.rbac.decorators.get_registry", return_value=_registry):
        with app.test_client() as c:
            yield c


# ---------------------------------------------------------------------------
# 4.5  require_permission
# ---------------------------------------------------------------------------


class TestRequirePermission:
    def test_authenticated_with_permission(self, client):
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["roles"] = ["power-user"]
            sess["user"] = {"email": "user@test.com"}

        resp = client.get("/need-perm", headers={"Content-Type": "application/json"})
        assert resp.status_code == 200

    def test_authenticated_without_permission(self, client):
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["roles"] = ["base-user"]
            sess["user"] = {"email": "user@test.com"}

        resp = client.get("/need-perm", headers={"Content-Type": "application/json"})
        assert resp.status_code == 403
        data = resp.get_json()
        assert "required_permissions" in data

    def test_unauthenticated_api_request(self, client):
        resp = client.get("/need-perm", headers={"Content-Type": "application/json"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 4.6  require_any_permission
# ---------------------------------------------------------------------------


class TestRequireAnyPermission:
    def test_has_one_of_listed(self, client):
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["roles"] = ["power-user"]
            sess["user"] = {"email": "user@test.com"}

        resp = client.get("/need-any", headers={"Content-Type": "application/json"})
        assert resp.status_code == 200

    def test_has_none_of_listed(self, client):
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["roles"] = ["base-user"]
            sess["user"] = {"email": "user@test.com"}

        resp = client.get("/need-any", headers={"Content-Type": "application/json"})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 4.7  check_sso_required
# ---------------------------------------------------------------------------


class TestCheckSsoRequired:
    def test_anonymous_allowed(self):
        """When allow_anonymous is True, unauthenticated requests pass through."""
        anon_registry = RBACRegistry(ANON_CONFIG)
        app = _create_app(anon_registry)
        with patch(
            "src.utils.rbac.decorators.get_registry", return_value=anon_registry
        ):
            with app.test_client() as c:
                resp = c.get("/sso-gate")
                assert resp.status_code == 200

    def test_anonymous_not_allowed(self, client):
        """When allow_anonymous is False and no session, JSON request → 401."""
        resp = client.get("/sso-gate", headers={"Content-Type": "application/json"})
        assert resp.status_code == 401
