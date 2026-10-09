"""Service wiring for the Slack bot (openspec/changes/add-slack-service, PR 2).

The bot itself is covered by test_slack_bot.py. These tests pin how ``archi create``
deploys it: the registry entry, the Compose block, the Dockerfile, the rendered config
defaults, and which secrets reach the container.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from jinja2 import (
    ChainableUndefined,
    Environment,
    FileSystemLoader,
    PackageLoader,
    select_autoescape,
)

from src.cli.managers.secrets_manager import SecretsManager
from src.cli.service_registry import service_registry
from src.cli.utils.service_builder import ServiceBuilder

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = REPO_ROOT / "src/cli/templates/dockerfiles"
SLACK_SECRETS = {"SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"}


# ---------------------------------------------------------------------------
# Registry and service builder
# ---------------------------------------------------------------------------


def test_registry_defines_the_slack_integration_service():
    svc = service_registry.get_all_services()["slack"]
    assert svc.category == "integration"
    # The entry point reads its config from Postgres, then calls the chat app.
    assert svc.depends_on == ["postgres", "chatbot"]
    assert svc.required_secrets == ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"]
    assert svc.consumes_agent_specs is False
    assert svc.requires_volume is False
    assert svc.default_host_port is None


def test_registry_requires_both_slack_tokens():
    assert SLACK_SECRETS <= service_registry.get_required_secrets(["slack"])


def test_selected_services_gain_the_chatbot_that_slack_depends_on():
    # `--services slack` runs the chatbot too, so the chatbot must be validated.
    assert service_registry.selected_with_dependencies(["slack"]) == [
        "slack",
        "chatbot",
    ]


@pytest.mark.parametrize(
    "selected", [["chatbot"], ["chatbot", "slack"], ["slack", "chatbot"]]
)
def test_selected_services_add_no_auto_enabled_or_duplicate_service(selected):
    # postgres and data-manager run anyway; adding them here would change the
    # validation of every deployment.
    assert sorted(service_registry.selected_with_dependencies(selected)) == sorted(
        set(selected) | {"chatbot"}
    )


def _plan(tmp_path, enabled=("chatbot", "slack")):
    return ServiceBuilder.build_compose_config(
        name="demo",
        verbosity=3,
        base_dir=tmp_path,
        enabled_services=list(enabled),
        secrets={"PG_PASSWORD", *SLACK_SECRETS},
        tag="dev",
    )


def test_service_builder_renders_slack_template_vars(tmp_path):
    template_vars = _plan(tmp_path).to_template_vars()
    assert template_vars["slack_enabled"] is True
    assert template_vars["slack_container_name"] == "slack-demo"
    assert template_vars["slack_image"] == "slack-demo"


# ---------------------------------------------------------------------------
# Compose block
# ---------------------------------------------------------------------------


def _compose_services(tmp_path, **overrides):
    environment = Environment(
        loader=FileSystemLoader(str(REPO_ROOT / "src/cli/templates")),
        undefined=ChainableUndefined,
    )
    template_vars = _plan(tmp_path).to_template_vars()
    template_vars.update(
        app_version="test",
        postgres_port=5432,
        # data-manager is enabled with the chatbot, and its ports line needs these.
        data_manager_port_host=7871,
        data_manager_port_container=7871,
        chatbot_port_host=7861,
        chatbot_port_container=7861,
        prompt_files=[],
        rubrics=[],
        host_mode=False,
        gpu_ids=None,
    )
    template_vars.update(overrides)
    rendered = environment.get_template("base-compose.yaml").render(**template_vars)
    return yaml.safe_load(rendered)["services"]


def test_compose_slack_block_waits_for_postgres_config_and_chatbot(tmp_path):
    slack = _compose_services(tmp_path)["slack"]
    assert slack["depends_on"] == {
        "postgres": {"condition": "service_healthy"},
        "config-seed": {"condition": "service_completed_successfully"},
        # The chatbot has no healthcheck; the bot's own /v1/models retry covers it.
        "chatbot": {"condition": "service_started"},
    }


def test_compose_slack_block_names_itself_and_mounts_secrets(tmp_path):
    slack = _compose_services(tmp_path)["slack"]
    environment = slack["environment"]
    assert environment["OTEL_SERVICE_NAME"] == "archi-slack"
    assert environment["SLACK_BOT_TOKEN_FILE"] == "/run/secrets/slack_bot_token"
    assert environment["SLACK_APP_TOKEN_FILE"] == "/run/secrets/slack_app_token"
    assert {"slack_bot_token", "slack_app_token"} <= set(slack["secrets"])
    assert slack["container_name"] == "slack-demo"


def test_compose_slack_block_has_no_data_volume_and_no_gpu(tmp_path):
    slack = _compose_services(tmp_path, gpu_ids=[0])["slack"]
    assert slack["build"]["dockerfile"].endswith("/Dockerfile-slack")
    assert slack["volumes"] == ["./configs:/root/archi/configs"]
    assert "deploy" not in slack


def test_compose_slack_block_uses_host_network_in_host_mode(tmp_path):
    slack = _compose_services(tmp_path, host_mode=True)["slack"]
    assert slack["network_mode"] == "host"
    assert slack["environment"]["PGHOST"] == "localhost"


def test_dockerfile_slack_runs_the_slack_entry_point_on_the_python_base():
    dockerfile = [
        line.rstrip()
        for line in (DOCKERFILES / "Dockerfile-slack").read_text().splitlines()
    ]
    piazza = (DOCKERFILES / "Dockerfile-piazza").read_text().splitlines()
    base_line = next(line.rstrip() for line in piazza if line.startswith("FROM"))
    assert base_line in dockerfile
    assert 'CMD ["python", "-u", "src/bin/service_slack.py"]' in dockerfile
    assert not (DOCKERFILES / "Dockerfile-slack-gpu").exists()


# ---------------------------------------------------------------------------
# Rendered config defaults
# ---------------------------------------------------------------------------


def _render_config(**kwargs):
    environment = Environment(
        loader=PackageLoader("src.cli"),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = environment.get_template("base-config.yaml")
    return yaml.safe_load(template.render(verbosity=0, **kwargs))["services"]


def test_config_slack_defaults():
    slack = _render_config()["slack"]
    assert slack == {
        "chat_url": "http://chatbot:7861",
        "timeout_seconds": 600,
        "max_workers": 4,
        "history_limit": 20,
    }


def test_config_slack_chat_url_follows_the_chat_container_port():
    services = {"chat_app": {"port": 7999, "external_port": 8080}}
    slack = _render_config(services=services)["slack"]
    assert slack["chat_url"] == "http://chatbot:7999"


@pytest.mark.parametrize(
    "chat_app, expected",
    [
        ({}, "http://localhost:7861"),
        ({"port": 7999}, "http://localhost:7999"),
        ({"port": 7999, "external_port": 8080}, "http://localhost:8080"),
    ],
)
def test_config_slack_chat_url_in_host_mode(chat_app, expected):
    # Host mode serves the chat app on external_port when it is set (#310).
    slack = _render_config(services={"chat_app": chat_app}, host_mode=True)["slack"]
    assert slack["chat_url"] == expected


def test_config_slack_explicit_values_are_kept():
    services = {
        "slack": {
            "chat_url": "http://archi.example.org:7861",
            "timeout_seconds": 120,
            "max_workers": 2,
            "history_limit": 0,
        }
    }
    assert _render_config(services=services)["slack"] == services["slack"]


def test_config_slack_null_values_render_the_defaults():
    services = {
        "slack": dict.fromkeys(
            ["chat_url", "timeout_seconds", "max_workers", "history_limit"]
        )
    }
    assert _render_config(services=services)["slack"] == {
        "chat_url": "http://chatbot:7861",
        "timeout_seconds": 600,
        "max_workers": 4,
        "history_limit": 20,
    }


# ---------------------------------------------------------------------------
# ARCHI_API_TOKEN: required only when chat authentication is on
# ---------------------------------------------------------------------------


def _secrets_manager(tmp_path, configs):
    env_path = tmp_path / ".env"
    env_path.write_text("PG_PASSWORD=pw\n")
    config_manager = SimpleNamespace(
        get_models_configs=lambda: [], get_configs=lambda: configs
    )
    return SecretsManager(env_file_path=str(env_path), config_manager=config_manager)


AUTH_ON = [{"services": {"chat_app": {"auth": {"enabled": True}}}}]
AUTH_OFF = [{"services": {"chat_app": {"auth": {"enabled": False}}}}]


def test_archi_api_token_required_for_slack_when_chat_auth_is_on(tmp_path):
    manager = _secrets_manager(tmp_path, AUTH_ON)
    required = manager.get_required_secrets_for_services({"chatbot", "slack"})
    assert "ARCHI_API_TOKEN" in required


@pytest.mark.parametrize(
    "configs, services",
    [
        (AUTH_OFF, {"chatbot", "slack"}),
        ([{}], {"chatbot", "slack"}),
        ([{"services": None}], {"chatbot", "slack"}),
        (AUTH_ON, {"chatbot"}),
    ],
)
def test_archi_api_token_not_required_otherwise(tmp_path, configs, services):
    manager = _secrets_manager(tmp_path, configs)
    assert "ARCHI_API_TOKEN" not in manager.get_required_secrets_for_services(services)
