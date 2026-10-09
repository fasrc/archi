"""Ingestion status tracking with lock-free reads.

The ingestion status dict is written by the ingestion thread and read by
Flask's request thread (``/api/ingestion/status``).  A dedicated lock
protects the status dict so the reader never blocks on the ingestion
mutual-exclusion lock, which is held for the entire ingest (22–64 min).
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def build_ingestion_helpers(
    run_ingestion_fn: Callable[..., Any],
    ingestion_lock: threading.RLock,
):
    """Build the ingestion lifecycle functions used by service_data_manager.

    Returns a dict with:
      - ``set_ingestion_status(state, *, step, error)``
      - ``set_ingestion_progress(done, total=None)``
      - ``get_ingestion_status() -> dict``
      - ``run_initial_ingestion_async()``
      - ``run_tracked(step, fn)``
      - ``run_source_refresh(name, func, update_vectorstore, set_source_status)``
      - ``ingestion_lock`` — the caller's ingestion mutual-exclusion lock
    """
    _status_lock = threading.Lock()
    _status: Dict[str, object] = {
        "state": "pending",
        "step": None,
        "error": None,
        "progress": None,
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

    # Runs that hold or wait for ingestion_lock. Only the lock owner publishes
    # its step, and "completed" is published only when no run is queued: a
    # benchmark that sees "completed" must not have a corpus change behind it.
    _inflight = 0

    def _join() -> bool:
        """Count a run in; True when no other run holds or awaits the lock."""
        nonlocal _inflight
        with _status_lock:
            _inflight += 1
            return _inflight == 1

    def _publish_running(step: str) -> None:
        with _status_lock:
            _status.update(
                {"state": "running", "step": step, "error": None, "progress": None}
            )

    def _finish(state: str, step: str, error: Optional[str] = None) -> None:
        """Count a run out. Call it while the run still holds ingestion_lock."""
        nonlocal _inflight
        with _status_lock:
            _inflight -= 1
            if state == "error" or _inflight == 0:
                _status.update({"state": state, "step": step, "error": error})

    def run_tracked(step: str, fn: Callable[[], Any]) -> Any:
        _join()
        with ingestion_lock:
            _publish_running(step)
            try:
                result = fn()
            except Exception as exc:
                _finish("error", "failed", str(exc))
                raise
            _finish("completed", "done")
            return result

    def run_source_refresh(
        name: str,
        func: Callable[[], None],
        update_vectorstore: Callable[..., Any],
        set_source_status: Callable[..., None],
    ) -> None:
        def body() -> None:
            logger.info("Running ingestion task: %s", name)
            set_source_status(name, state="running")
            func()
            logger.info("Updating vectorstore after scheduled task: %s", name)
            update_vectorstore(force=True)
            set_source_status(
                name,
                state="idle",
                last_run=datetime.now(timezone.utc).isoformat(),
            )

        run_tracked(f"scheduled:{name}", body)

    def run_initial_ingestion_async() -> None:
        if _join():
            _publish_running("initializing")
        with ingestion_lock:
            _publish_running("initializing")
            try:
                run_ingestion_fn(
                    progress_callback=lambda step: set_ingestion_status(
                        "running", step=step
                    ),
                    embedding_progress=set_ingestion_progress,
                )
            except Exception as exc:
                logger.exception("Initial ingestion failed")
                _finish("error", "failed", str(exc))
                return
            _finish("completed", "done")

    return {
        "set_ingestion_status": set_ingestion_status,
        "set_ingestion_progress": set_ingestion_progress,
        "get_ingestion_status": get_ingestion_status,
        "run_initial_ingestion_async": run_initial_ingestion_async,
        "run_tracked": run_tracked,
        "run_source_refresh": run_source_refresh,
        "ingestion_lock": ingestion_lock,
    }
