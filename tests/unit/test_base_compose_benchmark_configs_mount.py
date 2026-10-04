from pathlib import Path

import yaml
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from src.cli.utils.service_builder import ServiceBuilder


def _render_compose(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    environment = Environment(
        loader=FileSystemLoader(str(repository / "src/cli/templates")),
        undefined=ChainableUndefined,
    )
    plan = ServiceBuilder.build_compose_config(
        name="demo",
        verbosity=3,
        base_dir=tmp_path,
        enabled_services=["chatbot", "benchmarking"],
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

    rendered = environment.get_template("base-compose.yaml").render(**template_vars)
    return yaml.safe_load(rendered)


class TestBenchmarkConfigsMount:
    def test_configs_mounted_readonly_in_benchmark(self, tmp_path):
        compose = _render_compose(tmp_path)

        assert (
            "./configs:/root/archi/configs:ro"
            in compose["services"]["benchmark"]["volumes"]
        )

    def test_configs_not_mounted_readwrite_in_benchmark(self, tmp_path):
        compose = _render_compose(tmp_path)

        assert (
            "./configs:/root/archi/configs"
            not in compose["services"]["benchmark"]["volumes"]
        )

    def test_configs_mount_unchanged_in_chatbot(self, tmp_path):
        compose = _render_compose(tmp_path)

        assert (
            "./configs:/root/archi/configs" in compose["services"]["chatbot"]["volumes"]
        )

    def test_configs_mount_unchanged_in_data_manager(self, tmp_path):
        compose = _render_compose(tmp_path)

        assert (
            "./configs:/root/archi/configs"
            in compose["services"]["data-manager"]["volumes"]
        )

    def test_rendered_config_mount_unchanged_in_config_seed(self, tmp_path):
        compose = _render_compose(tmp_path)

        assert (
            "./configs:/rendered-config:ro"
            in compose["services"]["config-seed"]["volumes"]
        )
