"""
services.chat_app.openai_compat.local_only: /v1 answers only loopback callers.

The Slack service reaches the chat app on localhost (host mode), so a deployment can
turn on /v1 for it without opening an unauthenticated model API to the network
(openspec/changes/v1-local-only). The gate runs before auth and before validation, so
a remote caller learns nothing about the API.
"""

from unittest.mock import MagicMock, patch

import pytest

flask = pytest.importorskip("flask", reason="Flask not installed")
Flask = flask.Flask

import src.interfaces.chat_app.openai_compat as compat
from src.interfaces.chat_app.openai_compat import register_openai_compat

REMOTE = "10.31.4.7"


def _app(local_only, auth_enabled=False):
    app = Flask(__name__)
    app.config["TESTING"] = True
    chat_wrapper = MagicMock()
    chat_wrapper.pg_config = {"host": "localhost", "dbname": "test"}
    register_openai_compat(
        app,
        chat_wrapper,
        user_service=MagicMock(),
        auth_enabled=auth_enabled,
        local_only=local_only,
    )
    return app


@pytest.fixture(autouse=True)
def _reset_and_patch():
    registry = MagicMock()
    registry.default_role = "base-user"
    registry.allow_anonymous = False
    with (
        patch(
            "src.utils.config_access.get_full_config",
            return_value={"name": "test-model"},
        ),
        patch(
            "src.interfaces.chat_app.openai_compat.get_registry",
            return_value=registry,
        ),
        patch(
            "src.interfaces.chat_app.openai_compat.has_permission", return_value=True
        ),
    ):
        yield
    compat._chat_wrapper = None
    compat._user_service = None
    compat._auth_enabled = False
    compat._token_ttl_days = 90
    compat._local_only = False


def _get(app, addr, headers=None):
    return app.test_client().get(
        "/v1/models", environ_base={"REMOTE_ADDR": addr}, headers=headers or {}
    )


class TestLocalOnlyOff:
    def test_default_answers_a_remote_caller(self):
        app = Flask(__name__)
        register_openai_compat(app, MagicMock(), auth_enabled=False)
        resp = _get(app, REMOTE)
        assert resp.status_code == 200

    def test_false_answers_a_remote_caller(self):
        assert _get(_app(local_only=False), REMOTE).status_code == 200


class TestLocalOnlyOn:
    @pytest.mark.parametrize("addr", ["127.0.0.1", "127.0.0.53", "::1"])
    def test_loopback_caller_is_answered(self, addr):
        assert _get(_app(local_only=True), addr).status_code == 200

    @pytest.mark.parametrize(
        "addr", [REMOTE, "192.168.1.20", "fe80::1", "::ffff:10.0.0.1", "", "garbage"]
    )
    def test_non_loopback_caller_gets_403(self, addr):
        resp = _get(_app(local_only=True), addr)
        assert resp.status_code == 403
        body = resp.get_json()
        assert body["error"]["type"] == "permission_error"
        assert "local" in body["error"]["message"].lower()

    @pytest.mark.parametrize(
        "header",
        [
            {"X-Forwarded-For": "203.0.113.9"},
            {"Forwarded": "for=203.0.113.9"},
            {"X-Real-IP": "203.0.113.9"},
            {"X-Forwarded-Host": "archi.example.org"},
        ],
    )
    def test_a_proxied_request_is_refused_even_from_loopback(self, header):
        # A reverse proxy on the same host connects from 127.0.0.1; its forwarding
        # headers are the only sign that the real caller is remote.
        resp = _get(_app(local_only=True), "127.0.0.1", headers=header)
        assert resp.status_code == 403

    def test_refused_before_validation(self):
        # An empty body would be a 400 from the validator; a remote caller must not
        # reach it.
        app = _app(local_only=True)
        resp = app.test_client().post(
            "/v1/chat/completions", environ_base={"REMOTE_ADDR": REMOTE}, json={}
        )
        assert resp.status_code == 403

    def test_refused_before_auth(self):
        # With auth on and no token, a remote caller gets 403 (not 401): the gate
        # answers first.
        resp = _get(_app(local_only=True, auth_enabled=True), REMOTE)
        assert resp.status_code == 403

    def test_loopback_still_needs_auth_when_auth_is_on(self):
        resp = _get(_app(local_only=True, auth_enabled=True), "127.0.0.1")
        assert resp.status_code == 401

    def test_does_not_gate_other_routes(self):
        app = _app(local_only=True)

        @app.route("/api/health")
        def health():
            return "ok"

        resp = app.test_client().get(
            "/api/health", environ_base={"REMOTE_ADDR": REMOTE}
        )
        assert resp.status_code == 200


class TestOptionsFromConfig:
    """app.py hands services.chat_app.openai_compat to openai_compat_options()."""

    def test_local_only_true_is_passed(self):
        from src.interfaces.chat_app.openai_compat import openai_compat_options

        opts = openai_compat_options({"enabled": True, "local_only": True})
        assert opts["local_only"] is True

    @pytest.mark.parametrize("cfg", [{}, {"local_only": False}, {"local_only": None}])
    def test_local_only_defaults_off(self, cfg):
        from src.interfaces.chat_app.openai_compat import openai_compat_options

        assert openai_compat_options(cfg)["local_only"] is False

    def test_token_ttl_days_kept(self):
        from src.interfaces.chat_app.openai_compat import openai_compat_options

        assert openai_compat_options({})["token_ttl_days"] == 90
        assert openai_compat_options({"token_ttl_days": 7})["token_ttl_days"] == 7

    def test_options_register_cleanly(self):
        from src.interfaces.chat_app.openai_compat import openai_compat_options

        app = Flask(__name__)
        register_openai_compat(
            app, MagicMock(), **openai_compat_options({"local_only": True})
        )
        assert _get(app, REMOTE).status_code == 403

    def test_app_py_uses_the_options(self):
        # app.py is too heavy to construct here; pin that it passes the options
        # through instead of reading keys itself (a missing local_only= there would
        # silently leave /v1 open).
        from pathlib import Path

        src = Path("src/interfaces/chat_app/app.py").read_text()
        assert "**openai_compat_options(openai_compat_config)" in src


class TestTemplate:
    """base-config.yaml carries local_only into the stored config."""

    def _render(self, **kwargs):
        import yaml
        from jinja2 import (
            ChainableUndefined,
            Environment,
            PackageLoader,
            select_autoescape,
        )

        env = Environment(
            loader=PackageLoader("src.cli"),
            autoescape=select_autoescape(),
            undefined=ChainableUndefined,
        )
        rendered = env.get_template("base-config.yaml").render(verbosity=0, **kwargs)
        return yaml.safe_load(rendered)["services"]["chat_app"]["openai_compat"]

    def test_true_renders_true(self):
        cfg = self._render(
            services={
                "chat_app": {"openai_compat": {"enabled": True, "local_only": True}}
            }
        )
        assert cfg["local_only"] is True
        assert cfg["enabled"] is True

    @pytest.mark.parametrize("value", [False, None])
    def test_false_or_null_renders_false(self, value):
        cfg = self._render(
            services={"chat_app": {"openai_compat": {"local_only": value}}}
        )
        assert cfg["local_only"] is False

    def test_absent_renders_false(self):
        assert self._render()["local_only"] is False


class TestRegistrationResets:
    def test_a_later_registration_without_local_only_turns_it_off(self):
        _app(local_only=True)
        assert _get(_app(local_only=False), REMOTE).status_code == 200
