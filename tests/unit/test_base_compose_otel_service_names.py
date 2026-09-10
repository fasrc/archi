"""Every archi process in the compose file must name itself.

The services share one ``.env`` file, so that file cannot carry a per-service
``OTEL_SERVICE_NAME``. Each service block has its own ``environment`` map, and that
is the only place a distinct name can be rendered. Without one, every span from
every service arrives at the receiver under the same name, and a trace cannot say
which process produced it.

``src/utils/telemetry.py`` derives a name from the entrypoint script when the
variable is absent, which covers a process compose did not start. This guard covers
the processes it did.
"""

from pathlib import Path

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from src.cli.utils.service_builder import ServiceBuilder

# The compose block name mapped to the service name its spans must carry. Blocks
# that run no archi Python process are absent on purpose: postgres and grafana are
# third-party images, and db-migrate runs psql.
EXPECTED_SERVICE_NAMES = {
    "chatbot": "archi-chat",
    "data-manager": "archi-data-manager",
    "grader": "archi-grader",
    "piazza": "archi-piazza",
    "mattermost": "archi-mattermost",
    "redmine": "archi-redmine",
    "mailbox": "archi-mailbox",
    "benchmark": "archi-benchmark",
    "config-seed": "archi-config-seed",
}

UNTRACED_BLOCKS = ("postgres", "grafana", "db-migrate")


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
        enabled_services=[
            "chatbot",
            "data-manager",
            "grader",
            "piazza",
            "mattermost",
            "redmine-mailer",
            "benchmarking",
            "grafana",
        ],
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
        grader_port_host=7862,
        grader_port_container=7862,
        grafana_port_host=3000,
        prompt_files=[],
        rubrics=[],
    )
    rendered = environment.get_template("base-compose.yaml").render(**template_vars)
    return yaml.safe_load(rendered)


@pytest.fixture(scope="module")
def compose(tmp_path_factory):
    return _render_compose(tmp_path_factory.mktemp("compose"))


class TestEveryArchiProcessNamesItself:
    @pytest.mark.parametrize(
        ("block", "expected"), sorted(EXPECTED_SERVICE_NAMES.items())
    )
    def test_block_renders_its_own_service_name(self, compose, block, expected):
        services = compose["services"]
        assert block in services, f"{block} is missing from the rendered compose file"

        environment = services[block].get("environment") or {}
        assert environment.get("OTEL_SERVICE_NAME") == expected

    def test_the_names_are_all_different(self, compose):
        """One shared name would make the receiver useless for telling them apart."""
        rendered = [
            compose["services"][block]["environment"]["OTEL_SERVICE_NAME"]
            for block in EXPECTED_SERVICE_NAMES
            if block in compose["services"]
        ]

        assert len(rendered) == len(set(rendered))

    @pytest.mark.parametrize("block", UNTRACED_BLOCKS)
    def test_a_block_that_runs_no_archi_process_gets_no_name(self, compose, block):
        if block not in compose["services"]:
            pytest.skip(f"{block} is not rendered in this configuration")

        environment = compose["services"][block].get("environment") or {}
        assert "OTEL_SERVICE_NAME" not in environment
