"""templates_manager copies migrations/ into the rendered deployment directory.

Task 2.1: _stage_postgres_init must copy src/cli/templates/migrations/ into
base_dir/migrations/ with the same .sql file set.  A second render into the
same directory must succeed and leave the same set (idempotent).
"""

import json
from pathlib import Path
from types import SimpleNamespace

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape

from src.cli.managers.templates_manager import MIGRATIONS_MANIFEST, TemplateManager

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_DIR = _REPO_ROOT / "src" / "cli" / "templates"
_MIGRATIONS_SRC = _TEMPLATE_DIR / "migrations"


def _env():
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )


def _context(base_dir: Path):
    grafana_svc = SimpleNamespace(enabled=False)
    plan = SimpleNamespace(get_service=lambda name: grafana_svc)
    config_manager = SimpleNamespace(config={"data_manager": {}})
    return SimpleNamespace(
        plan=plan,
        config_manager=config_manager,
        secrets_manager=None,
        base_dir=base_dir,
    )


def _expected_sql_names():
    return {p.name for p in _MIGRATIONS_SRC.glob("*.sql")}


def test_stage_postgres_init_copies_migrations(tmp_path):
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()

    TemplateManager(_env(), verbosity=0)._stage_postgres_init(_context(base_dir))

    migrations_dir = base_dir / "migrations"
    assert migrations_dir.is_dir(), "migrations/ was not staged into the deploy dir"
    staged = {p.name for p in migrations_dir.glob("*.sql")}
    assert staged == _expected_sql_names()


def test_stage_postgres_init_migrations_idempotent(tmp_path):
    """Second render into the same directory leaves the same file set."""
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    ctx = _context(base_dir)

    mgr = TemplateManager(_env(), verbosity=0)
    mgr._stage_postgres_init(ctx)
    mgr._stage_postgres_init(ctx)

    migrations_dir = base_dir / "migrations"
    staged = {p.name for p in migrations_dir.glob("*.sql")}
    assert staged == _expected_sql_names()


def test_a_migration_archi_staged_and_no_longer_packages_is_removed(tmp_path):
    """Staging must SYNCHRONIZE what Archi owns, not merge into it.

    A merge leaves destination-only files in place, and the sidecar globs every
    staged `*.sql` on every startup — so a migration deleted or renamed upstream
    would go on executing forever against a schema its replacement has already
    moved past, with `ON_ERROR_STOP=1` turning any conflict between the two into a
    stack that will not start.

    Provenance is what makes the removal safe, so it is what this asserts: the file
    is recorded in the manifest of what Archi staged, and is gone from the package.
    """
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    ctx = _context(base_dir)
    mgr = TemplateManager(_env(), verbosity=0)

    mgr._stage_postgres_init(ctx)
    # The state left by a previous release that DID package this file.
    obsolete = base_dir / "migrations" / "000_deleted_upstream.sql"
    obsolete.write_text("ALTER TABLE users ADD COLUMN gone TEXT;\n")
    manifest = base_dir / "migrations" / MIGRATIONS_MANIFEST
    manifest.write_text(
        json.dumps(sorted(_expected_sql_names() | {"000_deleted_upstream.sql"}))
    )

    mgr._stage_postgres_init(ctx)

    staged = {p.name for p in (base_dir / "migrations").glob("*.sql")}
    assert staged == _expected_sql_names()
    assert not obsolete.exists()


def test_operator_authored_sql_archi_never_staged_survives(tmp_path):
    """The pruning must not be able to delete work it did not put there.

    An operator-written hotfix or recovery migration is absent from the package by
    definition, so a basename-only rule would remove it on the next routine
    redeploy — before the sidecar ever ran it. Absent from the manifest means
    Archi never staged it, which is the only safe licence to delete.
    """
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    ctx = _context(base_dir)
    mgr = TemplateManager(_env(), verbosity=0)

    mgr._stage_postgres_init(ctx)
    hotfix = base_dir / "migrations" / "zz_operator_hotfix.sql"
    hotfix.write_text("UPDATE users SET api_token_hash = NULL;\n")

    mgr._stage_postgres_init(ctx)

    assert hotfix.exists()
    assert hotfix.read_text() == "UPDATE users SET api_token_hash = NULL;\n"


def test_the_first_render_writes_a_manifest_and_prunes_nothing(tmp_path):
    """Upgrading into a deployment that predates the manifest must not guess.

    With no record of what Archi staged, every destination-only file is of unknown
    provenance, so nothing is removed and the manifest is established for next
    time. One extra run carrying an obsolete migration is the price of never
    deleting an operator's.
    """
    base_dir = tmp_path / "deploy"
    migrations_dir = base_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    unknown = migrations_dir / "000_unknown_provenance.sql"
    unknown.write_text("SELECT 1;\n")

    TemplateManager(_env(), verbosity=0)._stage_postgres_init(_context(base_dir))

    assert unknown.exists()
    recorded = json.loads((migrations_dir / MIGRATIONS_MANIFEST).read_text())
    assert set(recorded) == _expected_sql_names()


def test_a_corrupt_manifest_prunes_nothing(tmp_path):
    """An unreadable record is not a licence to delete."""
    base_dir = tmp_path / "deploy"
    migrations_dir = base_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / MIGRATIONS_MANIFEST).write_text("{not json")
    stray = migrations_dir / "000_stray.sql"
    stray.write_text("SELECT 1;\n")

    TemplateManager(_env(), verbosity=0)._stage_postgres_init(_context(base_dir))

    assert stray.exists()


def test_a_manifest_that_is_valid_json_but_not_a_list_prunes_nothing(tmp_path):
    """Parseable is not the same as usable.

    A JSON object parses fine and would then be membership-tested as a mapping,
    silently answering for keys rather than staged filenames — so it is rejected on
    shape, not merely on parseability.
    """
    base_dir = tmp_path / "deploy"
    migrations_dir = base_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / MIGRATIONS_MANIFEST).write_text(
        json.dumps({"000_stray.sql": True})
    )
    stray = migrations_dir / "000_stray.sql"
    stray.write_text("SELECT 1;\n")

    TemplateManager(_env(), verbosity=0)._stage_postgres_init(_context(base_dir))

    assert stray.exists()


def test_staging_only_prunes_what_the_sidecar_would_execute(tmp_path):
    """Scope the pruning to `*.sql` — the sidecar's own glob.

    Anything else in that directory is not on the migration path, so removing it
    would be a deletion this function has no mandate for.
    """
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    ctx = _context(base_dir)
    mgr = TemplateManager(_env(), verbosity=0)

    mgr._stage_postgres_init(ctx)
    note = base_dir / "migrations" / "NOTES.md"
    note.write_text("why we hand-repaired this schema\n")
    manifest = base_dir / "migrations" / MIGRATIONS_MANIFEST
    manifest.write_text(json.dumps(sorted(_expected_sql_names() | {"NOTES.md"})))

    mgr._stage_postgres_init(ctx)

    assert note.exists()
