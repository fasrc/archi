"""One image tag, one build, in the rendered compose file (fasrc/archi#791).

``docker compose up --build`` builds every service that has a ``build:`` block,
and it builds them in parallel. When two services build different Dockerfiles
under one image tag, the build that finishes last owns the tag, and both
containers start from that image. In a deploy with no chatbot, ``config-seed``
fell back to the benchmark image's tag but built Dockerfile-chat, so the
``benchmark`` container could start ``service_chat.py`` instead of
``service_benchmark.py``.
"""

from pathlib import Path

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from src.cli.utils.service_builder import ServiceBuilder

_APP_VERSION = "v2026.10.0-7-g5f3d80e9"
_DOCKERFILES = "archi_code/cli/templates/dockerfiles"
_GPU_CASES = [pytest.param(None, id="cpu"), pytest.param("all", id="gpu")]


def _render_compose(tmp_path, enabled_services, gpu_ids=None):
    repository = Path(__file__).resolve().parents[2]
    environment = Environment(
        loader=FileSystemLoader(str(repository / "src/cli/templates")),
        undefined=ChainableUndefined,
    )
    plan = ServiceBuilder.build_compose_config(
        name="demo",
        verbosity=3,
        base_dir=tmp_path,
        enabled_services=enabled_services,
        secrets={"PG_PASSWORD"},
        tag="dev",
        gpu_ids=gpu_ids,
    )
    template_vars = plan.to_template_vars()
    template_vars.update(
        app_version=_APP_VERSION,
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


def _builds_by_image(compose):
    """Map each image tag that a service builds to ``{service: build block}``.

    A service with a ``build:`` block and no ``image:`` gets an image name that
    compose derives from the project and service names, so it shares no tag.
    """
    builds = {}
    for service_name, service in compose["services"].items():
        if "build" in service and "image" in service:
            builds.setdefault(service["image"], {})[service_name] = service["build"]
    return builds


def _conflicting_builds(compose):
    """Return the image tags that two services build from different build blocks."""
    conflicts = {}
    for image, by_service in _builds_by_image(compose).items():
        first = next(iter(by_service.values()))
        if any(build != first for build in by_service.values()):
            conflicts[image] = by_service
    return conflicts


@pytest.mark.parametrize("gpu_ids", _GPU_CASES)
def test_a_benchmark_only_render_builds_no_tag_from_two_build_blocks(tmp_path, gpu_ids):
    compose = _render_compose(tmp_path, ["benchmarking"], gpu_ids=gpu_ids)

    assert _conflicting_builds(compose) == {}


@pytest.mark.parametrize("gpu_ids", _GPU_CASES)
def test_a_benchmark_only_render_builds_config_seed_as_the_benchmark_image(
    tmp_path, gpu_ids
):
    compose = _render_compose(tmp_path, ["benchmarking"], gpu_ids=gpu_ids)
    config_seed = compose["services"]["config-seed"]
    benchmark = compose["services"]["benchmark"]

    assert config_seed["image"] == benchmark["image"]
    assert config_seed["build"] == benchmark["build"]
    assert config_seed["command"] == [
        "bash",
        "-lc",
        "python -m src.cli.tools.config_seed",
    ]


def test_a_benchmark_only_render_passes_the_app_version_to_config_seed(tmp_path):
    compose = _render_compose(tmp_path, ["benchmarking"])

    environment = compose["services"]["config-seed"]["environment"]
    assert environment["APP_VERSION"] == _APP_VERSION


def test_a_chatbot_render_builds_no_tag_from_two_build_blocks(tmp_path):
    compose = _render_compose(tmp_path, ["chatbot", "benchmarking"])

    assert _conflicting_builds(compose) == {}


def test_a_chatbot_render_keeps_config_seed_on_the_chat_build(tmp_path):
    compose = _render_compose(tmp_path, ["chatbot", "benchmarking"])
    config_seed = compose["services"]["config-seed"]

    assert config_seed["image"] == compose["services"]["chatbot"]["image"]
    assert config_seed["build"] == {
        "context": ".",
        "dockerfile": f"{_DOCKERFILES}/Dockerfile-chat",
        "args": {"APP_VERSION": _APP_VERSION},
    }
    assert "APP_VERSION" not in config_seed["environment"]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "fasrc/archi#810: with a GPU chatbot, config-seed builds Dockerfile-chat "
        "and the chatbot builds Dockerfile-chat-gpu under one tag"
    ),
)
def test_a_gpu_chatbot_render_builds_no_tag_from_two_build_blocks(tmp_path):
    compose = _render_compose(tmp_path, ["chatbot", "benchmarking"], gpu_ids="all")

    assert _conflicting_builds(compose) == {}
