"""Ingest-run provenance: which run built the corpus, under which configuration.

One row per completed ingest run. The row carries the run window, the resulting
counts, and a snapshot of the ingest-affecting configuration taken from the
config that governed the run (see :mod:`src.utils.ingest_provenance`).

Why the snapshot rather than reading config back later: ``static_config`` only
ever holds *current* configuration, so a redeploy that flips a flag without
re-ingesting would otherwise let the status board claim the corpus was built
with a setting it was not built with.

Like the deployment record, this is provenance, not product: every failure is
swallowed and logged so an ingest can never fail because its commentary could
not be stored.
"""

import json
import logging
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

# Mirrors the valid_ingest_run_status CHECK constraint in init.sql. A status
# outside this set is refused here rather than sent to Postgres, so a bad value
# is a no-op record instead of an integrity error inside the ingest.
INGEST_RUN_STATUSES = ("running", "updated", "up_to_date", "failed")

# Mirrors the valid_ingestion_status CHECK constraint on `documents`.
_DOCUMENT_STATUSES = ("embedded", "failed", "pending")

_INSERT_INGEST_RUN = """
    INSERT INTO ingest_run (
        started_at, completed_at, status,
        documents_embedded, documents_failed, documents_pending,
        chunk_count, config_snapshot
    ) VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s)
"""

_SQL_DOCUMENT_COUNTS = """
    SELECT ingestion_status, COUNT(*)
    FROM documents
    WHERE NOT is_deleted
    GROUP BY ingestion_status
"""

_SQL_CHUNK_COUNT = "SELECT COUNT(*) FROM document_chunks"


def collect_ingest_counts(conn: Any) -> Dict[str, int]:
    """Count documents by ingestion status, plus chunks.

    Returns an empty mapping when the counts cannot be read, so a caller can
    tell "not counted" from a genuine zero.
    """
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(_SQL_DOCUMENT_COUNTS)
            by_status = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute(_SQL_CHUNK_COUNT)
            chunk_row = cursor.fetchone()
        finally:
            cursor.close()
    except Exception as exc:
        logger.warning("Failed to collect ingest counts: %s", exc)
        return {}

    counts = {
        f"documents_{status}": int(by_status.get(status, 0))
        for status in _DOCUMENT_STATUSES
    }
    counts["chunk_count"] = int(chunk_row[0]) if chunk_row else 0
    return counts


def record_ingest_run(
    conn: Any,
    *,
    started_at: Any,
    status: str,
    config_snapshot: Mapping,
    counts: Optional[Mapping] = None,
) -> bool:
    """Insert one ingest-run record. Returns whether the write landed.

    Never raises: an ingest must not fail because its provenance could not be
    stored (design D9).

    A missing count is stored as NULL rather than 0 — "no documents embedded"
    and "we did not count" are different facts, and the board renders them
    differently.
    """
    if status not in INGEST_RUN_STATUSES:
        logger.warning(
            "Refusing to record ingest run with unknown status %r (expected one of %s)",
            status,
            ", ".join(INGEST_RUN_STATUSES),
        )
        return False

    values = counts or {}
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                _INSERT_INGEST_RUN,
                (
                    started_at,
                    status,
                    values.get("documents_embedded"),
                    values.get("documents_failed"),
                    values.get("documents_pending"),
                    values.get("chunk_count"),
                    json.dumps(dict(config_snapshot or {})),
                ),
            )
        finally:
            cursor.close()
        conn.commit()
        logger.info("ingest run recorded: status=%s started_at=%s", status, started_at)
        return True
    except Exception as exc:  # provenance must never break the ingest
        logger.warning("Failed to record ingest run: %s", exc)
        return False
