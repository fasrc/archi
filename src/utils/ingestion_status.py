"""Ingestion status tracking with lock-free reads.

The ingestion status dict is written by the ingestion thread and read by
Flask's request thread (``/api/ingestion/status``).  A dedicated lock
protects the status dict so the reader never blocks on the ingestion
mutual-exclusion lock, which is held for the entire ingest (22–64 min).
"""

import threading
from typing import Any, Callable, Dict, Optional


def build_ingestion_helpers(
    run_ingestion_fn: Callable[..., Any],
    ingestion_lock: threading.RLock,
):
    """Build the ingestion lifecycle functions used by service_data_manager.

    Returns a dict with:
      - ``set_ingestion_status(state, *, step, error)``
      - ``get_ingestion_status() -> dict``
      - ``run_initial_ingestion_async()``
      - ``ingestion_lock`` — the caller's ingestion mutual-exclusion lock
    """
    _status_lock = threading.Lock()
    _status: Dict[str, object] = {
        "state": "pending",
        "step": None,
        "error": None,
    }

    def set_ingestion_status(
        state: str, *, step: Optional[str] = None, error: Optional[str] = None
    ) -> None:
        with _status_lock:
            _status.update({"state": state, "step": step, "error": error})

    def get_ingestion_status() -> Dict[str, object]:
        with _status_lock:
            return dict(_status)

    def run_initial_ingestion_async() -> None:
        set_ingestion_status("running", step="initializing")
        try:
            with ingestion_lock:
                run_ingestion_fn(
                    progress_callback=lambda step: set_ingestion_status(
                        "running", step=step
                    )
                )
            set_ingestion_status("completed", step="done")
        except Exception as exc:
            set_ingestion_status("error", step="failed", error=str(exc))

    return {
        "set_ingestion_status": set_ingestion_status,
        "get_ingestion_status": get_ingestion_status,
        "run_initial_ingestion_async": run_initial_ingestion_async,
        "ingestion_lock": ingestion_lock,
    }
