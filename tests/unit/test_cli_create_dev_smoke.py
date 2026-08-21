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


@pytest.fixture
def benchmark_config(tmp_path):
    """Write a benchmarking-valid config to tmp_path and return the path.

    Satisfies all five blank keys that the rendered base-config.yaml exposes
    for the benchmarking service (agent_class, agent_md_file, provider, model,
    ollama_url) so validate_configs passes with services=["postgres",
    "benchmarking"].  agent_md_file uses an absolute path to an existing repo
    file so the exists() check in _validate_benchmarking_config passes.
    """
    agent_md = REPO_ROOT / "examples" / "agents" / "cms-comp-ops.md"
    miscellanea = (
        REPO_ROOT / "examples" / "deployments" / "basic-openai" / "miscellanea.list"
    )
    config_text = f"""\
name: smoke-benchmark

services:
  chat_app:
    agent_class: CMSCompOpsAgent
    agents_dir: examples/agents
    client_timeout_seconds: 1800
    default_provider: openai
    default_model: gpt-4o
    providers:
      openai:
        enabled: true
        default_model: gpt-4o
        models:
          - gpt-4o
      local:
        enabled: false
    trained_on: My data
    port: 7866
    external_port: 7866
  vectorstore:
    backend: postgres
  data_manager:
    port: 7889
    external_port: 7889
    auth:
      enabled: false
  benchmarking:
    agent_class: CMSCompOpsAgent
    agent_md_file: {str(agent_md)}
    provider: openai
    model: gpt-4o
    ollama_url: http://localhost:11434

data_manager:
  sources:
    links:
      input_lists:
        - {str(miscellanea)}
  embedding_name: HuggingFaceEmbeddings
"""
    p = tmp_path / "benchmark.yaml"
    p.write_text(config_text)
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


def _existing_deployment(archi_home, name="smoke"):
    """Create a deployment directory with a marker file that must survive a failed create."""
    existing = archi_home / f"archi-{name}"
    existing.mkdir(parents=True)
    (existing / "marker.txt").write_text("pre-existing deployment")
    return existing


def _record_teardowns(monkeypatch):
    from src.cli.managers.deployment_manager import DeploymentManager

    teardowns = []
    monkeypatch.setattr(
        DeploymentManager,
        "delete_deployment",
        lambda self, **kwargs: teardowns.append(kwargs),
    )
    return teardowns


