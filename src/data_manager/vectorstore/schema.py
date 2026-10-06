"""Runtime schema-ensure helpers for the hierarchical retrieval feature.

``init.sql`` creates ``document_parent_nodes`` (and its index) only when Postgres
initializes a *fresh* data directory. A deployment upgraded on a pre-existing
volume never re-runs ``init.sql``, so the hierarchical write/read paths would hit
an undefined-table error. This module provides an idempotent ensure step —
mirroring the runtime ``CREATE TABLE IF NOT EXISTS`` pattern in
``collectors/utils/index_utils.py`` — invoked before the hierarchical path writes
or reads the table. The DDL here mirrors ``src/cli/templates/init.sql`` so a table
created at runtime is identical to one created by ``init.sql``.
"""

from __future__ import annotations

# Keep these statements byte-for-byte aligned with the corresponding block in
# ``src/cli/templates/init.sql`` so the runtime-created table matches the one a
# fresh Postgres volume would build.
_CREATE_PARENT_NODES_TABLE = """
CREATE TABLE IF NOT EXISTS document_parent_nodes (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    parent_index INTEGER NOT NULL,
    parent_text TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_CREATE_PARENT_NODES_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_parent_nodes_document "
    "ON document_parent_nodes(document_id)"
)

# Mirrors init.sql:346-347. Without this index each NOT EXISTS in
# DELETE_UNREFERENCED_PARENTS_FOR_* would scan all of document_chunks.
_CREATE_CHUNKS_PARENT_ID_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_chunks_parent_id "
    "ON document_chunks ((metadata->>'parent_id'))"
)


def ensure_hierarchical_schema(cursor) -> None:
    """Idempotently create ``document_parent_nodes`` and its index if absent.

    Executes ``CREATE TABLE IF NOT EXISTS`` followed by ``CREATE INDEX IF NOT
    EXISTS`` on the given DB-API ``cursor``. Because both statements use ``IF NOT
    EXISTS``, the call is a no-op (no error, no row changes) when the table and
    index already exist, and creates them when they do not. The caller owns the
    surrounding transaction/commit.

    The chat retrieval path calls this without committing, so it must stay
    cheap: the ``idx_chunks_parent_id`` build lives in
    ``ensure_chunks_parent_id_index``, which only committed write paths call.
    """
    cursor.execute(_CREATE_PARENT_NODES_TABLE)
    cursor.execute(_CREATE_PARENT_NODES_INDEX)


def ensure_chunks_parent_id_index(cursor) -> None:
    """Idempotently create the ``idx_chunks_parent_id`` expression index.

    Mirrors ``init.sql:346-347`` for volumes that predate it, so the ``NOT
    EXISTS`` predicate in the parent-delete helpers uses the index instead of a
    full scan of ``document_chunks``. Call it only from a path that commits;
    a rolled-back ``CREATE INDEX`` is rebuilt on the next call.
    """
    cursor.execute(_CREATE_CHUNKS_PARENT_ID_INDEX)
