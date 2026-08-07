"""Regression: /api/ingestion/status must respond while ingestion is running.

Before the fix, `run_initial_ingestion_async` held the same RLock used by
`get_ingestion_status`, so every status poll blocked for the entire 22–64 min
ingest. The benchmark container timed out waiting for a response that could
never arrive (issue #219).

The fix gives ingestion_status its own lock, decoupled from the ingestion
mutual-exclusion lock.
"""

import threading
import time

import pytest


def test_status_endpoint_responds_during_ingestion():
    """The status endpoint must return within 1s even while ingestion holds its lock."""
    from src.utils.ingestion_status import build_ingestion_helpers

    ingestion_started = threading.Event()
    ingestion_release = threading.Event()

    def fake_run_ingestion(progress_callback=None):
        ingestion_started.set()
        if progress_callback:
            progress_callback("embedding")
        ingestion_release.wait(timeout=10)

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())

    ingestion_thread = threading.Thread(
        target=helpers["run_initial_ingestion_async"], daemon=True
    )
    ingestion_thread.start()
    ingestion_started.wait(timeout=5)
    assert ingestion_started.is_set(), "ingestion never started"

    start = time.monotonic()
    status = helpers["get_ingestion_status"]()
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"status read took {elapsed:.2f}s — lock contention"
    assert status["state"] == "running"
    assert status["step"] == "embedding"

    ingestion_release.set()
    ingestion_thread.join(timeout=5)


def test_status_shows_completed_after_ingestion():
    """After ingestion finishes, the status endpoint reflects completion."""
    from src.utils.ingestion_status import build_ingestion_helpers

    def fake_run_ingestion(progress_callback=None):
        if progress_callback:
            progress_callback("done")

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    helpers["run_initial_ingestion_async"]()

    status = helpers["get_ingestion_status"]()
    assert status["state"] == "completed"
    assert status["step"] == "done"


def test_status_shows_error_on_ingestion_failure():
    """A failed ingestion records the error without deadlocking."""
    from src.utils.ingestion_status import build_ingestion_helpers

    def fake_run_ingestion(progress_callback=None):
        raise RuntimeError("disk full")

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    helpers["run_initial_ingestion_async"]()

    status = helpers["get_ingestion_status"]()
    assert status["state"] == "error"
    assert "disk full" in status["error"]


def test_concurrent_ingestions_are_serialized():
    """The ingestion lock still prevents concurrent run_ingestion calls."""
    from src.utils.ingestion_status import build_ingestion_helpers

    call_log = []
    barrier = threading.Barrier(2, timeout=5)

    def fake_run_ingestion(progress_callback=None):
        call_log.append(("enter", threading.current_thread().name))
        try:
            barrier.wait(timeout=1)
        except threading.BrokenBarrierError:
            pass
        call_log.append(("exit", threading.current_thread().name))

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    lock = helpers["ingestion_lock"]

    def run_locked():
        with lock:
            fake_run_ingestion()

    t1 = threading.Thread(target=run_locked, name="t1", daemon=True)
    t2 = threading.Thread(target=run_locked, name="t2", daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    enters = [e for e in call_log if e[0] == "enter"]
    exits = [e for e in call_log if e[0] == "exit"]
    assert len(enters) == 2
    assert len(exits) == 2
    if enters[0][1] == enters[1][1]:
        pytest.fail("both ingestions ran on the same thread — test is broken")
    assert call_log[1] == (
        "exit",
        call_log[0][1],
    ), "second enter happened before first exit — lock did not serialize"
