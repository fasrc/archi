"""Parity between fresh-deploy (init.sql) and existing-deploy (migration) schema.

Covers task 4.2 of add-title-aware-retrieval: *verify* that fresh deployments
(provisioned by ``init.sql``) and existing deployments (converted by migration
``0001_weighted_chunk_tsv.sql``) end up with the SAME title-aware full-text
representation.

``init.sql`` only runs on a fresh database volume; existing corpora are upgraded
by the migration. The design requires both to converge: the migration must
recreate exactly the weighted ``chunk_tsv`` / ``chunk_search_text`` generated
columns and indexes that fresh deployments already get from ``init.sql``. If the
two ever drift, a migrated corpus would rank title/filename matches differently
from a freshly ingested one. These tests pin that convergence.

Both files embed the DDL inside PL/pgSQL ``EXECUTE '...'`` literals, so doubled
single quotes are collapsed before comparison, and surrounding whitespace is
normalized so formatting differences alone do not register as drift.
"""

import re
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src/cli/templates"
MIGRATION_PATH = TEMPLATES_DIR / "migrations/0001_weighted_chunk_tsv.sql"


def _render_init_sql(**overrides) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=ChainableUndefined,
    )
    context = {
        "use_grafana": False,
        "grafana_pg_password": "",
        "embedding_dimensions": 384,
        "vector_index_type": "hnsw",
        "vector_index_hnsw_m": 16,
        "vector_index_hnsw_ef": 64,
    }
    context.update(overrides)
    return env.get_template("init.sql").render(**context)


def _migration_sql() -> str:
    return MIGRATION_PATH.read_text()


def _collapse(fragment: str) -> str:
    """Collapse doubled single quotes and runs of whitespace for comparison."""
    return re.sub(r"\s+", " ", fragment.replace("''", "'")).strip()


def _generated_column(sql: str, column: str, col_type: str) -> str:
    match = re.search(
        rf"{column} {col_type}\s+GENERATED ALWAYS AS \((.*?)\) STORED",
        sql,
        re.DOTALL,
    )
    assert match is not None, f"{column} generated column not found"
    return _collapse(match.group(1))


# --- GIN fallback path: chunk_tsv weighted tsvector ------------------------


def test_chunk_tsv_definition_matches_across_init_and_migration():
    init_def = _generated_column(_render_init_sql(), "chunk_tsv", "tsvector")
    migration_def = _generated_column(_migration_sql(), "chunk_tsv", "tsvector")
    assert init_def == migration_def


def test_gin_index_matches_across_init_and_migration():
    expected = (
        "CREATE INDEX IF NOT EXISTS idx_chunks_fts "
        "ON document_chunks USING gin(chunk_tsv)"
    )
    assert expected in _collapse(_render_init_sql())
    assert expected in _collapse(_migration_sql())


# --- BM25 path: chunk_search_text weighted text ----------------------------


def test_chunk_search_text_definition_matches_across_init_and_migration():
    init_def = _generated_column(_render_init_sql(), "chunk_search_text", "TEXT")
    migration_def = _generated_column(_migration_sql(), "chunk_search_text", "TEXT")
    assert init_def == migration_def


def test_bm25_index_matches_across_init_and_migration():
    expected = "USING bm25(chunk_search_text) WITH (text_config='english')"
    assert expected in _collapse(_render_init_sql())
    assert expected in _collapse(_migration_sql())


# --- Both paths are gated on the same extension probe -----------------------


def test_both_gate_full_text_path_on_pg_textsearch_extension():
    probe = "pg_extension WHERE extname = 'pg_textsearch'"
    assert probe in _render_init_sql()
    assert probe in _migration_sql()