def test_force_create_with_missing_grafana_secret_keeps_existing_deployment(
    archi_home, monkeypatch
):
    """A create that cannot satisfy its secrets must not destroy the deployment first.

    Without --env-file, SecretsManager falls back to secrets_dummy.env, which
    holds only PG_PASSWORD; grafana requires GRAFANA_PG_PASSWORD. The run is
    therefore guaranteed to fail validation, so it must fail before the
    --force teardown rather than after it.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "--services",
            "chatbot,grafana",
            "--hostmode",
        ],
    )

    assert teardowns == [], (
        f"existing deployment was torn down before secret validation ran. "
        f"output:\n{result.output}\n"
    )
    assert (existing / "marker.txt").exists(), (
        f"existing deployment directory was removed by a create that could never "
        f"succeed. output:\n{result.output}\n"
    )
    assert result.exit_code != 0, (
        f"create without the grafana secret should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert (
        "GRAFANA_PG_PASSWORD" in result.output
    ), f"the error should name the missing secret. output:\n{result.output}\n"
    assert "--env-file" in result.output, (
        f"the error should point at --env-file rather than at the packaged dummy "
        f"env file. output:\n{result.output}\n"
    )


def test_force_create_with_missing_secret_keeps_existing_deployment(
    env_file, archi_home, monkeypatch
):
    """The defect is an ordering defect, not a grafana defect.

    grader requires ADMIN_PASSWORD, which the env_file fixture does not supply.
    A fix that special-cases grafana passes the test above and fails this one.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

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
            "chatbot,grader",
            "--hostmode",
        ],
    )

    assert teardowns == [], (
        f"existing deployment was torn down before secret validation ran. "
        f"output:\n{result.output}\n"
    )
    assert (existing / "marker.txt").exists(), (
        f"existing deployment directory was removed for a non-grafana secret "
        f"failure. output:\n{result.output}\n"
    )
    assert result.exit_code != 0, (
        f"create without ADMIN_PASSWORD should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )


def test_force_create_with_unbuildable_compose_plan_keeps_existing_deployment(
    env_file, archi_home, monkeypatch
):
    """Compose-plan construction can refuse the deployment, so it precedes teardown.

    build_compose_config() calls _discover_repo_path() under --dev, which raises
    when no ancestor holds pyproject.toml. This is the test that fails if the
    teardown sits below secret validation but above the compose plan.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    import click as _click

    from src.cli import cli_main

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    def _no_checkout():
        raise _click.ClickException(
            "archi create --dev requires running from a git checkout "
            "(no pyproject.toml found in any parent directory)."
        )

    monkeypatch.setattr(service_builder, "_discover_repo_path", _no_checkout)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--force",
            "--dev",
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
        f"existing deployment was torn down before the compose plan was built, "
        f"so a knowable failure still cost the operator their deployment. "
        f"output:\n{result.output}\n"
    )
    assert (existing / "marker.txt").exists(), (
        f"existing deployment directory was removed despite an unbuildable "
        f"compose plan. output:\n{result.output}\n"
    )
    assert result.exit_code != 0, (
        f"--dev outside a checkout should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )


def test_force_create_with_invalid_port_keeps_existing_deployment(
    env_file, archi_home, monkeypatch, tmp_path
):
    """A nonnumeric host-side port value is detectable before teardown.

    Port normalization raises ValueError immediately when it cannot convert the
    configured value to an integer.  That check must fire before the --force
    teardown, not seven stages later inside prepare_deployment_files(), so an
    operator whose config was always going to be refused does not lose their
    running deployment first.

    The red run (unfixed code) fails because teardowns != []: the teardown
    executes before the port check fires inside _check_ports_available().
    """
    import yaml

    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    data = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    data["services"]["chat_app"]["port"] = "notaport"
    bad_config = tmp_path / "config-bad-port.yaml"
    bad_config.write_text(yaml.safe_dump(data))

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    def _stop_before_host_mutation(*args, **kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "TemplateManager", _stop_before_host_mutation)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(bad_config),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert teardowns == [], (
        f"existing deployment was torn down before port validation ran. "
        f"output:\n{result.output}\n"
    )
    assert (existing / "marker.txt").exists(), (
        f"existing deployment directory was removed despite an invalid port "
        f"config. output:\n{result.output}\n"
    )
    assert result.exit_code != 0, (
        f"a nonnumeric port value should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert (
        "Invalid port value" in result.output
    ), f"the error should name the invalid port value. output:\n{result.output}\n"


def test_force_create_with_falsy_port_keeps_existing_deployment(
    env_file, archi_home, monkeypatch, tmp_path
):
    """A configured falsy port (e.g. 0) is detectable before teardown.

    Port 0 is an invalid port value (out of range).  The check must fire before
    the --force teardown, so an operator whose config was always going to be
    refused does not lose their running deployment first.

    The red run (unfixed code) fails because teardowns != []: the falsy value is
    silently dropped by extract_port_config, the port check passes, and teardown
    runs before the config refusal surfaces.
    """
    import yaml

    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    data = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    data["services"]["chat_app"]["port"] = 0
    bad_config = tmp_path / "config-zero-port.yaml"
    bad_config.write_text(yaml.safe_dump(data))

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    def _stop_before_host_mutation(*args, **kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "TemplateManager", _stop_before_host_mutation)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(bad_config),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert teardowns == [], (
        f"existing deployment was torn down before port validation ran. "
        f"output:\n{result.output}\n"
    )
    assert (existing / "marker.txt").exists(), (
        f"existing deployment directory was removed despite an invalid port "
        f"config. output:\n{result.output}\n"
    )
    assert result.exit_code != 0, (
        f"a port value of 0 should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )


def test_force_create_with_duplicate_ports_keeps_existing_deployment(
    env_file, archi_home, monkeypatch, tmp_path
):
    """Duplicate host-side port assignments are detectable before teardown.

    When two enabled services share the same host port, validate_port_config()
    returns an errors list and the caller must raise before reaching the
    --force teardown.  data-manager is auto-enabled, so giving both
    services.chat_app.port and services.data_manager.port the same value
    exercises the duplicate check with only --services chatbot.

    The red run (unfixed code) fails because teardowns != []: the teardown
    executes before the duplicate check fires inside _check_ports_available().
    """
    import yaml

    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    data = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    data["services"]["chat_app"]["port"] = 7866
    data["services"]["data_manager"]["port"] = 7866
    bad_config = tmp_path / "config-dup-ports.yaml"
    bad_config.write_text(yaml.safe_dump(data))

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    def _stop_before_host_mutation(*args, **kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "TemplateManager", _stop_before_host_mutation)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(bad_config),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert teardowns == [], (
        f"existing deployment was torn down before port validation ran. "
        f"output:\n{result.output}\n"
    )
    assert (existing / "marker.txt").exists(), (
        f"existing deployment directory was removed despite a duplicate port "
        f"config. output:\n{result.output}\n"
    )
    assert result.exit_code != 0, (
        f"duplicate host-side port assignment should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert "assigned to multiple services" in result.output, (
        f"the error should name the duplicate port assignment. "
        f"output:\n{result.output}\n"
    )


@pytest.mark.usefixtures("fake_repo_root")
def test_dry_force_create_reports_teardown_without_performing_it(
    env_file, archi_home, monkeypatch
):
    """--dry --force still reports the teardown it would perform, and performs none."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--dry",
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

    assert result.exit_code == 0, (
        f"a valid dry run should succeed. exit_code={result.exit_code}\n"
        f"output:\n{result.output}\n"
    )
    assert (
        teardowns == []
    ), f"a dry run must not remove anything. output:\n{result.output}\n"
    assert (
        existing / "marker.txt"
    ).exists(), f"a dry run removed the existing deployment. output:\n{result.output}\n"
    assert "Would remove existing deployment" in result.output, (
        f"a dry forced re-create must still report the teardown it would perform. "
        f"output:\n{result.output}\n"
    )


def test_dry_force_create_with_missing_secret_omits_teardown_notice(
    archi_home, monkeypatch
):
    """A dry run that fails validation must not claim it would remove anything.

    A real run with these inputs refuses before reaching the teardown, so
    reporting the removal would misdescribe what the real run would do.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--dry",
            "--force",
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "--services",
            "chatbot,grafana",
            "--hostmode",
        ],
    )

    assert result.exit_code != 0, (
        f"a dry run missing a required secret should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert (
        teardowns == []
    ), f"a dry run must not remove anything. output:\n{result.output}\n"
    assert (existing / "marker.txt").exists(), (
        f"a failing dry run removed the existing deployment. "
        f"output:\n{result.output}\n"
    )
    assert "Would remove existing deployment" not in result.output, (
        f"a dry run that refuses before the teardown must not claim it would "
        f"remove the deployment. output:\n{result.output}\n"
    )


@pytest.mark.usefixtures("fake_repo_root")
def test_force_create_still_tears_down_once_validation_passes(
    env_file, archi_home, monkeypatch
):
    """The fix must not be 'never tear down'.

    With valid inputs the forced teardown still runs, and still runs before the
    replacement deployment directory is created.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    def _stop_before_host_mutation(*args, **kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "TemplateManager", _stop_before_host_mutation)

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

    assert len(teardowns) == 1, (
        f"a valid forced create must still tear the old deployment down. "
        f"teardowns={teardowns}\noutput:\n{result.output}\n"
    )
    assert SENTINEL in result.output, (
        f"expected the run to reach deployment setup and stop at the sentinel, "
        f"which proves the teardown ran before the replacement was written. "
        f"output:\n{result.output}\n"
    )


def test_force_evaluate_still_removes_existing_runtime(
    env_file, archi_home, benchmark_config, monkeypatch
):
    """Splitting the helper must not break archi evaluate --force.

    evaluate() calls handle_existing_deployment() followed by
    remove_existing_deployment(), then refuses if the directory still exists. It
    depends on the destructive half running at that call site, which is why the
    split had to update it rather than leave only the precondition behind.

    The TemplateManager sentinel stops the run before any host mutation so the
    test never creates real volumes or containers.  The sentinel appearing in
    the output proves the run reached deployment setup, meaning the teardown
    genuinely ran rather than the test passing vacuously because validation
    refused first.
    """
    import shutil

    from src.cli import cli_main
    from src.cli.managers.deployment_manager import DeploymentManager

    existing = _existing_deployment(archi_home)

    teardowns = []

    def _delete(self, **kwargs):
        teardowns.append(kwargs)
        shutil.rmtree(existing, ignore_errors=True)

    def _stop_before_host_mutation(*args, **kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(DeploymentManager, "delete_deployment", _delete)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )
    monkeypatch.setattr(cli_main, "TemplateManager", _stop_before_host_mutation)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.evaluate,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(benchmark_config),
            "-e",
            str(env_file),
        ],
    )

    assert len(teardowns) == 1, (
        f"evaluate --force must still remove the existing benchmarking runtime. "
        f"teardowns={teardowns}\noutput:\n{result.output}\n"
    )
    assert "already exists" not in result.output, (
        f"evaluate --force refused a runtime it was supposed to have removed, "
        f"which is what happens if the destructive half no longer runs at its "
        f"call site. output:\n{result.output}\n"
    )
    assert SENTINEL in result.output, (
        f"expected the run to reach deployment setup and stop at the sentinel, "
        f"which proves the teardown ran before the replacement was written. "
        f"output:\n{result.output}\n"
    )


