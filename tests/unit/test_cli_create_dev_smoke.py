"""Smoke-test that archi create --dev --dry runs and prints the dev warning."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from src.cli.utils import service_builder

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = REPO_ROOT / "examples" / "deployments" / "basic-openai" / "config.yaml"

# Marker used to halt a non-dry create before it touches the host container
# runtime, so unit tests never create real volumes or containers.
SENTINEL = "stopped-before-host-mutation"


@pytest.fixture
def fake_repo_root(monkeypatch):
    monkeypatch.setattr(service_builder, "_discover_repo_path", lambda: Path("/REPO"))


@pytest.fixture
def archi_home(tmp_path, monkeypatch):
    """Point the CLI at a throwaway ARCHI_DIR.

    cli_main resolves ARCHI_DIR into a module-level constant at import time, so
    setting the env var alone only works for whichever test imports the module
    first; the attribute has to be patched too or these tests leak into each
    other (and, in the worst case, into the real ~/.archi).
    """
    home = tmp_path / "archi-home"
    monkeypatch.setenv("ARCHI_DIR", str(home))

    from src.cli import cli_main

    monkeypatch.setattr(cli_main, "ARCHI_DIR", str(home))
    return home


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


def test_non_dry_create_requires_docker(env_file, archi_home, monkeypatch):
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

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


def test_non_dry_create_with_podman_skips_docker_check(
    env_file, archi_home, monkeypatch
):
    """--podman must short-circuit the Docker preflight.

    The run is stopped at the first post-preflight step so the test never
    reaches VolumeManager/DeploymentManager, which would otherwise create real
    volumes and containers on whatever container runtime the host happens to
    have installed.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    docker_checked = []

    def _record_docker_check():
        docker_checked.append(True)
        return False

    monkeypatch.setattr(cli_main, "check_docker_available", _record_docker_check)

    def _stop_before_host_mutation(*args, **kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "TemplateManager", _stop_before_host_mutation)

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

    assert docker_checked == [], (
        f"--podman must not consult the Docker preflight at all. "
        f"output:\n{result.output}\n"
    )
    assert "Docker is not available on this system" not in result.output, (
        f"--podman should short-circuit the Docker check regardless of exit code. "
        f"output:\n{result.output}\n"
    )
    assert SENTINEL in result.output, (
        f"expected the run to reach deployment setup and stop at the sentinel. "
        f"output:\n{result.output}\n"
    )


def test_force_create_without_docker_keeps_existing_deployment(
    env_file, archi_home, monkeypatch
):
    """--force must not tear down an existing deployment when Docker is missing.

    handle_existing_deployment() swallows a failed compose stop and still
    rmtree's the deployment directory, so the preflight has to run *before* it.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    existing = archi_home / "archi-smoke"
    existing.mkdir(parents=True)
    (existing / "marker.txt").write_text("pre-existing deployment")

    from src.cli import cli_main
    from src.cli.managers.deployment_manager import DeploymentManager

    monkeypatch.setattr(cli_main, "check_docker_available", lambda: False)

    teardowns = []
    monkeypatch.setattr(
        DeploymentManager,
        "delete_deployment",
        lambda self, **kwargs: teardowns.append(kwargs),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--force",
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

    assert teardowns == [], (
        f"existing deployment was torn down before the Docker preflight ran. "
        f"output:\n{result.output}\n"
    )
    assert (existing / "marker.txt").exists(), (
        f"existing deployment directory was removed without Docker. "
        f"output:\n{result.output}\n"
    )
    assert result.exit_code != 0, (
        f"--force without Docker should fail. exit_code={result.exit_code}\n"
        f"output:\n{result.output}\n"
    )


def test_non_dry_create_requires_docker_under_verbose_logging(
    env_file, archi_home, monkeypatch
):
    """The Docker preflight must exit non-zero even at --verbosity 4.

    The broad `except Exception` handler in create() only prints a traceback at
    verbosity >= 4, so a preflight raised inside that try would be swallowed and
    the command would report success.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

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
            "--verbosity",
            "4",
        ],
    )

    assert result.exit_code != 0, (
        f"missing Docker must fail the command at every verbosity. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
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
