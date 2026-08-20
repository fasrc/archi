from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

from src.interfaces.chat_app import evaluation_console
from src.interfaces.chat_app.evaluation_console import (
    build_authorize_request,
    build_evaluation_service,
    can_view_evaluations,
)
from src.utils.rbac.permission_enum import Permission


def _flask_app():
    # A secret key is required before a test request context accepts session writes.
    app = Flask(__name__)
    app.secret_key = "evaluation-console-test"
    return app


@pytest.mark.parametrize(
    "chat_app_config",
    [
        {},
        {"evaluations": {}},
        {"evaluations": None},
        {"evaluations": {"enabled": False}},
        {"evaluations": {"enabled": 1}},
        {"evaluations": {"enabled": "true"}},
    ],
)
def test_evaluation_service_requires_strictly_true_enablement(chat_app_config):
    assert build_evaluation_service(chat_app_config) is None


def test_evaluation_service_uses_deployment_defaults():
    # The defaults are container paths, so the constructor is recorded rather
    # than run: a real build would mkdir /root/archi/evaluations on the host.
    with patch.object(evaluation_console, "EvaluationConsoleService") as factory:
        service = build_evaluation_service({"evaluations": {"enabled": True}})

    assert service is factory.return_value
    assert factory.call_args.args == (Path("/root/archi/evaluations"),)
    assert factory.call_args.kwargs == {
        "agent_config_path": Path("/root/archi/configs/config.yaml"),
        "agents_dir": Path("/root/archi/agents"),
        "mcp_config_path": None,
    }


def test_evaluation_service_honours_configured_paths(tmp_path):
    service = build_evaluation_service(
        {
            "agents_dir": str(tmp_path / "agents"),
            "evaluations": {
                "enabled": True,
                "root": str(tmp_path / "evaluations"),
                "agent_config_path": str(tmp_path / "configs" / "config.yaml"),
                "mcp_config_path": str(tmp_path / "qa_evaluation_mcp.yaml"),
            },
        }
    )

    assert service is not None
    assert service.catalog.root == tmp_path / "evaluations"
    assert service.agent_config_path == tmp_path / "configs" / "config.yaml"
    assert service.agents_dir == tmp_path / "agents"
    assert service.mcp_config_path == tmp_path / "qa_evaluation_mcp.yaml"


def test_evaluation_service_creates_the_catalog_tree(tmp_path):
    root = tmp_path / "evaluations"

    build_evaluation_service({"evaluations": {"enabled": True, "root": str(root)}})

    assert (root / "datasets").is_dir()
    assert (root / "runs").is_dir()
    assert (root / "jobs").is_dir()


def test_authorize_request_allows_every_permission_when_auth_is_off():
    authorize_request = build_authorize_request(False)

    with _flask_app().test_request_context("/api/evaluations/catalog"):
        assert authorize_request(Permission.Evaluations.VIEW) is None
        assert authorize_request(Permission.Evaluations.MANAGE) is None


def test_authorize_request_rejects_anonymous_callers_with_401():
    authorize_request = build_authorize_request(True)

    with _flask_app().test_request_context("/api/evaluations/catalog"):
        response, status = authorize_request(Permission.Evaluations.VIEW)

    assert status == 401
    assert response.get_json()["error"] == "Unauthorized"


def test_authorize_request_rejects_missing_permission_with_403():
    authorize_request = build_authorize_request(True)

    with _flask_app().test_request_context("/api/evaluations/catalog") as ctx:
        ctx.session["logged_in"] = True
        ctx.session["roles"] = ["viewer"]
        with patch.object(
            evaluation_console, "has_permission", return_value=False
        ) as has_permission:
            response, status = authorize_request(Permission.Evaluations.RUN)

    assert status == 403
    assert response.get_json()["error"] == "Forbidden"
    assert response.get_json()["required_permission"] == Permission.Evaluations.RUN
    has_permission.assert_called_once_with(Permission.Evaluations.RUN, ["viewer"])


def test_authorize_request_allows_a_permitted_session():
    authorize_request = build_authorize_request(True)

    with _flask_app().test_request_context("/api/evaluations/catalog") as ctx:
        ctx.session["logged_in"] = True
        ctx.session["roles"] = ["admin"]
        with patch.object(evaluation_console, "has_permission", return_value=True):
            assert authorize_request(Permission.Evaluations.MANAGE) is None


@pytest.mark.parametrize(
    ("evaluations_enabled", "auth_enabled", "has_view_permission", "expected_visible"),
    [
        (False, False, True, False),
        (False, True, True, False),
        (True, False, False, True),
        (True, True, False, False),
        (True, True, True, True),
    ],
)
def test_navigation_visibility_matches_route_access(
    evaluations_enabled, auth_enabled, has_view_permission, expected_visible
):
    with _flask_app().test_request_context():
        with patch.object(
            evaluation_console, "has_permission", return_value=has_view_permission
        ) as has_permission:
            visible = can_view_evaluations(evaluations_enabled, auth_enabled)

    assert visible is expected_visible
    assert has_permission.call_count == int(evaluations_enabled and auth_enabled)
