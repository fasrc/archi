import logging
import os
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
    # agent_config_path has no default — it is required, and named here.
    with patch.object(evaluation_console, "EvaluationConsoleService") as factory:
        service = build_evaluation_service(
            {
                "evaluations": {
                    "enabled": True,
                    "agent_config_path": "/root/archi/configs/evaluation.yaml",
                }
            }
        )

    assert service is factory.return_value
    assert factory.call_args.args == (Path("/root/archi/evaluations"),)
    assert factory.call_args.kwargs == {
        "agent_config_path": Path("/root/archi/configs/evaluation.yaml"),
        "agents_dir": Path("/root/archi/agents"),
        "mcp_config_path": None,
    }


@pytest.mark.parametrize(
    "evaluations_config",
    [
        {"enabled": True},
        {"enabled": True, "agent_config_path": None},
        {"enabled": True, "agent_config_path": ""},
        {"enabled": True, "agent_config_path": "   "},
        {"enabled": True, "agent_config_path": 7},
    ],
)
def test_evaluation_service_requires_a_named_agent_config(evaluations_config, caplog):
    """An enabled console without an explicit config path stays off.

    Each run copies the named file into its own run directory, so there is no
    safe default: the deployment must name a redacted copy.
    """
    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service({"evaluations": evaluations_config})

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "evaluations.agent_config_path is required" in caplog.text


@pytest.mark.parametrize(
    "configured_path",
    [
        "/root/archi/configs/config.yaml",
        "/root/archi/configs//config.yaml",
        "/root/archi/configs/./config.yaml",
        "/root/archi/configs/../configs/config.yaml",
        "/root/archi/../archi/configs/config.yaml",
    ],
)
def test_evaluation_service_refuses_the_live_deployment_config(configured_path, caplog):
    """The live config is the one path the console must never snapshot.

    Every spelling of it counts: a ``..`` segment names the same file, so the
    guard compares canonical targets, not the text it was handed.
    """
    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service(
            {"evaluations": {"enabled": True, "agent_config_path": configured_path}}
        )

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "/root/archi/configs/config.yaml" in caplog.text
    assert "live deployment config" in caplog.text


def test_evaluation_service_refuses_a_symlink_to_the_live_deployment_config(
    monkeypatch, tmp_path, caplog
):
    """A symlink pointing at the live config resolves to it, so it is refused.

    The refused location moves into ``tmp_path``: the case needs a real symlink
    and a real target, and the gate cannot write under ``/root``.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    alias = tmp_path / "alias.yaml"
    alias.symlink_to(live)
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service(
            {"evaluations": {"enabled": True, "agent_config_path": str(alias)}}
        )

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "live deployment config" in caplog.text


def test_evaluation_service_refuses_a_hard_link_to_the_live_deployment_config(
    monkeypatch, tmp_path, caplog
):
    """A hard link is a second name for one file, so it is refused as well.

    Path canonicalization cannot see it — both names are already canonical — so
    the guard asks the filesystem for identity instead. A bind mount of the live
    config into the container reads the same way. The refused location moves into
    ``tmp_path``: the case needs two real names for one real file.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    alias = tmp_path / "alias.yaml"
    os.link(live, alias)
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service(
            {"evaluations": {"enabled": True, "agent_config_path": str(alias)}}
        )

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "live deployment config" in caplog.text


def test_evaluation_service_accepts_a_distinct_existing_config(monkeypatch, tmp_path):
    """The identity check must not refuse a real file that is a different file."""
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    redacted = tmp_path / "evaluation.yaml"
    redacted.write_text("redacted: true\n", encoding="utf-8")
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    service = build_evaluation_service(
        {
            "evaluations": {
                "enabled": True,
                "root": str(tmp_path / "evaluations"),
                "agent_config_path": str(redacted),
            }
        }
    )

    assert service is not None
    assert service.agent_config_path == redacted


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

    build_evaluation_service(
        {
            "evaluations": {
                "enabled": True,
                "root": str(root),
                "agent_config_path": str(tmp_path / "evaluation.yaml"),
            }
        }
    )

    assert (root / "datasets").is_dir()
    assert (root / "runs").is_dir()
    assert (root / "jobs").is_dir()


def test_evaluation_service_disables_on_an_unwritable_root(
    monkeypatch, tmp_path, caplog
):
    """A root whose parent is a regular file cannot be mkdir'd, and must not crash chat.

    The live-config refusal is neutralized via ``LIVE_AGENT_CONFIG_PATH`` so this
    test proves the storage guard, not the earlier config guard.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory\n", encoding="utf-8")
    root = blocker / "evaluations"

    agent_config_path = tmp_path / "evaluation.yaml"
    agent_config_path.write_text("redacted: true\n", encoding="utf-8")

    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service(
            {
                "evaluations": {
                    "enabled": True,
                    "root": str(root),
                    "agent_config_path": str(agent_config_path),
                }
            }
        )

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert str(root) in caplog.text


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
