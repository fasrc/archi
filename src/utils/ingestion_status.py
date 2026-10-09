"""Ingestion status tracking with lock-free reads.

The ingestion status dict is written by the ingestion thread and read by
Flask's request thread (``/api/ingestion/status``).  A dedicated lock
protects the status dict so the reader never blocks on the ingestion
mutual-exclusion lock, which is held for the entire ingest (22–64 min).

Each run also carries an identity: a UUID4 ``run_id`` and ISO-8601 UTC
``started_at``/``finished_at`` timestamps, set only at run start and run end
so that ``progress_callback`` updates during the run never disturb them.
"""

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def build_ingestion_helpers(
    run_ingestion_fn: Callable[..., Any],
    ingestion_lock: threading.RLock,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
):
    """Build the ingestion lifecycle functions used by service_data_manager.

    Returns a dict with:
      - ``set_ingestion_status(state, *, step, error)``
      - ``set_ingestion_progress(done, total=None)``
      - ``get_ingestion_status() -> dict``
      - ``run_initial_ingestion_async()``
      - ``ingestion_lock`` — the caller's ingestion mutual-exclusion lock

    ``now`` is a seam returning an aware UTC ``datetime``, used to stamp
    ``started_at``/``finished_at``; tests can fix it for deterministic values.
    """
    _status_lock = threading.Lock()
    _status: Dict[str, object] = {
        "state": "pending",
        "step": None,
        "error": None,
        "progress": None,
        "run_id": None,
        "started_at": None,
        "finished_at": None,
    }

    def set_ingestion_status(
        state: str, *, step: Optional[str] = None, error: Optional[str] = None
    ) -> None:
        with _status_lock:
            _status.update({"state": state, "step": step, "error": error})

    def set_ingestion_progress(done: int, total: Optional[int] = None) -> None:
        with _status_lock:
            _status["progress"] = {"done": done, "total": total}

    def get_ingestion_status() -> Dict[str, object]:
        with _status_lock:
            return dict(_status)

    def _finish_run(
        state: str, *, step: Optional[str] = None, error: Optional[str] = None
    ) -> None:
        with _status_lock:
            _status.update(
                {
                    "state": state,
                    "step": step,
                    "error": error,
                    "finished_at": now().isoformat(),
                }
            )

    def run_initial_ingestion_async() -> None:
        with _status_lock:
            _status.update(
                {
                    "state": "running",
                    "step": "initializing",
                    "error": None,
                    "progress": None,
                    "run_id": str(uuid.uuid4()),
                    "started_at": now().isoformat(),
                    "finished_at": None,
                }
            )
        try:
            with ingestion_lock:
                run_ingestion_fn(
                    progress_callback=lambda step: set_ingestion_status(
                        "running", step=step
                    ),
                    embedding_progress=set_ingestion_progress,
                )
            _finish_run("completed", step="done")
        except Exception as exc:
            logger.exception("Initial ingestion failed")
            _finish_run("error", step="failed", error=str(exc))

    return {
        "set_ingestion_status": set_ingestion_status,
        "set_ingestion_progress": set_ingestion_progress,
        "get_ingestion_status": get_ingestion_status,
        "run_initial_ingestion_async": run_initial_ingestion_async,
        "ingestion_lock": ingestion_lock,
    }
