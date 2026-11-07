"""Tests for refuse_missing_input_lists at CLI call sites (issue #617)."""

import os
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from src.cli import cli_main
from src.cli.managers.config_manager import ConfigurationManager
from src.cli.managers.deployment_manager import DeploymentManager
from src.cli.managers.templates_manager import TemplateManager
from tests.unit.test_cli_create_dev_smoke import (
    EXAMPLE_CONFIG,
    REPO_ROOT,
    _existing_deployment,
    _record_teardowns,
    archi_home,
    benchmark_config,
    env_file,
)

MISSING = "does-not-exist-617/missing.list"
SENTINEL = "passed-the-input-list-check"


def _setup_spies(monkeypatch):
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
        ConfigurationManager, "set_sources_enabled", _spy("set_sources_enabled")
    )
    monkeypatch.setattr(
        TemplateManager, "prepare_deployment_files", _spy("prepare_deployment_files")
    )
    monkeypatch.setattr(DeploymentManager, "restart_service", _spy("restart_service"))
    return calls


def _write_restart_deployment(archi_home):
    deployment_dir = archi_home / "archi-smoke"
    deployment_dir.mkdir(parents=True)
    (deployment_dir / "compose.yaml").write_text(
        yaml.safe_dump({"services": {"chatbot": {}}})
    )
    configs_dir = deployment_dir / "configs"
    configs_dir.mkdir()
    (configs_dir / "smoke.yaml").write_text(yaml.safe_dump({"name": "smoke"}))
    return deployment_dir


@pytest.mark.parametrize("flags", [["--force"], ["--force", "--dry"]])
def test_create_refuses_missing_list_before_any_change(
    flags, archi_home, env_file, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    calls = _setup_spies(monkeypatch)

    config = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    config["data_manager"]["sources"]["links"]["input_lists"] = [MISSING]
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(config))

    result = CliRunner().invoke(
        cli_main.create,
        [
            "-n",
            "smoke",
            "-c",
            str(cfg),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ]
        + flags,
    )

    assert result.exit_code != 0
    assert MISSING in result.output
    assert str(cfg) in result.output
    assert os.getcwd() in result.output
    assert calls == []
    assert teardowns == []
    assert (existing / "marker.txt").exists()


def test_restart_refuses_missing_list_before_any_change(
    archi_home, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    _write_restart_deployment(archi_home)
    calls = _setup_spies(monkeypatch)

    config = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    config["data_manager"]["sources"]["links"]["input_lists"] = [MISSING]
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(config))

    result = CliRunner().invoke(
        cli_main.restart,
        ["--name", "smoke", "--config", str(cfg)],
    )

    assert result.exit_code != 0
    assert MISSING in result.output
    assert str(cfg) in result.output
    assert os.getcwd() in result.output
    assert calls == []


def test_evaluate_refuses_missing_list_before_any_change(
    archi_home, env_file, benchmark_config, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    calls = _setup_spies(monkeypatch)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )

    config = yaml.safe_load(benchmark_config.read_text())
    config["data_manager"]["sources"]["links"]["input_lists"] = [MISSING]
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(config))

    result = CliRunner().invoke(
        cli_main.evaluate,
        ["--force", "-n", "smoke", "-c", str(cfg), "-e", str(env_file)],
    )

    assert result.exit_code != 0
    assert MISSING in result.output
    assert str(cfg) in result.output
    assert os.getcwd() in result.output
    assert calls == []
    assert teardowns == []
    assert (existing / "marker.txt").exists()


@pytest.mark.parametrize("case", ["relative", "absolute", "undecodable"])
def test_create_passes_the_check_with_an_existing_list(
    case, archi_home, env_file, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    if case == "relative":
        list_path = "examples/deployments/basic-openai/miscellanea.list"
    elif case == "absolute":
        ok = tmp_path / "ok.list"
        ok.write_text("https://example.com/\n")
        list_path = str(ok)
    else:
        binary = tmp_path / "binary.list"
        binary.write_bytes(b"\xff\xfe\x00git-https://x\n")
        list_path = str(binary)

    config = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    config["data_manager"]["sources"]["links"]["input_lists"] = [list_path]
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(config))

    def _sentinel_sm(*a, **kw):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "SecretsManager", _sentinel_sm)

    result = CliRunner().invoke(
        cli_main.create,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(cfg),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert SENTINEL in result.output


@pytest.mark.parametrize("case", ["relative", "absolute", "undecodable"])
def test_restart_passes_the_check_with_an_existing_list(
    case, archi_home, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    _write_restart_deployment(archi_home)

    if case == "relative":
        list_path = "examples/deployments/basic-openai/miscellanea.list"
    elif case == "absolute":
        ok = tmp_path / "ok.list"
        ok.write_text("https://example.com/\n")
        list_path = str(ok)
    else:
        binary = tmp_path / "binary.list"
        binary.write_bytes(b"\xff\xfe\x00git-https://x\n")
        list_path = str(binary)

    config = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    config["data_manager"]["sources"]["links"]["input_lists"] = [list_path]
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(config))

    def _sentinel_ges(self):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(ConfigurationManager, "get_enabled_sources", _sentinel_ges)

    result = CliRunner().invoke(
        cli_main.restart,
        ["--name", "smoke", "--config", str(cfg)],
    )

    assert SENTINEL in str(result.exception)


@pytest.mark.parametrize("case", ["relative", "absolute", "undecodable"])
def test_evaluate_passes_the_check_with_an_existing_list(
    case, archi_home, env_file, benchmark_config, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    if case == "relative":
        list_path = "examples/deployments/basic-openai/miscellanea.list"
    elif case == "absolute":
        ok = tmp_path / "ok.list"
        ok.write_text("https://example.com/\n")
        list_path = str(ok)
    else:
        binary = tmp_path / "binary.list"
        binary.write_bytes(b"\xff\xfe\x00git-https://x\n")
        list_path = str(binary)

    config = yaml.safe_load(benchmark_config.read_text())
    config["data_manager"]["sources"]["links"]["input_lists"] = [list_path]
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(config))

    def _sentinel_preflight(configs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "preflight_benchmark_configs", _sentinel_preflight)

    result = CliRunner().invoke(
        cli_main.evaluate,
        ["--force", "-n", "smoke", "-c", str(cfg), "-e", str(env_file)],
    )

    assert SENTINEL in result.output
