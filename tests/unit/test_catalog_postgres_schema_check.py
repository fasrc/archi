"""
Tests for PostgresCatalogService schema verification at startup.

Spec: openspec/changes/fix-issue-180-migration-sidecar/specs/schema-migration-provisioning/spec.md
Requirement: The catalog verifies its required columns at startup
"""

import inspect
import re
from contextlib import contextmanager
from unittest.mock import MagicMock, call

import pytest

from src.data_manager.collectors.utils.catalog_postgres import (
    _REQUIRED_DOCUMENT_COLUMNS,
    PostgresCatalogService,
)

# All 18 columns the upsert_resource INSERT writes, excluding last_modified
_ALL_REQUIRED_EXCEPT_LAST_MODIFIED = [
    ("resource_hash",),
    ("file_path",),
    ("display_name",),
    ("source_type",),
    ("url",),
    ("ticket_id",),
    ("suffix",),
    ("size_bytes",),
    ("original_path",),
    ("base_path",),
    ("relative_path",),
    ("file_modified_at",),
    ("ingested_at",),
    ("ingestion_status",),
    ("extra_json",),
    ("extra_text",),
    ("is_deleted",),
]

_ALL_REQUIRED_COLUMNS = _ALL_REQUIRED_EXCEPT_LAST_MODIFIED + [
    ("last_modified",),
    ("deleted_at",),
]


def _schema_check_calls(cursor):
    """Every execute() that was the startup column-existence probe.

    Matched on the relation-resolution helper rather than on a catalog view name, so the
    assertion survives the query being retargeted from information_schema to pg_attribute.
    """
    return [
        c
        for c in cursor.execute.call_args_list
        if "to_regclass" in str(c) or "information_schema" in str(c)
    ]


def _make_service(cursor):
    """Build a PostgresCatalogService with a fully mocked connection (no __init__)."""
    service = PostgresCatalogService.__new__(PostgresCatalogService)
    service._file_index = {}
    service._metadata_index = {}
    service._id_cache = {}
    # Mirrors what __init__ would set; the latch that keeps the startup schema check
    # from repeating on every refresh().
    service._schema_verified = False

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False

    @contextmanager
    def _connect():
        yield conn

    service._connect = _connect
    return service


def test_refresh_raises_runtime_error_when_last_modified_column_missing():
    """refresh() raises RuntimeError naming last_modified when that column is absent."""
    cursor = MagicMock()
    # Schema check returns all required columns except last_modified;
    # RuntimeError is raised before the main refresh SELECT runs.
    cursor.fetchall.side_effect = [_ALL_REQUIRED_EXCEPT_LAST_MODIFIED, []]

    service = _make_service(cursor)

    with pytest.raises(RuntimeError, match="last_modified"):
        service.refresh()


def test_refresh_does_not_raise_when_schema_is_complete():
    """refresh() completes without raising when all required columns are present."""
    cursor = MagicMock()
    cursor.fetchall.side_effect = [_ALL_REQUIRED_COLUMNS, []]

    service = _make_service(cursor)
    service.refresh()  # must not raise


def test_schema_verification_query_issued_once_not_per_upsert():
    """The information_schema check runs once in refresh(), never in upsert_resource()."""
    cursor = MagicMock()
    cursor.fetchall.side_effect = [_ALL_REQUIRED_COLUMNS, []]
    cursor.fetchone.return_value = (1,)

    service = _make_service(cursor)
    service.refresh()

    service.upsert_resource(
        resource_hash="hash1",
        path="/data/page.html",
        metadata={"source_type": "web"},
    )
    service.upsert_resource(
        resource_hash="hash2",
        path="/data/other.html",
        metadata={"source_type": "web"},
    )

    schema_checks = _schema_check_calls(cursor)
    assert len(schema_checks) == 1


