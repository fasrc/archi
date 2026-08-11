"""templates_manager copies migrations/ into the rendered deployment directory.

Task 2.1: _stage_postgres_init must copy src/cli/templates/migrations/ into
base_dir/migrations/ with the same .sql file set.  A second render into the
same directory must succeed and leave the same set (idempotent).
"""

from pathlib import Path
from types import SimpleNamespace

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape

from src.cli.managers.templates_manager import TemplateManager

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
