from pathlib import Path

import pytest
import yaml
from flask import Flask, render_template
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from src.interfaces.chat_app.evaluation_console import (
    build_authorize_request,
    build_evaluation_service,
)
from src.interfaces.chat_app.evaluation_routes import register_evaluations
from src.utils.evaluations_root import EVALUATIONS_MOUNT_PATH

ENABLEMENT_MATRIX = [
    ({}, False),
    ({"evaluations": {}}, False),
    ({"evaluations": {"enabled": False}}, False),
    ({"evaluations": {"enabled": True}}, True),
    ({"evaluations": {"enabled": 1}}, False),
    ({"evaluations": {"enabled": "true"}}, False),
]


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _template_env():
    return Environment(
        loader=FileSystemLoader(str(_repository() / "src/cli/templates")),
        undefined=ChainableUndefined,
    )


def _chat_app_flask_app():
    return Flask(
        __name__,
        template_folder=str(_repository() / "src/interfaces/chat_app/templates"),
        static_folder=str(_repository() / "src/interfaces/chat_app/static"),
    )


@pytest.mark.parametrize(("chat_app_config", "expected_enabled"), ENABLEMENT_MATRIX)
def test_generated_evaluation_console_requires_explicit_enablement(
    chat_app_config, expected_enabled
):
    template = _template_env().get_template("base-config.yaml")

    rendered_config = yaml.safe_load(
        template.render(services={"chat_app": chat_app_config})
    )

    assert rendered_config["services"]["chat_app"]["evaluations"] == {
        "enabled": expected_enabled,
        "root": "/root/archi/evaluations",
        "agent_config_path": "/root/archi/configs/config.yaml",
        "mcp_config_path": None,
    }


@pytest.mark.parametrize(("chat_app_config", "expected_enabled"), ENABLEMENT_MATRIX)
def test_evaluation_console_routes_require_explicit_enablement(
    tmp_path, chat_app_config, expected_enabled
):
    evaluations_config = chat_app_config.get("evaluations")
    if evaluations_config is not None:
        # An explicit ``agent_config_path`` is fork policy: the seam requires one
        # and refuses the live deployment config, because every run snapshots
        # that file into its own run directory. This test measures route
        # registration, so it names a throwaway path and leaves the enablement
        # flag as the only thing under test.
        chat_app_config = {
            **chat_app_config,
            "evaluations": {
                **evaluations_config,
                "root": str(tmp_path),
                "agent_config_path": str(tmp_path / "evaluation.yaml"),
            },
        }

    service = build_evaluation_service(chat_app_config)

    flask_app = _chat_app_flask_app()
    if service is not None:
        register_evaluations(
            flask_app,
            authorize_request=build_authorize_request(False),
            service=service,
        )
    registered_routes = {rule.rule for rule in flask_app.url_map.iter_rules()}
    page_response = flask_app.test_client().get("/evaluations")

    assert (service is not None) is expected_enabled
    if expected_enabled:
        assert service.mcp_config_path is None
    assert ("/evaluations" in registered_routes) is expected_enabled
    assert ("/api/evaluations/catalog" in registered_routes) is expected_enabled
    assert page_response.status_code == (200 if expected_enabled else 404)


@pytest.mark.parametrize("can_view_evaluations", [False, True])
def test_chat_template_shows_evaluation_tab_only_when_allowed(can_view_evaluations):
    flask_app = _chat_app_flask_app()

    with flask_app.test_request_context():
        rendered = render_template(
            "index.html",
            can_view_evaluations=can_view_evaluations,
        )

    evaluation_label = ">Evaluation</a>"
    assert (evaluation_label in rendered) is can_view_evaluations
    if can_view_evaluations:
        assert rendered.index(">Data</button>") < rendered.index(evaluation_label)
        assert rendered.index(evaluation_label) < rendered.index(">Status</a>")


def test_chatbot_deployments_persist_the_evaluation_root():
    compose = (_repository() / "src/cli/templates/base-compose.yaml").read_text()

    assert "./data/evaluations:/root/archi/evaluations" in compose


def test_evaluations_mount_constant_matches_the_compose_template():
    compose = (_repository() / "src/cli/templates/base-compose.yaml").read_text()
    volume_lines = [
        line.strip().lstrip("- ")
        for line in compose.splitlines()
        if "./data/evaluations:" in line
    ]
    assert len(volume_lines) == 1
    _host_side, container_side = volume_lines[0].split(":", 1)
    assert container_side == EVALUATIONS_MOUNT_PATH