def test_schema_verification_runs_once_across_repeated_refreshes():
    """The precondition is an initialization check, not a per-refresh one.

    refresh() is called well beyond startup — after index flushes and deletions, and
    twice consecutively on the ingestion path — so leaving the check inside it repeats
    the catalog scan for the whole life of a long-running data-manager process. The spec
    requires it run once during initialization.
    """
    cursor = MagicMock()
    cursor.fetchall.side_effect = [_ALL_REQUIRED_COLUMNS, [], [], []]

    service = _make_service(cursor)
    service.refresh()
    service.refresh()
    service.refresh()

    assert len(_schema_check_calls(cursor)) == 1


def test_schema_check_resolves_the_target_relation_not_a_bare_table_name():
    """Resolve columns from the relation the INSERT will actually hit.

    An unqualified `information_schema.columns WHERE table_name = 'documents'` unions the
    columns of every visible schema's `documents` table. A complete `archive.documents`
    would then mask a missing column in the one the service's search_path resolves to, so
    startup passes and the real INSERT still fails with UndefinedColumn. `to_regclass`
    resolves through the same search_path the INSERT uses.
    """
    cursor = MagicMock()
    cursor.fetchall.side_effect = [_ALL_REQUIRED_COLUMNS, []]

    service = _make_service(cursor)
    service.refresh()

    sql = str(_schema_check_calls(cursor)[0])
    assert "to_regclass" in sql, f"schema check does not resolve the relation: {sql}"
    assert (
        "information_schema" not in sql
    ), f"schema check still reads the schema-blind catalog view: {sql}"


def _upsert_insert_columns():
    source = inspect.getsource(PostgresCatalogService.upsert_resource)
    match = re.search(r"INSERT INTO documents \((.*?)\)\s*VALUES", source, re.DOTALL)
    assert match, "INSERT INTO documents (...) not found in upsert_resource source"
    return frozenset(col.strip() for col in match.group(1).split(",") if col.strip())


def _upsert_update_columns():
    """Columns assigned in the ON CONFLICT ... DO UPDATE SET branch."""
    source = inspect.getsource(PostgresCatalogService.upsert_resource)
    match = re.search(r"DO UPDATE SET(.*?)RETURNING", source, re.DOTALL)
    assert match, "ON CONFLICT ... DO UPDATE SET not found in upsert_resource source"
    return frozenset(re.findall(r"(\w+)\s*=", match.group(1)))


def test_required_columns_include_the_update_branch_not_just_the_insert():
    """The precondition must cover every column upsert_resource writes on EITHER path.

    Re-ingesting a resource that already exists takes the ON CONFLICT branch, which writes
    columns the INSERT list does not name — `deleted_at = NULL` among them. A `documents`
    table that predates those columns passes a check built from the INSERT list alone, and
    then raises UndefinedColumn on the first re-ingest: exactly the silent per-resource
    failure this precondition exists to prevent.
    """
    update_cols = _upsert_update_columns()
    assert (
        "deleted_at" in update_cols
    ), "fixture drift: the UPDATE branch no longer writes deleted_at"
    missing = update_cols - _REQUIRED_DOCUMENT_COLUMNS
    assert (
        not missing
    ), f"written by the UPDATE branch but unchecked at startup: {sorted(missing)}"


def test_required_document_columns_matches_upsert_statement():
    """_REQUIRED_DOCUMENT_COLUMNS must exactly match what upsert_resource writes.

    If a column is added to or removed from either branch of the upsert without updating
    the constant (or vice versa), this test catches the drift before a deploy would.
    """
    written = _upsert_insert_columns() | _upsert_update_columns()
    assert written == _REQUIRED_DOCUMENT_COLUMNS, (
        "constant drift detected:\n"
        f"  written but not in constant: {sorted(written - _REQUIRED_DOCUMENT_COLUMNS)}\n"
        f"  in constant but never written: {sorted(_REQUIRED_DOCUMENT_COLUMNS - written)}"
    )
