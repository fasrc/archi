"""DeploymentPlan + ServiceBuilder propagate dev_mode + repo_path to template vars."""

from pathlib import Path

import pytest

from src.cli.utils import service_builder
from src.cli.utils.service_builder import ServiceBuilder


@pytest.fixture
def fake_repo_root(monkeypatch):
    monkeypatch.setattr(service_builder, "_discover_repo_path", lambda: Path("/REPO"))
    return Path("/REPO")


def test_dev_true_propagates_to_template_vars(fake_repo_root):
    plan = ServiceBuilder.build_compose_config(
        name="t",
        verbosity=0,
        base_dir=Path("/tmp/x"),
        enabled_services=["chatbot"],
        dev=True,
    )
    vars_ = plan.to_template_vars()
    assert vars_["dev_mode"] is True
    assert vars_["repo_path"] == str(fake_repo_root)


def test_dev_default_off_yields_empty_repo_path():
    plan = ServiceBuilder.build_compose_config(
        name="t",
        verbosity=0,
        base_dir=Path("/tmp/x"),
        enabled_services=["chatbot"],
    )
    vars_ = plan.to_template_vars()
    assert vars_["dev_mode"] is False
    assert vars_["repo_path"] == ""
