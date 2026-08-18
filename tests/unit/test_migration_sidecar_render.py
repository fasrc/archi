"""Render base-compose.yaml and assert db-migrate sidecar structure."""

from pathlib import Path

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape


@pytest.fixture
def render_compose():
    repo_root = Path(__file__).resolve().parents[2]
    template_dir = repo_root / "src" / "cli" / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-compose.yaml")

    base_vars = dict(
        data_manager_enabled=True,
        postgres_enabled=True,
        chatbot_enabled=True,
        grader_enabled=True,
        piazza_enabled=True,
        mattermost_enabled=True,
        redmine_mailer_enabled=True,
        benchmarking_enabled=True,
        data_manager_image="im",
        data_manager_tag="t",
        data_manager_container_name="dm",
        data_manager_port_host=1,
        data_manager_port_container=2,
        data_manager_volume_name="dmv",
        postgres_container_name="pg",
        postgres_port=5432,
        postgres_volume_name="pgv",
        chatbot_image="im",
        chatbot_tag="t",
        chatbot_container_name="cb",
        chatbot_port_host=8000,
        chatbot_port_container=8000,
        grader_image="im",
        grader_tag="t",
        grader_container_name="gr",
        grader_volume_name="gv",
        grader_port_host=8001,
        grader_port_container=8001,
        piazza_image="im",
        piazza_tag="t",
        piazza_container_name="pz",
        mattermost_image="im",
        mattermost_tag="t",
        mattermost_container_name="mm",
        redmine_mailer_image="im",
        redmine_mailer_tag="t",
        redmine_mailer_container_name="rm",
        benchmarking_image="im",
        benchmarking_tag="t",
        benchmarking_container_name="bm",
        benchmarking_volume_name="bv",
        benchmarking_dest="/tmp",
        data_volume_name="dv",
        app_version="1.0",
        verbosity=3,
        host_mode=False,
        gpu_ids=None,
        use_podman=False,
        required_secrets=[],
        required_volumes=[],
        name="x",
        prompt_files=[],
        rubrics=[],
    )

    def render(postgres_enabled: bool = True):
        out = template.render(
            dev_mode=False,
            repo_path="",
            **{**base_vars, "postgres_enabled": postgres_enabled},
        )
        return yaml.safe_load(out)

    return render


def test_db_migrate_service_exists_when_postgres_enabled(render_compose):
    compose = render_compose(postgres_enabled=True)
    assert (
        "db-migrate" in compose["services"]
    ), "db-migrate service missing when postgres_enabled"


def test_db_migrate_restart_no(render_compose):
    svc = render_compose(postgres_enabled=True)["services"]["db-migrate"]
    assert svc.get("restart") == "no"


def test_db_migrate_depends_on_postgres_healthy(render_compose):
    svc = render_compose(postgres_enabled=True)["services"]["db-migrate"]
    depends_on = svc.get("depends_on", {})
    assert "postgres" in depends_on, "db-migrate missing depends_on.postgres"
    assert (
        depends_on["postgres"]["condition"] == "service_healthy"
    ), "db-migrate.depends_on.postgres.condition must be service_healthy"


def test_db_migrate_absent_when_postgres_disabled(render_compose):
    compose = render_compose(postgres_enabled=False)
    assert (
        "db-migrate" not in compose["services"]
    ), "db-migrate should not appear when postgres disabled"


def test_config_seed_depends_on_db_migrate(render_compose):
    svc = render_compose(postgres_enabled=True)["services"]["config-seed"]
    depends_on = svc.get("depends_on", {})
    assert "db-migrate" in depends_on, "config-seed missing depends_on.db-migrate"
    assert (
        depends_on["db-migrate"]["condition"] == "service_completed_successfully"
    ), "config-seed.depends_on.db-migrate.condition must be service_completed_successfully"


def test_data_manager_depends_on_db_migrate(render_compose):
    svc = render_compose(postgres_enabled=True)["services"]["data-manager"]
    depends_on = svc.get("depends_on", {})
    assert "db-migrate" in depends_on, "data-manager missing depends_on.db-migrate"
    assert (
        depends_on["db-migrate"]["condition"] == "service_completed_successfully"
    ), "data-manager.depends_on.db-migrate.condition must be service_completed_successfully"


def test_db_migrate_exposes_pgpassword_for_libpq(render_compose):
    """psql reads PGPASSWORD, not PG_PASSWORD.

    The other services here are Python processes that read PG_PASSWORD themselves and
    hand it to psycopg. db-migrate is the only service whose command is psql, which
    authenticates through libpq and recognises PGPASSWORD alone. Without it the sidecar
    cannot authenticate over TCP (there is no .pgpass in the image), exits non-zero, and
    every service gated on service_completed_successfully stays blocked — the whole stack.
    """
    env = render_compose(postgres_enabled=True)["services"]["db-migrate"]["environment"]
    assert "PGPASSWORD" in env, (
        "db-migrate must expose PGPASSWORD for libpq; " f"got only {sorted(env)}"
    )
    assert env["PGPASSWORD"] == "${PG_PASSWORD}"


def test_db_migrate_command_aborts_on_first_failed_migration(render_compose):
    """ON_ERROR_STOP only ends the failing psql, not the loop around it.

    Without fail-fast in the shell, a failure in any migration but the last is swallowed:
    the loop continues and `bash -c` returns the status of the final iteration, so Compose
    marks db-migrate successful and starts dependents against a half-migrated schema.
    """
    cmd = render_compose(postgres_enabled=True)["services"]["db-migrate"]["command"]
    joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert (
        "ON_ERROR_STOP=1" in joined
    ), "psql must still stop at the first bad statement"
    assert "-e" in cmd or "set -e" in joined, (
        "the shell running the migration loop must abort on the first failure; "
        f"got: {joined}"
    )


def test_db_migrate_command_does_not_parse_ls_output(render_compose):
    """Iterate the glob directly rather than command-substituting `ls`.

    A bare `$(...)` in a Compose command string is also ambiguous with Compose's own
    variable interpolation, which is why the escaped `$$f` sits next to it.
    """
    cmd = render_compose(postgres_enabled=True)["services"]["db-migrate"]["command"]
    joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "ls /migrations" not in joined, f"parses ls output: {joined}"
    assert "/migrations/*.sql" in joined


def test_postgres_healthcheck_probes_tcp_not_the_unix_socket(render_compose):
    """`healthy` must mean "accepting TCP", because that is what dependents use.

    The upstream postgres entrypoint starts a socket-only server with
    `listen_addresses=''` while it processes init files, so a `pg_isready` with no
    `-h` can report ready over the Unix socket before TCP is listening. Every
    dependent here connects over TCP via PGHOST, and db-migrate in particular does
    so with no retry — on a fresh volume whose init.sql outlives the first 10s
    health interval, it would be released early and exit with connection refused,
    blocking everything gated on its successful completion.
    """
    svc = render_compose(postgres_enabled=True)["services"]["postgres"]
    probe = " ".join(svc["healthcheck"]["test"])
    assert "pg_isready" in probe
    assert "-h" in probe, (
        "pg_isready without -h probes the Unix socket, which is ready before TCP is; "
        f"got: {probe}"
    )