def test_force_evaluate_with_missing_secret_keeps_existing_runtime(
    archi_home, benchmark_config, monkeypatch, tmp_path
):
    """evaluate --force must not tear down the runtime when secrets validation fails.

    An env file without PG_PASSWORD causes validate_secrets to refuse. The
    teardown must not run until after every refusing step succeeds
    (fasrc/archi#290).
    """
    import shutil

    from src.cli import cli_main
    from src.cli.managers.deployment_manager import DeploymentManager

    existing = _existing_deployment(archi_home)

    teardowns = []

    def _delete(self, **kwargs):
        teardowns.append(kwargs)
        shutil.rmtree(existing, ignore_errors=True)

    env_no_pg = tmp_path / "no-pg.env"
    env_no_pg.write_text("OPENAI_API_KEY=sk-test\n" "HUGGING_FACE_HUB_TOKEN=test-hf\n")

    monkeypatch.setattr(DeploymentManager, "delete_deployment", _delete)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_main.evaluate,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(benchmark_config),
            "-e",
            str(env_no_pg),
        ],
    )

    assert result.exit_code != 0, (
        f"evaluate --force with a missing secret should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert teardowns == [], (
        f"evaluate --force must not tear down the runtime before secrets validation. "
        f"teardowns={teardowns}\noutput:\n{result.output}\n"
    )
    assert (existing / "marker.txt").exists(), (
        f"the existing runtime must survive a secrets validation failure. "
        f"output:\n{result.output}\n"
    )


