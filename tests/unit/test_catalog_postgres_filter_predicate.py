"""`get_metadata_by_filter` must not apply a text predicate to a non-text column.

Spec: openspec/changes/fix-issue-155-sitemap-lastmod-persist/specs/incremental-reingest/spec.md
Requirement: A value-less filter on a non-text column uses the NULL check alone

Calling `get_metadata_by_filter("last_modified")` with no value takes the "has a
value" branch, which built `col IS NOT NULL AND col != ''`. On a TIMESTAMPTZ column
PostgreSQL has to cast `''` to a timestamp to evaluate that, raises
`InvalidDatetimeFormat`, and fails the whole query — so the filter does not return a
wrong set, it returns nothing and errors.

`last_modified` is new in this change, but the same predicate was already reachable
for `created_at`, `ingested_at`, `modified_at`/`file_modified_at` (all TIMESTAMPTZ)
and `size_bytes` (BIGINT), so the fix is by column type rather than by field name.

These tests read the SQL handed to the cursor. That is the level the bug lives at:
the query is malformed before the database sees it, and asserting on rows would need
a real PostgreSQL to reproduce a failure that is already visible in the string.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from src.data_manager.collectors.utils.catalog_postgres import (
    _METADATA_COLUMN_MAP,
    _NON_TEXT_COLUMNS,
    PostgresCatalogService,
)


def _make_service(cursor):
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


def _sql_for(metadata_field):
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    _make_service(cursor).get_metadata_by_filter(metadata_field)
    return cursor.execute.call_args[0][0]


@pytest.mark.parametrize(
    "metadata_field, column",
    [
        ("last_modified", "last_modified"),
        ("created_at", "created_at"),
        ("ingested_at", "ingested_at"),
        ("modified_at", "file_modified_at"),
        ("file_modified_at", "file_modified_at"),
        ("size_bytes", "size_bytes"),
    ],
)
def test_non_text_columns_use_only_the_null_check(metadata_field, column):
    sql = _sql_for(metadata_field)

    assert f"{column} IS NOT NULL" in sql
    # The cast that raises. Its absence is the entire fix.
    assert "!= ''" not in sql


@pytest.mark.parametrize(
    "metadata_field, column",
    [("url", "url"), ("path", "file_path"), ("source_type", "source_type")],
)
def test_text_columns_keep_the_emptiness_check(metadata_field, column):
    """The fix must not widen to text columns.

    Dropping `!= ''` everywhere would silently start matching rows whose text
    column is present but blank — a behaviour change disguised as a bug fix, and
    one no error would announce.
    """
    sql = _sql_for(metadata_field)

    assert f"{column} IS NOT NULL" in sql
    assert f"{column} != ''" in sql


def test_every_non_text_column_reachable_through_the_map_is_guarded():
    """Guard the guard: a mapped column of non-text type must be in the set.

    Written against the map rather than a hand-listed set of fields, so adding a
    key that points at a timestamp column fails here rather than in production.
    """
    documents_non_text = {
        "size_bytes",
        "file_modified_at",
        "last_modified",
        "ingested_at",
        "indexed_at",
        "created_at",
    }
    reachable = set(_METADATA_COLUMN_MAP.values())

    assert (documents_non_text & reachable) <= _NON_TEXT_COLUMNS
