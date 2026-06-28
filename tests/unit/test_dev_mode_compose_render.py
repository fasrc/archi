"""Render base-compose.yaml with dev_mode true/false and check structural invariants."""

from pathlib import Path

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape

DEV_MOUNTED_SERVICES = [
    "data-manager",
    "chatbot",
    "grader",
    "piazza",
    "mattermost",
    "redmine",
    "mailbox",
    "benchmark",
]


@pytest.fixture
def render_compose():
    repo_root = Path(__file__).resolve().parents[2]
    template_dir = repo_root / "src" / "cli" / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-compose.yaml")

    base_vars = dict(
        data_manager_enabled=True,
        postgres_enabled=True,
        chatbot_enabled=True,
        grader_enabled=True,
        piazza_enabled=True,
        mattermost_enabled=True,
        redmine_mailer_enabled=True,
        benchmarking_enabled=True,
        data_manager_image="im",
        data_manager_tag="t",
        data_manager_container_name="dm",
        data_manager_port_host=1,
        data_manager_port_container=2,
        data_manager_volume_name="dmv",
        postgres_container_name="pg",
        postgres_port=5432,
        postgres_volume_name="pgv",
        chatbot_image="im",
        chatbot_tag="t",
        chatbot_container_name="cb",
        chatbot_port_host=8000,
        chatbot_port_container=8000,
        grader_image="im",
        grader_tag="t",
        grader_container_name="gr",
        grader_volume_name="gv",
        grader_port_host=8001,
        grader_port_container=8001,
        piazza_image="im",
        piazza_tag="t",
        piazza_container_name="pz",
        mattermost_image="im",
        mattermost_tag="t",
        mattermost_container_name="mm",
        redmine_mailer_image="im",
        redmine_mailer_tag="t",
        redmine_mailer_container_name="rm",
        benchmarking_image="im",
        benchmarking_tag="t",
        benchmarking_container_name="bm",
        benchmarking_volume_name="bv",
        benchmarking_dest="/tmp",
        data_volume_name="dv",
        app_version="1.0",
        verbosity=3,
        host_mode=False,
        gpu_ids=None,
        use_podman=False,
        required_secrets=[],
        required_volumes=[],
        name="x",
        prompt_files=[],
        rubrics=[],
    )

    def render(dev_mode: bool):
        out = template.render(
            dev_mode=dev_mode, repo_path="/REPO" if dev_mode else "", **base_vars
        )
        return yaml.safe_load(out)

    return render


def _env_dict(service):
    env_block = service.get("environment")
    if isinstance(env_block, dict):
        return env_block
    if isinstance(env_block, list):
        return dict(kv.split("=", 1) if "=" in kv else (kv, None) for kv in env_block)
    return {}


def test_dev_mounts_present_only_when_dev_mode(render_compose):
    dev = render_compose(dev_mode=True)
    baseline = render_compose(dev_mode=False)

    for service_name in DEV_MOUNTED_SERVICES:
        dev_vols = dev["services"][service_name].get("volumes", [])
        baseline_vols = baseline["services"][service_name].get("volumes", [])
        assert any(
            "/REPO/src:/root/archi/src" in v for v in dev_vols
        ), f"{service_name} missing dev src mount when dev_mode=True"
        assert not any(
            "/REPO" in v for v in baseline_vols
        ), f"{service_name} leaks dev path when dev_mode=False"


def test_pythondontwritebytecode_set_iff_dev_mode(render_compose):
    dev = render_compose(dev_mode=True)
    baseline = render_compose(dev_mode=False)

    for service_name in DEV_MOUNTED_SERVICES:
        dev_env = _env_dict(dev["services"][service_name])
        baseline_env = _env_dict(baseline["services"][service_name])
        assert (
            "PYTHONDONTWRITEBYTECODE" in dev_env
        ), f"{service_name} missing PYTHONDONTWRITEBYTECODE in dev_mode"
        assert (
            "PYTHONDONTWRITEBYTECODE" not in baseline_env
        ), f"{service_name} sets PYTHONDONTWRITEBYTECODE when dev_mode=False"


def test_service_set_unchanged_by_dev_mode(render_compose):
    dev = render_compose(dev_mode=True)
    baseline = render_compose(dev_mode=False)
    assert set(dev["services"].keys()) == set(baseline["services"].keys())


def test_non_dev_services_never_get_pythondontwritebytecode(render_compose):
    dev = render_compose(dev_mode=True)
    for service_name in ("postgres", "config-seed"):
        env = _env_dict(dev["services"][service_name])
        assert (
            "PYTHONDONTWRITEBYTECODE" not in env
        ), f"{service_name} should not get dev env vars (no src mount)"
