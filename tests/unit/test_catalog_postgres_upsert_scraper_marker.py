"""Tests for the scraper marker in catalog_postgres.upsert_resource (#640).

Spec: openspec/changes/fix-issue-640-scraper-marker-survives-refetch/specs/scraper-source-marker/spec.md
Requirement: The catalog upsert SHALL keep a stored scraper marker when the incoming
metadata has none.

The unit suite cannot run SQL, so these tests pin the conflict clause text (design D2).
The behavior of that exact text was proven against a throwaway Postgres (design,
Appendix A). Do not change the clause without a new proof.
"""

import json
import re
from contextlib import contextmanager
from unittest.mock import MagicMock

from src.data_manager.collectors.utils.catalog_postgres import PostgresCatalogService

_MARKER_CLAUSE = """
    extra_json = CASE
        WHEN COALESCE(jsonb_typeof(EXCLUDED.extra_json -> 'scraper'), 'null') = 'null'
            AND COALESCE(jsonb_typeof(documents.extra_json -> 'scraper'), 'null') <> 'null'
        THEN COALESCE(EXCLUDED.extra_json, '{}'::jsonb)
            || jsonb_build_object('scraper', documents.extra_json -> 'scraper')
        ELSE EXCLUDED.extra_json
    END,
"""


def _normalized(text):
    return " ".join(text.split())


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


def _upsert_sql_and_params(metadata):
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    service = _make_service(cursor)
    service.upsert_resource(
        resource_hash="hash1", path="/data/page.html", metadata=metadata
    )
    sql, params = cursor.execute.call_args[0]
    return sql, params


def test_conflict_clause_keeps_a_stored_scraper_marker():
    """A link re-fetch upserts the same hash with no marker; the stored one stays."""
    sql, _params = _upsert_sql_and_params({"source_type": "web", "url": "https://x/a"})
    assert "ON CONFLICT" in sql, "the upsert must still be a conflict-update"
    conflict = sql.split("ON CONFLICT", 1)[1]
    assert _normalized(_MARKER_CLAUSE) in _normalized(conflict), (
        "the conflict clause must keep a stored scraper marker (design D1); "
        f"got {conflict!r}"
    )


def test_conflict_clause_has_no_unconditional_extra_json_assignment():
    """The old form replaced the whole blob and erased the marker (#640)."""
    sql, _params = _upsert_sql_and_params({"source_type": "web", "url": "https://x/a"})
    conflict = sql.split("ON CONFLICT", 1)[1]
    assert not re.search(r"extra_json\s*=\s*EXCLUDED\.extra_json\s*,", conflict), (
        "the unconditional form erases the scraper marker on every link re-fetch; "
        f"got {conflict!r}"
    )


def test_insert_binds_the_incoming_extra_json_unchanged():
    """The marker logic is SQL-only: Python still binds the incoming extras as is."""
    _sql, params = _upsert_sql_and_params(
        {"source_type": "web", "url": "https://x/a", "title": "plain"}
    )
    assert json.dumps({"title": "plain"}, sort_keys=True) in params
