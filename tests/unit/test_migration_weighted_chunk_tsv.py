"""Tests for the idempotent weighted full-text migration.

Covers task 2.3 of add-title-aware-retrieval: an idempotent migration that
converts EXISTING ``document_chunks`` tables to the weighted title/filename
full-text representation that fresh deployments get from ``init.sql``.

init.sql only runs on a fresh database volume, so existing corpora keep the old
unweighted ``to_tsvector('english', chunk_text)`` column until this migration is
applied. The migration must:

  * detect a stale (unweighted) generated column and drop it so the weighted
    definition is recreated -- without disturbing an already-weighted column;
  * recreate the same weighted ``chunk_tsv`` / ``chunk_search_text`` definitions
    and indexes as init.sql, conditioned on the ``pg_textsearch`` extension;
  * be safe to re-run (idempotent).

The migration is pure SQL with no Jinja variables, so the assertions read the
raw file and collapse the doubled single quotes that appear inside PL/pgSQL
``EXECUTE '...'`` string literals.
"""

import re
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "src/cli/templates/migrations/0001_weighted_chunk_tsv.sql"
)


def _migration_sql() -> str:
    return MIGRATION_PATH.read_text()


def _normalized(sql: str) -> str:
    """Collapse the doubled single quotes used inside EXECUTE '...' literals."""
    return sql.replace("''", "'")


def _chunk_tsv_definition(sql: str) -> str:
    match = re.search(
        r"chunk_tsv tsvector\s+GENERATED ALWAYS AS \((.*?)\) STORED",
        sql,
        re.DOTALL,
    )
    assert match is not None, "chunk_tsv generated column not found in migration"
    return match.group(1).replace("''", "'")


def _chunk_search_text_definition(sql: str) -> str:
    match = re.search(
        r"chunk_search_text TEXT\s+GENERATED ALWAYS AS \((.*?)\) STORED",
        sql,
        re.DOTALL,
    )
    assert (
        match is not None
    ), "chunk_search_text generated column not found in migration"
    return match.group(1).replace("''", "'")


def test_migration_file_exists():
    assert MIGRATION_PATH.is_file()


def test_migration_conditioned_on_pg_textsearch_extension():
    # Mirrors init.sql: BM25 when pg_textsearch is present, weighted GIN otherwise.
    sql = _migration_sql()
    assert "pg_extension WHERE extname = 'pg_textsearch'" in sql


# --- GIN fallback path (weighted tsvector) ---------------------------------


def test_gin_chunk_tsv_uses_weighted_setweight_expression():
    fragment = _chunk_tsv_definition(_migration_sql())
    assert fragment.count("setweight") == 3
    assert "||" in fragment


def test_gin_display_name_and_filename_weighted_a():
    fragment = _chunk_tsv_definition(_migration_sql())
    assert (
        "setweight(to_tsvector('english', coalesce(metadata->>'display_name', '')), 'A')"
        in fragment
    )
    assert (
        "setweight(to_tsvector('english', coalesce(metadata->>'filename', '')), 'A')"
        in fragment
    )


def test_gin_chunk_text_weighted_b():
    fragment = _chunk_tsv_definition(_migration_sql())
    assert "setweight(to_tsvector('english', chunk_text), 'B')" in fragment


def test_gin_index_recreated_over_chunk_tsv():
    sql = _normalized(_migration_sql())
    assert (
        "CREATE INDEX IF NOT EXISTS idx_chunks_fts ON document_chunks USING gin(chunk_tsv)"
        in sql
    )


# --- BM25 path (pg_textsearch) ---------------------------------------------


def test_bm25_search_text_repeats_title_filename_for_weight():
    fragment = _chunk_search_text_definition(_migration_sql())
    assert fragment.count("metadata->>'display_name'") == 2
    assert fragment.count("metadata->>'filename'") == 2
    assert "chunk_text" in fragment


def test_bm25_index_recreated_over_search_text():
    sql = _normalized(_migration_sql())
    assert "USING bm25(chunk_search_text)" in sql
    assert "USING bm25(chunk_text)" not in sql


# --- Idempotency / migration semantics -------------------------------------


def test_migration_detects_stale_column_via_generation_expression():
    # Staleness is detected from the column's generation expression so that an
    # already-weighted column is left alone (idempotent), and only a legacy
    # unweighted definition is dropped and recreated.
    sql = _migration_sql()
    assert "pg_get_expr(d.adbin, d.adrelid) NOT ILIKE '%setweight%'" in sql
    assert "pg_get_expr(d.adbin, d.adrelid) NOT ILIKE '%display_name%'" in sql


def test_migration_drops_stale_columns_before_recreating():
    sql = _migration_sql()
    assert "ALTER TABLE document_chunks DROP COLUMN chunk_tsv" in sql
    assert "ALTER TABLE document_chunks DROP COLUMN chunk_search_text" in sql


def test_migration_recreates_columns_idempotently():
    # ADD COLUMN IF NOT EXISTS makes the recreate safe when the column is already
    # current (the stale-detection drop is skipped, so the column still exists).
    sql = _migration_sql()
    assert "ADD COLUMN IF NOT EXISTS chunk_tsv" in sql
    assert "ADD COLUMN IF NOT EXISTS chunk_search_text" in sql


def test_migration_targets_document_chunks_table():
    sql = _migration_sql()
    assert "'document_chunks'::regclass" in sql
