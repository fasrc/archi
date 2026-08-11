"""Assert every migration .sql file contains no bare ALTER TABLE RENAME COLUMN."""

import re
from pathlib import Path

import pytest

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "cli" / "templates" / "migrations"
)

_DO_BLOCK_RE = re.compile(r"DO\s+\$\$.*?END\s+\$\$\s*;", re.DOTALL | re.IGNORECASE)
_BARE_RENAME_RE = re.compile(r"ALTER\s+TABLE\s+\S+\s+RENAME\s+COLUMN", re.IGNORECASE)


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
