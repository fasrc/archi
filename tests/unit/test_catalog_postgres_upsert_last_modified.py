"""
Tests for catalog_postgres.upsert_resource last_modified persistence.

Spec: openspec/changes/fix-issue-155-sitemap-lastmod-persist/specs/incremental-reingest/spec.md
Requirement: The documents catalog persists a last_modified value
"""

import re
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.data_manager.collectors.utils.catalog_postgres import PostgresCatalogService


def _param_for_column(sql, params, column):
    """Return the bound parameter that lands in *column*'s slot.

    ``assert <value> in params`` is positionally blind: the INSERT binds many
    nullable columns, so ``None in params`` holds whenever *any* of them is
    NULL, and ``expected in params`` holds wherever the value landed.  Both
    pass even when the ``last_modified`` slot is wrong — swapping the
    ``last_modified`` and ``ingested_at`` bindings leaves every assertion in
    this file green.  Pairing the INSERT column list with its VALUES list
    instead lets each assertion name the column it actually means.
    """
    match = re.search(
        r"INSERT INTO documents\s*\((.*?)\)\s*VALUES\s*\((.*?)\)", sql, re.S
    )
    assert match, "could not locate the INSERT column/VALUES lists in the SQL"

    columns = [c.strip() for c in match.group(1).split(",")]
    values = [v.strip() for v in match.group(2).split(",")]
    assert len(columns) == len(
        values
    ), f"INSERT lists disagree: {len(columns)} columns vs {len(values)} values"

    bound = {}
    next_param = 0
    for col, value in zip(columns, values):
        if value == "%s":
            bound[col] = params[next_param]
            next_param += 1
        else:
            bound[col] = value  # SQL literal, not a bound parameter
    assert next_param == len(
        params
    ), f"{len(params)} params supplied but {next_param} placeholders in the SQL"
    assert column in bound, f"{column!r} is not an INSERT column"
    return bound[column]


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

    sql, params = cursor.execute.call_args[0]
    expected = datetime(2026, 4, 21, 19, 19, 35, tzinfo=timezone.utc)
    assert _param_for_column(sql, params, "last_modified") == expected


def test_upsert_resource_conflict_update_includes_last_modified():
    """ON CONFLICT DO UPDATE SET must use COALESCE to preserve a stored value when absent.

    The clause must be
    ``last_modified = COALESCE(EXCLUDED.last_modified, documents.last_modified)``
    so that an incoming NULL (no new information) leaves an existing stored
    timestamp unchanged rather than overwriting it.
    """
    cursor = MagicMock()
    cursor.fetchone.return_value = (3,)
    service = _make_service(cursor)

    service.upsert_resource(
        resource_hash="hash3",
        path="/data/page.html",
        metadata={"source_type": "web", "last_modified": "2026-04-21T00:00:00+00:00"},
    )

    sql, _params = cursor.execute.call_args[0]
    assert (
        "last_modified = COALESCE(EXCLUDED.last_modified, documents.last_modified)"
        in sql
    )


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
    assert _param_for_column(sql, params, "last_modified") is None


def test_upsert_resource_without_last_modified_uses_coalesce_and_passes_none():
    """Re-upsert without last_modified emits COALESCE clause AND passes None as the param.

    Both conditions must hold together: the clause must be the COALESCE form so
    the database decides (not Python omitting a parameter), and the param must be
    None (NULL) so an absent timestamp is explicitly represented rather than
    silently dropped.  This proves preservation is decided in SQL (design D5).
    """
    cursor = MagicMock()
    cursor.fetchone.return_value = (7,)
    service = _make_service(cursor)

    service.upsert_resource(
        resource_hash="hash7",
        path="/data/page.html",
        metadata={"source_type": "web"},
    )

    sql, params = cursor.execute.call_args[0]
    assert (
        "last_modified = COALESCE(EXCLUDED.last_modified, documents.last_modified)"
        in sql
    )
    assert _param_for_column(sql, params, "last_modified") is None


def test_upsert_resource_older_last_modified_still_overwrites():
    """A supplied last_modified replaces the stored one even when it is older.

    COALESCE must not be mistaken for "keep the newest": it only activates when
    the incoming value is NULL.  A non-NULL incoming timestamp — even an older
    one — must be passed through and will overwrite the stored value.
    """
    cursor = MagicMock()
    cursor.fetchone.return_value = (8,)
    service = _make_service(cursor)

    older_ts = "2020-01-01T00:00:00+00:00"
    service.upsert_resource(
        resource_hash="hash8",
        path="/data/page.html",
        metadata={"source_type": "web", "last_modified": older_ts},
    )

    sql, params = cursor.execute.call_args[0]
    expected = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert _param_for_column(sql, params, "last_modified") == expected


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
