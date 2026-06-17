"""Tests for the weighted full-text generated column in init.sql.

Covers task 2.1 of add-title-aware-retrieval: the fallback ``chunk_tsv``
generated column must be a weighted ``tsvector`` giving ``display_name`` and
``filename`` weight ``A`` and ``chunk_text`` weight ``B`` so that title/filename
matches outrank body-only matches in full-text ranking.
"""

import re
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src/cli/templates"


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


def _chunk_tsv_definition(sql: str) -> str:
    """Return the ``chunk_tsv`` generated-column DDL fragment, quotes normalized.

    The DDL lives inside a PL/pgSQL ``EXECUTE '...'`` string literal where every
    single quote is doubled; collapse them so the assertions read naturally.
    """
    match = re.search(
        r"chunk_tsv tsvector\s+GENERATED ALWAYS AS \((.*?)\) STORED",
        sql,
        re.DOTALL,
    )
    assert match is not None, "chunk_tsv generated column not found in init.sql"
    return match.group(1).replace("''", "'")


def test_chunk_tsv_uses_setweight_expression():
    fragment = _chunk_tsv_definition(_render_init_sql())
    # No longer a bare to_tsvector over only chunk_text.
    assert "setweight" in fragment


def test_display_name_and_filename_weighted_a():
    fragment = _chunk_tsv_definition(_render_init_sql())
    assert (
        "setweight(to_tsvector('english', coalesce(metadata->>'display_name', '')), 'A')"
        in fragment
    )
    assert (
        "setweight(to_tsvector('english', coalesce(metadata->>'filename', '')), 'A')"
        in fragment
    )


def test_chunk_text_weighted_b():
    fragment = _chunk_tsv_definition(_render_init_sql())
    assert "setweight(to_tsvector('english', chunk_text), 'B')" in fragment


def test_weighted_components_are_concatenated():
    fragment = _chunk_tsv_definition(_render_init_sql())
    # Three setweight() calls combined into one tsvector.
    assert fragment.count("setweight") == 3
    assert "||" in fragment


def test_fts_gin_index_built_over_chunk_tsv():
    sql = _render_init_sql()
    assert (
        "CREATE INDEX IF NOT EXISTS idx_chunks_fts ON document_chunks USING gin(chunk_tsv)"
        in sql
    )


def _chunk_search_text_definition(sql: str) -> str:
    """Return the ``chunk_search_text`` generated-column DDL, quotes normalized.

    Like ``_chunk_tsv_definition`` this fragment lives inside a PL/pgSQL
    ``EXECUTE '...'`` literal, so doubled single quotes are collapsed.
    """
    match = re.search(
        r"chunk_search_text TEXT\s+GENERATED ALWAYS AS \((.*?)\) STORED",
        sql,
        re.DOTALL,
    )
    assert match is not None, "chunk_search_text generated column not found in init.sql"
    return match.group(1).replace("''", "'")


def test_bm25_indexes_weighted_search_text_column():
    # Task 2.2: the pg_textsearch BM25 index is rebuilt over the weighted
    # chunk_search_text column rather than the bare chunk_text body.
    sql = _render_init_sql()
    assert "USING bm25(chunk_search_text)" in sql
    assert "USING bm25(chunk_text)" not in sql


def test_bm25_search_text_includes_title_and_filename():
    fragment = _chunk_search_text_definition(_render_init_sql())
    assert "coalesce(metadata->>'display_name', '')" in fragment
    assert "coalesce(metadata->>'filename', '')" in fragment
    assert "chunk_text" in fragment


def test_bm25_search_text_repeats_title_filename_for_weight():
    # BM25 has no per-field setweight, so title/filename are repeated to carry a
    # higher term frequency (weight) than body-only mentions.
    fragment = _chunk_search_text_definition(_render_init_sql())
    assert fragment.count("metadata->>'display_name'") == 2
    assert fragment.count("metadata->>'filename'") == 2
