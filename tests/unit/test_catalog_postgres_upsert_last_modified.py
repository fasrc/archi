"""
Tests for catalog_postgres.upsert_resource last_modified persistence.

Spec: openspec/changes/fix-issue-155-sitemap-lastmod-persist/specs/incremental-reingest/spec.md
Requirement: The documents catalog persists a last_modified value
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.data_manager.collectors.utils.catalog_postgres import PostgresCatalogService


def _make_service(cursor):
    """Build a PostgresCatalogService with a fully mocked connection."""
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


def test_upsert_resource_with_last_modified_includes_column_in_sql():
    """INSERT must include last_modified column when metadata carries it."""
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    service = _make_service(cursor)

    service.upsert_resource(
        resource_hash="hash1",
        path="/data/page.html",
        metadata={"source_type": "web", "last_modified": "2026-04-21T19:19:35+00:00"},
    )

    sql, _params = cursor.execute.call_args[0]
    assert "last_modified" in sql


def test_upsert_resource_with_last_modified_passes_parsed_value():
    """Parsed datetime for last_modified must appear in the params tuple."""
    cursor = MagicMock()
    cursor.fetchone.return_value = (2,)
    service = _make_service(cursor)

    service.upsert_resource(
        resource_hash="hash2",
        path="/data/page.html",
        metadata={"source_type": "web", "last_modified": "2026-04-21T19:19:35+00:00"},
    )

    _sql, params = cursor.execute.call_args[0]
    expected = datetime(2026, 4, 21, 19, 19, 35, tzinfo=timezone.utc)
    assert expected in params


def test_upsert_resource_conflict_update_includes_last_modified():
    """ON CONFLICT DO UPDATE SET must include last_modified = EXCLUDED.last_modified."""
    cursor = MagicMock()
    cursor.fetchone.return_value = (3,)
    service = _make_service(cursor)

    service.upsert_resource(
        resource_hash="hash3",
        path="/data/page.html",
        metadata={"source_type": "web", "last_modified": "2026-04-21T00:00:00+00:00"},
    )

    sql, _params = cursor.execute.call_args[0]
    assert "last_modified = EXCLUDED.last_modified" in sql


def test_upsert_resource_without_last_modified_no_error():
    """Absent last_modified → no exception; NULL passed for the last_modified slot."""
    cursor = MagicMock()
    cursor.fetchone.return_value = (4,)
    service = _make_service(cursor)

    doc_id = service.upsert_resource(
        resource_hash="hash4",
        path="/data/page.html",
        metadata={"source_type": "web"},
    )

    assert doc_id == 4
    sql, params = cursor.execute.call_args[0]
    assert "last_modified" in sql
    # No datetime values in params since no datetime metadata was provided
    assert not any(isinstance(p, datetime) for p in params)


def test_row_to_metadata_returns_last_modified():
    """Promoting the key to a real column must not hide it from metadata reads.

    `_row_to_metadata` rebuilds metadata from `extra_json` plus an explicit list
    of standard columns. Moving `last_modified` out of `extra_json` and into its
    own column without adding it to that list makes the stored timestamp
    unreadable through every catalog accessor — written, then invisible.
    """
    service = PostgresCatalogService.__new__(PostgresCatalogService)
    row = {
        "resource_hash": "hash5",
        "url": "https://example.org/kb/page",
        "source_type": "web",
        "last_modified": datetime(2026, 4, 21, 19, 19, 35, tzinfo=timezone.utc),
    }

    metadata = service._row_to_metadata(row)

    assert metadata["last_modified"] == "2026-04-21T19:19:35+00:00"


def test_get_metadata_by_filter_keeps_the_last_modified_rows_it_selected():
    """The filter selects on the column, then re-checks the rebuilt metadata.

    So a key missing from `_row_to_metadata` does not merely omit a field — the
    `metadata_field not in metadata` guard drops every row the query matched, and
    the filter reports nothing while the database holds the values.
    """
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {
            "resource_hash": "hash6",
            "url": "https://example.org/kb/page",
            "source_type": "web",
            "last_modified": datetime(2026, 4, 21, 19, 19, 35, tzinfo=timezone.utc),
        }
    ]
    service = _make_service(cursor)

    matches = service.get_metadata_by_filter("last_modified")

    assert [resource_hash for resource_hash, _ in matches] == ["hash6"]