def test_force_evaluate_with_invalid_config_keeps_existing_runtime(
    env_file, archi_home, monkeypatch
):
    """evaluate --force must not tear down the runtime when config validation fails.

    EXAMPLE_CONFIG (basic-openai) has no services.benchmarking block so
    validate_configs refuses. The teardown must not run until after every
    refusing step succeeds (fasrc/archi#290). This distinguishes an ordering
    fix from a secrets-only special case: a teardown moved below only
    validate_secrets passes the missing-secret test but fails here.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    import shutil

    from src.cli import cli_main
    from src.cli.managers.deployment_manager import DeploymentManager

    existing = _existing_deployment(archi_home)

    teardowns = []

    def _delete(self, **kwargs):
        teardowns.append(kwargs)
        shutil.rmtree(existing, ignore_errors=True)

    monkeypatch.setattr(DeploymentManager, "delete_deployment", _delete)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_main.evaluate,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "-e",
            str(env_file),
        ],
    )

    assert result.exit_code != 0, (
        f"evaluate --force with an invalid config should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert teardowns == [], (
        f"evaluate --force must not tear down the runtime before config validation. "
        f"teardowns={teardowns}\noutput:\n{result.output}\n"
    )
    assert (existing / "marker.txt").exists(), (
        f"the existing runtime must survive a config validation failure. "
        f"output:\n{result.output}\n"
    )


def test_evaluate_without_force_refuses_existing_runtime(
    env_file, archi_home, benchmark_config, monkeypatch
):
    """evaluate without --force must refuse an existing runtime without removing it.

    handle_existing_deployment stays before validation for error precedence.
    Without --force it raises before any teardown or validation logic runs
    (fasrc/archi#290).
    """
    from src.cli import cli_main

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_main.evaluate,
        [
            "-n",
            "smoke",
            "-c",
            str(benchmark_config),
            "-e",
            str(env_file),
        ],
    )

    assert result.exit_code != 0, (
        f"evaluate without --force should refuse an existing runtime. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert (
        "already exists" in result.output
    ), f"expected the already-exists refusal. output:\n{result.output}\n"
    assert (
        teardowns == []
    ), f"a refusal must not remove anything. output:\n{result.output}\n"
    assert (
        existing / "marker.txt"
    ).exists(), f"a refusal removed the existing runtime. output:\n{result.output}\n"


def test_force_evaluate_refuses_when_removal_silently_fails(
    env_file, archi_home, benchmark_config, monkeypatch
):
    """evaluate --force must refuse when remove_existing_deployment silently failed.

    If delete_deployment records but does not remove the directory (simulating a
    swallowed cleanup error), base_dir still exists after the removal attempt.
    The exists() guard then refuses rather than proceeding to write a new runtime
    on top of the old one (fasrc/archi#290).
    """
    from src.cli import cli_main
    from src.cli.managers.deployment_manager import DeploymentManager

    _existing_deployment(archi_home)
    teardowns = []
    monkeypatch.setattr(
        DeploymentManager,
        "delete_deployment",
        lambda self, **kwargs: teardowns.append(kwargs),
    )
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_main.evaluate,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(benchmark_config),
            "-e",
            str(env_file),
        ],
    )

    assert result.exit_code != 0, (
        f"evaluate --force should fail when the directory survived removal. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert "already exists" in result.output, (
        f"expected the already-exists refusal from the post-removal guard. "
        f"output:\n{result.output}\n"
    )
    assert len(teardowns) == 1, (
        f"deletion must have been attempted exactly once. "
        f"teardowns={teardowns}\noutput:\n{result.output}\n"
    )


def test_create_without_force_refuses_existing_deployment(
    env_file, archi_home, monkeypatch
):
    """Without --force an existing deployment is refused, and nothing is removed."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

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
        f"create without --force should refuse an existing deployment. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert (
        "already exists" in result.output
    ), f"expected the already-exists refusal. output:\n{result.output}\n"
    assert (
        teardowns == []
    ), f"a refusal must not remove anything. output:\n{result.output}\n"
    assert (
        existing / "marker.txt"
    ).exists(), f"a refusal removed the existing deployment. output:\n{result.output}\n"


def test_create_without_force_reports_existence_before_config_errors(
    env_file, archi_home, monkeypatch, tmp_path
):
    """The already-exists refusal keeps precedence over unrelated config errors.

    An operator who did not pass --force has not asked to replace anything, so
    that is the problem to report — not a config file they may not have
    intended to deploy. This is why the precondition stays early.
    """
    from src.cli import cli_main

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    missing_config = tmp_path / "does-not-exist.yaml"

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "-n",
            "smoke",
            "-c",
            str(missing_config),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert result.exit_code != 0, (
        f"create without --force should fail. exit_code={result.exit_code}\n"
        f"output:\n{result.output}\n"
    )
    assert "already exists" in result.output, (
        f"the already-exists refusal should outrank the config error. "
        f"output:\n{result.output}\n"
    )
    assert (
        teardowns == []
    ), f"a refusal must not remove anything. output:\n{result.output}\n"
    assert (
        existing / "marker.txt"
    ).exists(), f"a refusal removed the existing deployment. output:\n{result.output}\n"


@pytest.mark.usefixtures("fake_repo_root")
def test_force_create_continues_when_teardown_fails(env_file, archi_home, monkeypatch):
    """A failed cleanup is downgraded to a warning rather than aborting the create.

    This is pre-existing behaviour of the destructive branch and is preserved by
    the split; it is also why the Docker preflight has to stay above the
    teardown, since a swallowed compose failure still removes the directory.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main
    from src.cli.managers.deployment_manager import DeploymentManager

    _existing_deployment(archi_home)

    def _failing_delete(self, **kwargs):
        raise RuntimeError("compose stop failed")

    monkeypatch.setattr(DeploymentManager, "delete_deployment", _failing_delete)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    def _stop_before_host_mutation(*args, **kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "TemplateManager", _stop_before_host_mutation)

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

    assert "Could not clean up existing deployment" in result.output, (
        f"a failed teardown should be reported as a warning. "
        f"output:\n{result.output}\n"
    )
    assert (
        SENTINEL in result.output
    ), f"a failed teardown should not abort the create. output:\n{result.output}\n"


def test_force_create_with_missing_secret_fails_under_verbose_logging(
    archi_home, monkeypatch
):
    """Verbosity must change diagnostics, never exit status.

    create()'s outer handler used to print a traceback at verbosity >= 4 and
    fall through without re-raising, so a failed create exited 0 and any script
    chaining on it treated an unapplied replacement as a success. Measured on
    origin/dev: this exact invocation exits 0 *and* removes the deployment. The
    Docker preflight was moved outside that handler to dodge the problem;
    validation failures sit inside it, which would have made this fix's central
    promise -- refuse instead of destroy -- report success while refusing.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--force",
            "-v",
            "4",
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "--services",
            "chatbot,grafana",
            "--hostmode",
        ],
    )

    assert teardowns == [], (
        f"existing deployment was torn down before validation. "
        f"output:\n{result.output}\n"
    )
    assert (
        existing / "marker.txt"
    ).exists(), f"existing deployment directory was removed. output:\n{result.output}\n"
    assert result.exit_code != 0, (
        f"a create that failed validation must not exit 0 just because "
        f"--verbosity 4 was passed. exit_code={result.exit_code}\n"
        f"output:\n{result.output}\n"
    )


