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
from src.utils.evaluations_root import EVALUATIONS_MOUNT_PATH, validate_evaluations_root

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


def _render_compose(tmp_path):
    """Render base-compose.yaml the way the CLI does, then parse it."""
    from src.cli.utils.service_builder import ServiceBuilder

    plan = ServiceBuilder.build_compose_config(
        name="demo",
        verbosity=3,
        base_dir=tmp_path,
        enabled_services=["chatbot"],
        secrets={"PG_PASSWORD"},
        tag="dev",
    )
    template_vars = plan.to_template_vars()
    template_vars.update(
        app_version="test",
        postgres_port=5432,
        data_manager_port_host=7871,
        data_manager_port_container=7871,
        chatbot_port_host=7861,
        chatbot_port_container=7861,
        prompt_files=[],
        rubrics=[],
        evaluation_mcp_configured=False,
    )
    rendered = _template_env().get_template("base-compose.yaml").render(**template_vars)
    return yaml.safe_load(rendered)


def _container_mount_targets(compose, service):
    """Container-side targets of ``service``'s own volumes, or [] if absent.

    Scoped to one service on purpose. A whole-file substring scan cannot tell
    that the mount moved to another service: the line count and the container
    side both stay identical, the assertion still passes, and the validator ends
    up guarding a mount the chat container does not have.
    """
    volumes = (compose.get("services", {}).get(service) or {}).get("volumes") or []
    return [str(volume).split(":")[1] for volume in volumes if ":" in str(volume)]


def test_evaluations_mount_constant_matches_the_chatbot_service(tmp_path):
    compose = _render_compose(tmp_path)

    assert EVALUATIONS_MOUNT_PATH in _container_mount_targets(compose, "chatbot")


def test_the_mount_check_is_scoped_to_the_chatbot_service():
    """Proof that the check above discriminates.

    Same mount, same spelling, attached to a different service: the chatbot no
    longer has it, and the check has to say so.
    """
    drifted = {
        "services": {
            "chatbot": {"volumes": ["./configs:/root/archi/configs"]},
            "grafana": {"volumes": [f"./data/evaluations:{EVALUATIONS_MOUNT_PATH}"]},
        }
    }

    assert EVALUATIONS_MOUNT_PATH not in _container_mount_targets(drifted, "chatbot")
    assert EVALUATIONS_MOUNT_PATH in _container_mount_targets(drifted, "grafana")


def test_default_evaluations_config_is_accepted_and_renders_unchanged():
    template = _template_env().get_template("base-config.yaml")

    rendered_no_block = yaml.safe_load(template.render(services={"chat_app": {}}))
    chat_app_no_block = rendered_no_block["services"]["chat_app"]
    assert chat_app_no_block["evaluations"]["root"] == EVALUATIONS_MOUNT_PATH
    assert validate_evaluations_root(chat_app_no_block) is None

    rendered_enabled = yaml.safe_load(
        template.render(services={"chat_app": {"evaluations": {"enabled": True}}})
    )
    chat_app_enabled = rendered_enabled["services"]["chat_app"]
    assert chat_app_enabled["evaluations"]["root"] == EVALUATIONS_MOUNT_PATH
    assert validate_evaluations_root(chat_app_enabled) is None
