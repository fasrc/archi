"""Render the status board template against the provenance view model.

The template is not reachable from the unit suite through the Flask route, so
it is rendered directly here. Without this, a Jinja syntax error or a renamed
view-model key would only surface on a deployed page.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.interfaces.chat_app.status_provenance import (
    build_deployment_panel,
    build_knowledge_base_panel,
)

TEMPLATE_DIR = Path("src/interfaces/chat_app/templates")

DEPLOYED = datetime(2026, 9, 22, 18, 42, tzinfo=timezone.utc)
STARTED = datetime(2026, 9, 22, 19, 49, tzinfo=timezone.utc)
FINISHED = datetime(2026, 9, 22, 20, 56, tzinfo=timezone.utc)


@pytest.fixture
def env():
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    # The page calls url_for for its static assets; the provenance panels do not.
    environment.globals["url_for"] = lambda *a, **kw: "#"
    return environment


def _render(env, provenance):
    return env.get_template("status.html").render(
        alerts=[], is_alert_manager=False, provenance=provenance
    )


def _deploy_row(**overrides):
    row = {
        "config_ref": "deploy-pin-2026-09e",
        "config_sha": "48022ed74a1f5eed183268d0f82fd7c6d646f9b5",
        "config_head": "48022ed74a1f5eed183268d0f82fd7c6d646f9b5",
        "pin_matched": True,
        "dirty_paths": "",
        "app_version": "v2026.08.0-32-g8667940d",
        "deployed_at": DEPLOYED,
    }
    row.update(overrides)
    return row


def _run_row(**overrides):
    row = {
        "started_at": STARTED,
        "completed_at": FINISHED,
        "status": "updated",
        "documents_embedded": 1091,
        "documents_failed": 0,
        "documents_pending": 0,
        "chunk_count": 6926,
        "config_snapshot": {"categorization": False, "chunk_size": 1000},
    }
    row.update(overrides)
    return row


def test_a_clean_deploy_renders_the_pin_and_no_warning(env):
    html = _render(
        env,
        {
            "deployment": build_deployment_panel(_deploy_row()),
            "knowledge_base": build_knowledge_base_panel(_run_row(), {}),
        },
    )

    assert "deploy-pin-2026-09e" in html
    assert "48022ed7" in html
    assert "matches pin" in html
    assert "live-edited config" not in html
    assert "1091 embedded" in html
    assert "1 hr 7 min" in html


def test_a_live_edited_deploy_renders_the_warning_and_the_paths(env):
    row = _deploy_row(dirty_paths="M\tlists/sources.list")
    html = _render(
        env,
        {
            "deployment": build_deployment_panel(row),
            "knowledge_base": build_knowledge_base_panel(_run_row(), {}),
        },
    )

    assert "live-edited config" in html
    assert "lists/sources.list" in html
    assert "matches pin" not in html


def test_drift_renders_each_flag_with_both_values(env):
    run = _run_row(config_snapshot={"categorization": True, "chunk_size": 1000})
    html = _render(
        env,
        {
            "deployment": build_deployment_panel(_deploy_row()),
            "knowledge_base": build_knowledge_base_panel(
                run, {"categorization": False, "chunk_size": 1000}
            ),
        },
    )

    assert "configuration changed after this ingest" in html
    assert "categorization" in html
    assert "at ingest" in html


def test_the_page_renders_when_no_record_exists_and_alerts_survive(env):
    """An existing deployment carries no record until its next deploy."""
    html = _render(
        env,
        {
            "deployment": build_deployment_panel(None),
            "knowledge_base": build_knowledge_base_panel(None, {}),
        },
    )

    assert "Deployment provenance unavailable" in html
    assert "No completed ingest run recorded" in html
    # The pre-existing sections must be untouched by this change.
    assert "Active Alerts" in html
    assert "All systems operational" in html


def test_the_page_renders_when_provenance_is_absent_entirely(env):
    """Defensive: a caller that never supplied the key must not 500 the page."""
    html = env.get_template("status.html").render(alerts=[], is_alert_manager=False)

    assert "Active Alerts" in html


def test_dirty_path_overflow_is_summarised_not_dumped(env):
    row = _deploy_row(dirty_paths="\n".join(f"M\tfile{n}" for n in range(20)))
    html = _render(
        env,
        {
            "deployment": build_deployment_panel(row),
            "knowledge_base": build_knowledge_base_panel(None, {}),
        },
    )

    assert "20 tracked files" in html
    assert "15 more" in html