def test_dry_force_create_with_invalid_port_fails(
    env_file, archi_home, monkeypatch, tmp_path
):
    """A dry --force create with a bad port exits non-zero and omits the teardown notice.

    The pure port check runs before remove_existing_deployment(), so a real run
    would refuse before reaching the teardown.  A dry run must mirror that
    behaviour: if the real run would not perform the teardown, the dry run must
    not claim it would (see test_dry_force_create_with_missing_secret_omits_teardown_notice
    for the analogous secret-validation case).
    """
    import yaml

    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    data = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    data["services"]["chat_app"]["port"] = "notaport"
    bad_config = tmp_path / "config-bad-port.yaml"
    bad_config.write_text(yaml.safe_dump(data))

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--dry",
            "--force",
            "-n",
            "smoke",
            "-c",
            str(bad_config),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert result.exit_code != 0, (
        f"a dry run with a nonnumeric port should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert (
        teardowns == []
    ), f"a dry run must not call delete_deployment. output:\n{result.output}\n"
    assert (
        existing / "marker.txt"
    ).exists(), f"a dry run removed the existing deployment. output:\n{result.output}\n"
    assert "Would remove existing deployment" not in result.output, (
        f"a dry run that refuses before the teardown must not claim it would "
        f"remove the deployment. output:\n{result.output}\n"
    )
    assert (
        "Invalid port value" in result.output
    ), f"the error should name the invalid port value. output:\n{result.output}\n"


