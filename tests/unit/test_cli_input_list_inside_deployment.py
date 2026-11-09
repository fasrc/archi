"""Tests for refuse_input_lists_inside_deployment at the create and evaluate call sites (#819)."""

import os

import pytest
import yaml
from click.testing import CliRunner

from src.cli import cli_main
from src.cli.managers.templates_manager import TemplateManager
from tests.unit.test_cli_create_dev_smoke import (
    EXAMPLE_CONFIG,
    REPO_ROOT,
    _existing_deployment,
    _record_teardowns,
    archi_home,
    benchmark_config,
    env_file,
    satisfied_base_images,
)

SENTINEL = "passed-the-inside-deployment-check"
URL_LINE = "https://example.com/\n"
CREATE_ARGS = ["-n", "smoke", "--services", "chatbot", "--hostmode"]


def _setup_spies(monkeypatch):
    """Record the teardown and stop there.

    A run without the check must reach remove_existing_deployment, so the
    preflight render is stubbed out (as test_force_create_still_tears_down_once_
    validation_passes does) and the teardown spy raises after it records.
    """
    calls = []

    def _spy(name):
        def _inner(*a, **kw):
            calls.append(name)
            raise RuntimeError(f"spy reached: {name}")

        return _inner

    monkeypatch.setattr(
        cli_main, "remove_existing_deployment", _spy("remove_existing_deployment")
    )
    monkeypatch.setattr(
        TemplateManager, "preflight_render", lambda self, *a, **kw: None
    )
    return calls


def _list_inside(existing):
    weblists = existing / "weblists"
    weblists.mkdir()
    inside = weblists / "x.list"
    inside.write_text(URL_LINE)
    return inside


def _write_config(tmp_path, base, input_lists):
    config = yaml.safe_load(base.read_text())
    config["data_manager"]["sources"]["links"]["input_lists"] = input_lists
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(config))
    return cfg


def _assert_refused_before_the_teardown(
    result, inside, existing, cfg, calls, teardowns
):
    assert result.exit_code != 0
    assert str(inside) in result.output
    assert str(existing.resolve()) in result.output
    assert str(cfg) in result.output
    assert calls == []
    assert teardowns == []
    assert inside.exists()
    assert (existing / "marker.txt").exists()


@pytest.mark.parametrize("flags", [["--force"], ["--force", "--dry"]])
def test_forced_create_refuses_a_list_inside_the_deployment_before_the_teardown(
    flags, archi_home, env_file, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    existing = _existing_deployment(archi_home)
    inside = _list_inside(existing)
    teardowns = _record_teardowns(monkeypatch)
    calls = _setup_spies(monkeypatch)
    cfg = _write_config(tmp_path, EXAMPLE_CONFIG, [str(inside)])

    result = CliRunner().invoke(
        cli_main.create, CREATE_ARGS + ["-c", str(cfg), "-e", str(env_file)] + flags
    )

    _assert_refused_before_the_teardown(result, inside, existing, cfg, calls, teardowns)


def test_forced_create_refuses_a_relative_path_that_resolves_inside_the_deployment(
    archi_home, env_file, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    existing = _existing_deployment(archi_home)
    inside = _list_inside(existing)
    relative = os.path.relpath(inside, REPO_ROOT)
    assert relative.startswith("..")
    teardowns = _record_teardowns(monkeypatch)
    calls = _setup_spies(monkeypatch)
    cfg = _write_config(tmp_path, EXAMPLE_CONFIG, [relative])

    result = CliRunner().invoke(
        cli_main.create, CREATE_ARGS + ["-c", str(cfg), "-e", str(env_file), "--force"]
    )

    _assert_refused_before_the_teardown(result, inside, existing, cfg, calls, teardowns)
    assert f"{relative} -> {inside.resolve()}" in result.output


def test_forced_evaluate_refuses_a_list_inside_the_runtime_before_the_teardown(
    archi_home, env_file, benchmark_config, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )
    existing = _existing_deployment(archi_home)
    inside = _list_inside(existing)
    teardowns = _record_teardowns(monkeypatch)
    calls = _setup_spies(monkeypatch)
    cfg = _write_config(tmp_path, benchmark_config, [str(inside)])

    result = CliRunner().invoke(
        cli_main.evaluate,
        ["--force", "-n", "smoke", "-c", str(cfg), "-e", str(env_file)],
    )

    _assert_refused_before_the_teardown(result, inside, existing, cfg, calls, teardowns)


def test_evaluate_without_force_reports_the_existing_runtime_not_the_list(
    archi_home, env_file, benchmark_config, monkeypatch, tmp_path
):
    """Without --force there is no teardown, so the check does not run.

    The existing-runtime refusal keeps its precedence (it comes after the
    input-list checks in evaluate).
    """
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )
    existing = _existing_deployment(archi_home)
    inside = _list_inside(existing)
    teardowns = _record_teardowns(monkeypatch)
    calls = _setup_spies(monkeypatch)
    cfg = _write_config(tmp_path, benchmark_config, [str(inside)])

    result = CliRunner().invoke(
        cli_main.evaluate, ["-n", "smoke", "-c", str(cfg), "-e", str(env_file)]
    )

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "inside the deployment directory" not in result.output
    assert calls == []
    assert teardowns == []
    assert inside.exists()


def test_forced_create_passes_the_check_with_a_list_outside_the_deployment(
    archi_home, env_file, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    _existing_deployment(archi_home)
    outside = tmp_path / "ok.list"
    outside.write_text(URL_LINE)
    cfg = _write_config(tmp_path, EXAMPLE_CONFIG, [str(outside)])

    def _sentinel_sm(*a, **kw):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "SecretsManager", _sentinel_sm)

    result = CliRunner().invoke(
        cli_main.create, CREATE_ARGS + ["-c", str(cfg), "-e", str(env_file), "--force"]
    )

    assert SENTINEL in result.output


def test_forced_evaluate_passes_the_check_with_a_list_outside_the_runtime(
    archi_home, env_file, benchmark_config, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    _existing_deployment(archi_home)
    outside = tmp_path / "ok.list"
    outside.write_text(URL_LINE)
    cfg = _write_config(tmp_path, benchmark_config, [str(outside)])

    def _sentinel_preflight(configs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "preflight_benchmark_configs", _sentinel_preflight)

    result = CliRunner().invoke(
        cli_main.evaluate,
        ["--force", "-n", "smoke", "-c", str(cfg), "-e", str(env_file)],
    )

    assert SENTINEL in result.output
