"""Review follow-ups for the #294 render preflight (PR #581).

1. ``_stage_benchmarking`` must refuse a non-empty query path that is not a file:
   the compose file bind-mounts ``./queries.txt`` unconditionally, so a render that
   only warns produces a runtime whose benchmark fails to open its question bank.
   Raising here also makes the preflight refuse it before the ``--force`` teardown.
2. The "existing deployment was not changed" log belongs to the preflight only: the
   real ``prepare_deployment_files()`` runs after the teardown, so that wording is
   false there.
"""

import logging

import pytest
from jinja2 import Environment

from tests.unit.test_benchmark_anchor_staging import _benchmark_context
from tests.unit.test_cli_create_dev_smoke import archi_home, env_file  # noqa: F401


def _manager():
    from src.cli.managers.templates_manager import TemplateManager

    return TemplateManager(Environment(), verbosity=0)


def test_stage_benchmarking_copies_query_file(tmp_path):
    bank = tmp_path / "bank.json"
    bank.write_text("[]")
    out = tmp_path / "out"
    out.mkdir()
    context = _benchmark_context(out, query_file=str(bank), benchmarking={})
    context.base_dir = out

    _manager()._stage_benchmarking(context)

    assert (out / "queries.txt").read_text() == "[]"


@pytest.mark.parametrize("kind", ["directory", "missing"])
def test_stage_benchmarking_refuses_non_file_query_path(tmp_path, kind):
    query_path = tmp_path / "bank"
    if kind == "directory":
        query_path.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    context = _benchmark_context(out, query_file=str(query_path), benchmarking={})
    context.base_dir = out

    with pytest.raises(OSError):
        _manager()._stage_benchmarking(context)

    assert not (out / "queries.txt").exists()


@pytest.mark.parametrize("value", [None, ""])
def test_stage_benchmarking_absent_query_path_only_warns(tmp_path, caplog, value):
    out = tmp_path / "out"
    out.mkdir()
    context = _benchmark_context(out, query_file=value, benchmarking={})
    context.base_dir = out

    with caplog.at_level(logging.WARNING):
        _manager()._stage_benchmarking(context)

    assert "no query file provided" in caplog.text
    assert not (out / "queries.txt").exists()


def _failing_workflow(monkeypatch):
    from src.cli.managers.templates_manager import TemplateManager

    def _broken_stage(ctx):
        raise ValueError("stage-sentinel")

    monkeypatch.setattr(
        TemplateManager, "_build_workflow", lambda self, ctx: [_broken_stage]
    )


def test_prepare_failure_does_not_claim_deployment_unchanged(
    tmp_path, monkeypatch, caplog
):
    _failing_workflow(monkeypatch)
    context = _benchmark_context(tmp_path, query_file="", benchmarking={})

    with caplog.at_level(logging.ERROR), pytest.raises(ValueError):
        _manager().prepare_deployment_files(context.plan, context.config_manager, None)

    assert "_broken_stage failed" in caplog.text
    assert "not changed" not in caplog.text


def test_preflight_failure_says_deployment_unchanged(tmp_path, monkeypatch, caplog):
    _failing_workflow(monkeypatch)
    context = _benchmark_context(tmp_path, query_file="", benchmarking={})

    with caplog.at_level(logging.ERROR), pytest.raises(ValueError):
        _manager().preflight_render(context.plan, context.config_manager, None)

    assert "_broken_stage failed" in caplog.text
    assert "existing deployment was not changed" in caplog.text


def test_evaluate_force_without_query_bank_keeps_existing_runtime(
    env_file, archi_home, monkeypatch, tmp_path
):
    """evaluate --force with no queries_path must refuse before the teardown.

    evaluate() stages queries_path or "." — never a file — and the runtime opens
    QandA.txt unconditionally, so the replacement cannot run.
    """
    from click.testing import CliRunner

    from src.cli import cli_main
    from tests.unit.test_cli_create_dev_smoke import (
        REPO_ROOT,
        _existing_deployment,
        _record_teardowns,
    )
    from tests.unit.test_render_preflight import _satisfied_base_images

    _satisfied_base_images(monkeypatch)
    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )

    agent_md = REPO_ROOT / "examples" / "agents" / "cms-comp-ops.md"
    miscellanea = (
        REPO_ROOT / "examples" / "deployments" / "basic-openai" / "miscellanea.list"
    )
    config_file = tmp_path / "no-bank.yaml"
    config_file.write_text(
        f"""\
name: smoke-no-bank

services:
  benchmarking:
    agent_class: CMSCompOpsAgent
    agent_md_file: {agent_md}
    provider: openai
    model: gpt-4o
    ollama_url: http://localhost:11434

data_manager:
  sources:
    links:
      input_lists:
        - {miscellanea}
  embedding_name: HuggingFaceEmbeddings
"""
    )

    result = CliRunner().invoke(
        cli_main.evaluate,
        ["--force", "-n", "smoke", "-c", str(config_file), "-e", str(env_file)],
    )

    assert result.exit_code != 0, result.output
    assert "Is a directory" in result.output, result.output
    assert teardowns == [], f"teardowns={teardowns}\noutput:\n{result.output}"
    assert existing.exists()