def test_dry_create_with_invalid_port_fails(env_file, monkeypatch, tmp_path):
    """A plain dry run with a bad port exits non-zero.

    The pure port check is not gated on --force, so it fires for plain --dry
    creates too.  The call site sits above the --dry early return, so there is
    no need for a separate dry-run branch.
    """
    import yaml

    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    monkeypatch.setenv("ARCHI_DIR", str(tmp_path / "archi-home"))

    from src.cli import cli_main

    monkeypatch.setattr(cli_main, "ARCHI_DIR", str(tmp_path / "archi-home"))

    data = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    data["services"]["chat_app"]["port"] = "notaport"
    bad_config = tmp_path / "config-bad-port.yaml"
    bad_config.write_text(yaml.safe_dump(data))

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--dry",
            "-n",
            "smoke",
            "-c",
            str(bad_config),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert result.exit_code != 0, (
        f"a dry run with a nonnumeric port should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert (
        "Invalid port value" in result.output
    ), f"the error should name the invalid port value. output:\n{result.output}\n"


def _config_with_agents_dir(tmp_path, agents_dir):
    """Copy the example config with services.chat_app.agents_dir overridden."""
    import yaml

    data = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    data["services"]["chat_app"]["agents_dir"] = str(agents_dir)
    out = tmp_path / "config-agents-dir.yaml"
    out.write_text(yaml.safe_dump(data))
    return out


