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
    env_file, archi_home, monkeypatch
):
    """Splitting the helper must not break archi evaluate --force.

    evaluate() calls handle_existing_deployment() and then refuses if the
    directory still exists, so it depends on the destructive branch running at
    that call site.
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

    assert len(teardowns) == 1, (
        f"evaluate --force must still remove the existing benchmarking runtime. "
        f"teardowns={teardowns}\noutput:\n{result.output}\n"
    )
    assert "already exists" not in result.output, (
        f"evaluate --force refused a runtime it was supposed to have removed, "
        f"which is what happens if the destructive half no longer runs at its "
        f"call site. output:\n{result.output}\n"
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
