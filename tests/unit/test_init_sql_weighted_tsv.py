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