@pytest.mark.usefixtures("fake_repo_root")
def test_force_create_with_missing_agents_dir_keeps_existing_deployment(
    env_file, archi_home, monkeypatch, tmp_path
):
    """A nonexistent agents_dir is knowable up front, so it must refuse before teardown.

    _validate_chat_app_config() only checks agents_dir contents inside
    `if agents_dir.exists()`, so a path that does not exist passes validation
    entirely and TemplateManager._stage_agents() raises much later -- after
    base_dir.mkdir(), and so after the forced teardown. That is the same
    ordering defect this change exists to close, reached by a different route.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    config = _config_with_agents_dir(tmp_path, tmp_path / "no-such-agents-dir")

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(config),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert teardowns == [], (
        f"existing deployment was torn down before the agents_dir was checked, "
        f"so a knowable input error still cost the operator their deployment. "
        f"output:\n{result.output}\n"
    )
    assert (existing / "marker.txt").exists(), (
        f"existing deployment directory was removed for a missing agents_dir. "
        f"output:\n{result.output}\n"
    )
    assert result.exit_code != 0, (
        f"a nonexistent agents_dir should fail. exit_code={result.exit_code}\n"
        f"output:\n{result.output}\n"
    )
    assert (
        "agents_dir" in result.output
    ), f"the error should name agents_dir. output:\n{result.output}\n"


def test_create_without_env_file_names_the_flag_when_the_fallback_is_missing(
    archi_home, monkeypatch
):
    """The --env-file hint must survive the constructor, not only validate_secrets.

    SecretsManager resolves its fallback as the RELATIVE path
    src/cli/managers/secrets_dummy.env, and that file is not shipped as package
    data, so an installed archi run outside the repo raises FileNotFoundError in
    the constructor -- before validate_secrets() is ever reached. Running from a
    directory where the relative path does not resolve reproduces exactly that.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli_main.create,
            [
                "-n",
                "smoke",
                "-c",
                str(EXAMPLE_CONFIG),
                "--services",
                "chatbot",
                "--hostmode",
            ],
        )

    assert result.exit_code != 0, (
        f"create without a resolvable env file should fail. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
    assert "--env-file" in result.output, (
        f"the error should name --env-file rather than only reporting a missing "
        f"file path the operator never chose. output:\n{result.output}\n"
    )


def test_explicit_missing_env_file_is_reported_verbatim(
    archi_home, monkeypatch, tmp_path
):
    """An --env-file the operator chose must not be masked by the fallback hint.

    The hint exists for the case where archi silently fell back to its packaged
    placeholder. When the operator named a path themselves, the original error
    is what they need to see, so that branch re-raises unchanged.
    """
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing example config at {EXAMPLE_CONFIG}")

    from src.cli import cli_main

    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    missing_env = tmp_path / "not-here.env"

    runner = CliRunner()
    result = runner.invoke(
        cli_main.create,
        [
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "-e",
            str(missing_env),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert result.exit_code != 0, (
        f"a nonexistent --env-file should fail. exit_code={result.exit_code}\n"
        f"output:\n{result.output}\n"
    )
    assert "not-here.env" in result.output, (
        f"the error should name the path the operator gave. "
        f"output:\n{result.output}\n"
    )
    assert "No --env-file was given" not in result.output, (
        f"the fallback hint must not appear when --env-file was supplied. "
        f"output:\n{result.output}\n"
    )
