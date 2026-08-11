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
