"""Deployment and knowledge-base provenance for the service status board.

Builds the view model `/ssb/status` renders: which config is actually deployed,
when, when the corpus was last built, and under which ingest configuration.

All the branching lives here rather than in ``service_alerts.py`` or the Jinja
template, both of which are thinly covered by unit tests (design D7). The route
stays a call site; the match verdict, the live-edited state, drift, duration and
the missing-record fallbacks are exercised directly.

Nothing here may raise: an unreadable record must degrade to an explicit
"unavailable" panel while the alert sections still render.
"""

import logging
from typing import Any, Dict, List, Mapping, Optional

from src.utils.deployment_record import is_live_edited
from src.utils.ingest_provenance import (
    build_ingest_config_snapshot,
    compare_ingest_config,
)

logger = logging.getLogger(__name__)

# A very dirty checkout would otherwise push the alert sections off the page.
# The full listing stays queryable in the record.
_DIRTY_PREVIEW_LIMIT = 5

_SHORT_SHA = 8

_DEPLOYMENT_COLUMNS = (
    "config_ref",
    "config_sha",
    "config_head",
    "pin_matched",
    "dirty_paths",
    "app_version",
    "deployed_at",
)

_INGEST_RUN_COLUMNS = (
    "started_at",
    "completed_at",
    "status",
    "documents_embedded",
    "documents_failed",
    "documents_pending",
    "chunk_count",
    "config_snapshot",
)

_SQL_LATEST_DEPLOYMENT = f"""
    SELECT {", ".join(_DEPLOYMENT_COLUMNS)}
    FROM deployment_record
    ORDER BY deployed_at DESC
    LIMIT 1
"""

# Only a COMPLETED run describes a corpus that is actually serving queries.
_SQL_LATEST_INGEST_RUN = f"""
    SELECT {", ".join(_INGEST_RUN_COLUMNS)}
    FROM ingest_run
    WHERE completed_at IS NOT NULL
    ORDER BY completed_at DESC
    LIMIT 1
"""

_SQL_CURRENT_DATA_MANAGER_CONFIG = """
    SELECT data_manager_config FROM static_config WHERE id = 1
"""


def _short(sha: Optional[str]) -> Optional[str]:
    return sha[:_SHORT_SHA] if sha else None


def format_duration(started_at: Any, completed_at: Any) -> Optional[str]:
    """Render a run window as a human duration.

    Returns ``None`` for a run with no completion, and for a negative span:
    clock skew must not render as a negative run time.
    """
    if not started_at or not completed_at:
        return None
    try:
        seconds = int((completed_at - started_at).total_seconds())
    except Exception:
        return None
    if seconds < 0:
        return None
    if seconds < 60:
        return f"{seconds} sec"

    hours, remainder = divmod(seconds // 60, 60)
    if hours:
        return f"{hours} hr {remainder} min"
    return f"{remainder} min"


def _dirty_lines(raw: Any) -> List[str]:
    if not raw:
        return []
    return [line for line in str(raw).splitlines() if line.strip()]


def build_deployment_panel(row: Optional[Mapping]) -> Dict[str, Any]:
    """Build the Deployment panel from the newest deployment record."""
    if not row:
        return {
            "available": False,
            "config_ref": None,
            "config_sha_short": None,
            "config_head_short": None,
            "pin_matched": None,
            "live_edited": False,
            "dirty_path_count": 0,
            "dirty_paths_preview": [],
            "app_version": None,
            "deployed_at": None,
        }

    dirty = _dirty_lines(row.get("dirty_paths"))
    return {
        "available": True,
        "config_ref": row.get("config_ref"),
        "config_sha_short": _short(row.get("config_sha")),
        "config_head_short": _short(row.get("config_head")),
        "pin_matched": row.get("pin_matched"),
        "live_edited": is_live_edited(row),
        "dirty_path_count": len(dirty),
        "dirty_paths_preview": dirty[:_DIRTY_PREVIEW_LIMIT],
        "app_version": row.get("app_version"),
        "deployed_at": row.get("deployed_at"),
    }


def build_knowledge_base_panel(
    run: Optional[Mapping], current_snapshot: Mapping
) -> Dict[str, Any]:
    """Build the Knowledge base panel from the newest completed ingest run.

    The flag values come from the run's own snapshot, never from current
    configuration — that distinction is the whole point of recording it.
    """
    if not run:
        return {
            "available": False,
            "started_at": None,
            "completed_at": None,
            "duration": None,
            "status": None,
            "documents_embedded": None,
            "documents_failed": None,
            "documents_pending": None,
            "chunk_count": None,
            "config": {},
            "drift": [],
        }

    snapshot = run.get("config_snapshot") or {}
    return {
        "available": True,
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "duration": format_duration(run.get("started_at"), run.get("completed_at")),
        "status": run.get("status"),
        "documents_embedded": run.get("documents_embedded"),
        "documents_failed": run.get("documents_failed"),
        "documents_pending": run.get("documents_pending"),
        "chunk_count": run.get("chunk_count"),
        "config": snapshot,
        "drift": compare_ingest_config(current_snapshot, snapshot),
    }


def _fetch_row(cursor, sql: str, columns) -> Optional[Dict[str, Any]]:
    cursor.execute(sql)
    row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(columns, row))


def load_status_provenance(conn: Any) -> Dict[str, Any]:
    """Load both panels. Never raises.

    A failure here must not take the alert sections down with it, so the
    unavailable view model is the fallback rather than an exception.
    """
    deployment_row = None
    run_row = None
    current_snapshot: Dict[str, Any] = {}

    try:
        cursor = conn.cursor()
        try:
            deployment_row = _fetch_row(
                cursor, _SQL_LATEST_DEPLOYMENT, _DEPLOYMENT_COLUMNS
            )
            run_row = _fetch_row(cursor, _SQL_LATEST_INGEST_RUN, _INGEST_RUN_COLUMNS)

            cursor.execute(_SQL_CURRENT_DATA_MANAGER_CONFIG)
            config_row = cursor.fetchone()
            if config_row:
                current_snapshot = build_ingest_config_snapshot(config_row[0])
        finally:
            cursor.close()
    except Exception as exc:
        logger.warning("Failed to load status-board provenance: %s", exc)

    return {
        "deployment": build_deployment_panel(deployment_row),
        "knowledge_base": build_knowledge_base_panel(run_row, current_snapshot),
    }


def load_status_provenance_for(pg_config: Mapping) -> Dict[str, Any]:
    """Connect, load both panels, and close. Never raises.

    The route calls this so ``service_alerts.py`` stays a single call site and
    its existing alert connection handling is left untouched.
    """
    conn = None
    try:
        import psycopg2

        conn = psycopg2.connect(**dict(pg_config or {}))
        return load_status_provenance(conn)
    except Exception as exc:
        logger.warning("Failed to connect for status-board provenance: %s", exc)
        return load_status_provenance(None)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover - close failures are inert here
                pass
