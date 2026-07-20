"""Smoke-test that archi create --dev --dry runs and prints the dev warning."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from src.cli.utils import service_builder

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = REPO_ROOT / "examples" / "deployments" / "basic-openai" / "config.yaml"


@pytest.fixture
def fake_repo_root(monkeypatch):
    monkeypatch.setattr(service_builder, "_discover_repo_path", lambda: Path("/REPO"))


@pytest.fixture
def env_file(tmp_path):
    p = tmp_path / "secrets.env"
    p.write_text(
        "OPENAI_API_KEY=sk-test\n"
        "PG_PASSWORD=test-pg\n"
        "HUGGING_FACE_HUB_TOKEN=test-hf\n"
    )
    return p


@pytest.mark.usefixtures("fake_repo_root")
def test_dev_flag_prints_warning_in_dry_run(env_file, tmp_path, monkeypatch):
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")
    monkeypatch.setenv("ARCHI_DIR", str(tmp_path / "archi-home"))

    from src.cli.cli_main import create

    runner = CliRunner()
    result = runner.invoke(
        create,
        [
            "--dev",
            "--dry",
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert "DEV MODE" in result.output, (
        f"expected DEV MODE warning. exit_code={result.exit_code}\n"
        f"output:\n{result.output}\n"
    )
    assert result.exit_code == 0, (
        f"--dev --dry should exit 0. exit_code={result.exit_code}\n"
        f"output:\n{result.output}\n"
    )


def test_dry_run_succeeds_without_docker(env_file, tmp_path, monkeypatch):
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")
    monkeypatch.setenv("ARCHI_DIR", str(tmp_path / "archi-home"))

    from src.cli import cli_main

    monkeypatch.setattr(cli_main, "check_docker_available", lambda: False)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--dry",
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert "Docker is not available" not in result.output, (
        f"--dry should not require a container runtime. exit_code={result.exit_code}\n"
        f"output:\n{result.output}\n"
    )
    assert result.exit_code == 0, (
        f"--dry should exit 0 without Docker. exit_code={result.exit_code}\n"
        f"output:\n{result.output}\n"
    )


def test_non_dry_create_requires_docker(env_file, tmp_path, monkeypatch):
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")
    monkeypatch.setenv("ARCHI_DIR", str(tmp_path / "archi-home"))

    from src.cli import cli_main

    monkeypatch.setattr(cli_main, "check_docker_available", lambda: False)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert result.exit_code != 0, (
        f"non-dry create without --podman should fail without Docker. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert (
        "Docker is not available on this system" in result.output
    ), f"expected the Docker-unavailable message. output:\n{result.output}\n"


def test_non_dry_create_with_podman_skips_docker_check(env_file, tmp_path, monkeypatch):
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")
    monkeypatch.setenv("ARCHI_DIR", str(tmp_path / "archi-home"))

    from src.cli import cli_main

    monkeypatch.setattr(cli_main, "check_docker_available", lambda: False)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
            "--podman",
        ],
    )

    assert "Docker is not available on this system" not in result.output, (
        f"--podman should short-circuit the Docker check regardless of exit code. "
        f"output:\n{result.output}\n"
    )


def test_no_dev_flag_no_warning(env_file, tmp_path, monkeypatch):
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")
    monkeypatch.setenv("ARCHI_DIR", str(tmp_path / "archi-home"))

    from src.cli.cli_main import create

    runner = CliRunner()
    result = runner.invoke(
        create,
        [
            "--dry",
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )
    assert (
        "DEV MODE" not in result.output
    ), f"DEV MODE should not appear without --dev. output:\n{result.output}\n"
