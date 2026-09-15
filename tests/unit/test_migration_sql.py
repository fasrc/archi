"""Assert migration .sql files are safe: no bare RENAME COLUMN, all statements idempotent."""

import re
from pathlib import Path

import pytest

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "cli" / "templates" / "migrations"
)

_DO_BLOCK_RE = re.compile(r"DO\s+\$\$.*?END\s+\$\$\s*;", re.DOTALL | re.IGNORECASE)
_BARE_RENAME_RE = re.compile(r"ALTER\s+TABLE\s+\S+\s+RENAME\s+COLUMN", re.IGNORECASE)
_COMMENT_RE = re.compile(r"--[^\n]*", re.MULTILINE)
_IDEMPOTENT_KEYWORD_RE = re.compile(r"\bIF\s+(NOT\s+)?EXISTS\b", re.IGNORECASE)


def _bare_rename_violations(path: Path) -> list[str]:
    """Return a list of violation messages for bare RENAME COLUMN in *path*."""
    content = path.read_text()
    stripped = _DO_BLOCK_RE.sub("", content)
    if _BARE_RENAME_RE.search(stripped):
        return [f"{path.name}: bare ALTER TABLE RENAME COLUMN outside guarded DO block"]
    return []


def test_no_bare_rename_column_in_migrations():
    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    assert sql_files, f"No .sql files found in {_MIGRATIONS_DIR}"

    violations: list[str] = []
    for path in sql_files:
        violations.extend(_bare_rename_violations(path))

    assert not violations, (
        "Every ALTER TABLE RENAME COLUMN must be guarded by a DO $$ ... END $$ block "
        "that checks information_schema.columns:\n" + "\n".join(violations)
    )


_BARE_RENAME_INDEX_RE = re.compile(r"ALTER\s+INDEX\b.*?\bRENAME\s+TO", re.IGNORECASE)


def _bare_rename_index_violations(path: Path) -> list[str]:
    """Return violation messages for ALTER INDEX ... RENAME TO outside a DO block."""
    content = path.read_text()
    stripped = _DO_BLOCK_RE.sub("", content)
    if _BARE_RENAME_INDEX_RE.search(stripped):
        return [f"{path.name}: bare ALTER INDEX RENAME TO outside guarded DO block"]
    return []


def test_no_bare_rename_index_in_migrations():
    """A rename needs a guard on BOTH sides, which `IF EXISTS` does not give it.

    `ALTER INDEX IF EXISTS a RENAME TO b` covers only "a is gone". It says nothing
    about b, so on a half-migrated or hand-repaired schema carrying both names it
    raises `relation "b" already exists`. Under the sidecar's `ON_ERROR_STOP=1` plus
    `set -e` that aborts the whole file and fails `db-migrate`, and because
    `config-seed` and the data manager gate on its successful completion, the stack
    does not start.

    `test_every_migration_statement_is_idempotent` cannot catch this: it greps for
    the literal `IF EXISTS`, which the statement contains. Renames therefore get
    their own check, matching how every RENAME COLUMN here is already guarded.
    """
    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    assert sql_files, f"No .sql files found in {_MIGRATIONS_DIR}"

    violations: list[str] = []
    for path in sql_files:
        violations.extend(_bare_rename_index_violations(path))

    assert not violations, (
        "Every ALTER INDEX ... RENAME TO must sit inside a DO $$ ... END $$ block "
        "that checks the source index exists AND the target does not:\n"
        + "\n".join(violations)
    )


_UNQUALIFIED_INFO_SCHEMA_RE = re.compile(
    r"information_schema\.columns\b(?![\s\S]{0,400}?table_schema)", re.IGNORECASE
)


def test_relation_guards_are_not_ambiguous_across_schemas():
    """A guard must inspect the relation the statement will actually alter.

    `WHERE table_name = 'feedback'` matches that name in EVERY schema the role can
    see, so a same-named table elsewhere makes the predicate report on the wrong
    relation -- skipping a rename that is still needed, or attempting one that is
    not. `ALTER TABLE feedback` meanwhile resolves through `search_path`, so guard
    and statement can disagree about which table they mean.

    Either fix is accepted: constrain `table_schema`, or resolve the relation
    directly with `to_regclass` (which is what `_SCHEMA_CHECK_SQL` in
    `catalog_postgres.py` already does, so this keeps both halves consistent).
    """
    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    assert sql_files, f"No .sql files found in {_MIGRATIONS_DIR}"

    violations: list[str] = []
    for path in sql_files:
        if _UNQUALIFIED_INFO_SCHEMA_RE.search(_COMMENT_RE.sub("", path.read_text())):
            violations.append(
                f"{path.name}: information_schema.columns queried without a "
                "table_schema constraint"
            )

    assert not violations, (
        "Relation guards must not match a bare table_name across every visible "
        "schema:\n" + "\n".join(violations)
    )


def _non_idempotent_violations(path: Path) -> list[str]:
    """Return violation messages for statements not guarded by IF EXISTS / IF NOT EXISTS."""
    content = path.read_text()
    without_do = _DO_BLOCK_RE.sub("", content)
    without_comments = _COMMENT_RE.sub("", without_do)
    violations = []
    for stmt in without_comments.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        if not _IDEMPOTENT_KEYWORD_RE.search(stmt):
            violations.append(f"{path.name}: non-idempotent statement: {stmt[:120]!r}")
    return violations


_DROP_INDEX_RE = re.compile(r"DROP\s+INDEX\b", re.IGNORECASE)


def _unguarded_drop_index_violations(path: Path) -> list[str]:
    """Return violations for a DROP INDEX outside a guarded DO block."""
    content = _COMMENT_RE.sub("", _DO_BLOCK_RE.sub("", path.read_text()))
    if _DROP_INDEX_RE.search(content):
        return [f"{path.name}: DROP INDEX outside a guarded DO block"]
    return []


def test_no_unguarded_drop_index_in_migrations():
    """`DROP INDEX IF EXISTS` is re-runnable but it is not a no-op.

    The sidecar replays every file on every startup, so an unconditional drop
    followed by a create rebuilds the index on each boot: a full scan and sort of
    the table on the critical path, before any application service is allowed to
    start. Worse than the cost, psql runs each statement in its own transaction, so
    the drop commits and there is a window in which the uniqueness constraint does
    not exist — and if a duplicate lands in it, the CREATE fails, and under
    `ON_ERROR_STOP=1` plus `set -e` that keeps the whole stack down.

    `IF EXISTS` satisfies the keyword check in
    ``test_every_migration_statement_is_idempotent``, which is why this needs its
    own assertion: the requirement is that a re-run *changes nothing*, so the drop
    has to be guarded on the existing index's definition.
    """
    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    assert sql_files, f"No .sql files found in {_MIGRATIONS_DIR}"

    violations: list[str] = []
    for path in sql_files:
        violations.extend(_unguarded_drop_index_violations(path))

    assert not violations, (
        "A DROP INDEX must sit inside a DO $$ ... END $$ block that first checks "
        "the existing index's definition, so re-running the migration against an "
        "already-current schema does not rebuild the index:\n" + "\n".join(violations)
    )


def test_every_migration_statement_is_idempotent():
    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    assert sql_files, f"No .sql files found in {_MIGRATIONS_DIR}"

    violations: list[str] = []
    for path in sql_files:
        violations.extend(_non_idempotent_violations(path))

    assert not violations, (
        "Every migration statement must use IF EXISTS / IF NOT EXISTS or be "
        "inside a guarded DO $$ ... END $$ block:\n" + "\n".join(violations)
    )
