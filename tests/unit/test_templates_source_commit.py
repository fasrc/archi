"""prepare_deployment_files records the resolved archi source commit.

Covers the "Deployment records the source commit" spec requirement: the resolved
value is emitted to the deploy log. The render workflow is stubbed to an empty stage
list so the test exercises only the provenance wiring, and ``resolve_source_commit``
is patched so the assertion does not depend on the runner's own git state.
"""

import logging
from pathlib import Path

from jinja2 import Environment

from src.cli.managers import templates_manager
from src.cli.managers.templates_manager import TemplateManager
from src.cli.utils.service_builder import ServiceBuilder


def _manager():
    return TemplateManager(Environment(), verbosity=0)


def _plan(base_dir):
    return ServiceBuilder.build_compose_config(
        name="t",
        verbosity=0,
        base_dir=base_dir,
        enabled_services=["chatbot"],
    )


def test_source_commit_is_logged(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(TemplateManager, "_build_workflow", lambda self, ctx: [])
    monkeypatch.setattr(
        templates_manager, "resolve_source_commit", lambda: "936a52f8-dirty"
    )

    with caplog.at_level(logging.INFO):
        _manager().prepare_deployment_files(
            _plan(tmp_path), config_manager=None, secrets_manager=None
        )

    assert "936a52f8-dirty" in caplog.text


def test_unresolvable_source_commit_does_not_break_preparation(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.setattr(TemplateManager, "_build_workflow", lambda self, ctx: [])
    monkeypatch.setattr(templates_manager, "resolve_source_commit", lambda: "unknown")

    with caplog.at_level(logging.INFO):
        _manager().prepare_deployment_files(
            _plan(tmp_path), config_manager=None, secrets_manager=None
        )

    assert "unknown" in caplog.text
    assert "Finished preparing deployment artifacts" in caplog.text
