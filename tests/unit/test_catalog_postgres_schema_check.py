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

_ALL_REQUIRED_COLUMNS = _ALL_REQUIRED_EXCEPT_LAST_MODIFIED + [("last_modified",)]


def _make_service(cursor):
    """Build a PostgresCatalogService with a fully mocked connection (no __init__)."""
    service = PostgresCatalogService.__new__(PostgresCatalogService)
    service._file_index = {}
    service._metadata_index = {}
    service._id_cache = {}

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

    schema_checks = [
        c for c in cursor.execute.call_args_list if "information_schema" in str(c)
    ]
    assert len(schema_checks) == 1


def test_required_document_columns_matches_insert_statement():
    """_REQUIRED_DOCUMENT_COLUMNS must exactly match the INSERT INTO documents column list.

    If a column is added to or removed from the INSERT without updating the
    constant (or vice versa), this test catches the drift before a deploy would.
    """
    source = inspect.getsource(PostgresCatalogService.upsert_resource)
    match = re.search(r"INSERT INTO documents \((.*?)\)\s*VALUES", source, re.DOTALL)
    assert match, "INSERT INTO documents (...) not found in upsert_resource source"
    insert_cols = frozenset(
        col.strip() for col in match.group(1).split(",") if col.strip()
    )
    assert insert_cols == _REQUIRED_DOCUMENT_COLUMNS, (
        "constant drift detected:\n"
        f"  in INSERT but not in constant: {sorted(insert_cols - _REQUIRED_DOCUMENT_COLUMNS)}\n"
        f"  in constant but not in INSERT: {sorted(_REQUIRED_DOCUMENT_COLUMNS - insert_cols)}"
    )
