"""Regression: /api/ingestion/status must respond while ingestion is running.

Before the fix, `run_initial_ingestion_async` held the same RLock used by
`get_ingestion_status`, so every status poll blocked for the entire 22–64 min
ingest. The benchmark container timed out waiting for a response that could
never arrive (issue #219).

The fix gives ingestion_status its own lock, decoupled from the ingestion
mutual-exclusion lock.
"""

import logging
import threading
import time

import pytest


def test_status_endpoint_responds_during_ingestion():
    """The status endpoint must return within 1s even while ingestion holds its lock."""
    from src.utils.ingestion_status import build_ingestion_helpers

    ingestion_started = threading.Event()
    ingestion_release = threading.Event()

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        if progress_callback:
            progress_callback("embedding")
        ingestion_started.set()
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

    def fake_run_ingestion(progress_callback=None, **_kwargs):
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

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        raise RuntimeError("disk full")

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    helpers["run_initial_ingestion_async"]()

    status = helpers["get_ingestion_status"]()
    assert status["state"] == "error"
    assert "disk full" in status["error"]


def test_ingestion_failure_logs_traceback(caplog):
    """A failed ingestion must log the full traceback, not just the status dict."""
    from src.utils.ingestion_status import build_ingestion_helpers

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        raise RuntimeError("disk full")

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    with caplog.at_level(logging.ERROR, logger="src.utils.ingestion_status"):
        helpers["run_initial_ingestion_async"]()

    assert any("Initial ingestion failed" in r.message for r in caplog.records)
    assert any(r.exc_info is not None for r in caplog.records)


def test_concurrent_ingestions_are_serialized():
    """The ingestion lock still prevents concurrent run_ingestion calls."""
    from src.utils.ingestion_status import build_ingestion_helpers

    call_log = []
    barrier = threading.Barrier(2, timeout=5)

    def fake_run_ingestion(progress_callback=None, **_kwargs):
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


def test_initial_status_has_progress_none():
    """Before any ingest, get_ingestion_status returns progress=None with the baseline keys."""
    from src.utils.ingestion_status import build_ingestion_helpers

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        pass

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    status = helpers["get_ingestion_status"]()
    assert status["progress"] is None
    assert status["state"] == "pending"
    assert status["step"] is None
    assert status["error"] is None


def test_set_ingestion_progress_stores_counter():
    """set_ingestion_progress stores the done/total dict and is in the helpers."""
    from src.utils.ingestion_status import build_ingestion_helpers

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        pass

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    assert "set_ingestion_progress" in helpers

    helpers["set_ingestion_progress"](3, 10)
    assert helpers["get_ingestion_status"]()["progress"] == {"done": 3, "total": 10}

    helpers["set_ingestion_progress"](4)
    assert helpers["get_ingestion_status"]()["progress"] == {"done": 4, "total": None}


def test_get_ingestion_status_snapshot_not_mutated_by_later_progress():
    """A dict returned by get_ingestion_status is not changed by a later set_ingestion_progress."""
    from src.utils.ingestion_status import build_ingestion_helpers

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        pass

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    helpers["set_ingestion_progress"](1, 5)
    snapshot = helpers["get_ingestion_status"]()
    helpers["set_ingestion_progress"](2, 5)
    assert snapshot["progress"] == {"done": 1, "total": 5}


def test_set_ingestion_status_preserves_progress():
    """set_ingestion_status does not clear or overwrite progress."""
    from src.utils.ingestion_status import build_ingestion_helpers

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        pass

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    helpers["set_ingestion_progress"](3, 10)
    helpers["set_ingestion_status"]("running", step="x")
    status = helpers["get_ingestion_status"]()
    assert status["progress"] == {"done": 3, "total": 10}
    assert status["state"] == "running"
    assert status["step"] == "x"


def test_embedding_progress_updates_status_and_is_kept_on_completion():
    """embedding_progress callback updates status; completed payload retains the last counter."""
    from src.utils.ingestion_status import build_ingestion_helpers

    status_after_second_call = {}

    def fake_run_ingestion(progress_callback=None, embedding_progress=None, **_kwargs):
        if embedding_progress:
            embedding_progress(0, 2)
            embedding_progress(2, 2)
        status_after_second_call.update(helpers["get_ingestion_status"]())

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    helpers["run_initial_ingestion_async"]()

    assert status_after_second_call["progress"] == {"done": 2, "total": 2}
    completed = helpers["get_ingestion_status"]()
    assert completed["state"] == "completed"
    assert completed["progress"] == {"done": 2, "total": 2}


def test_second_run_initial_ingestion_resets_progress():
    """A second run_initial_ingestion_async resets progress to None before the fake fires."""
    from src.utils.ingestion_status import build_ingestion_helpers

    status_at_start_of_second = {}

    call_count = [0]

    def fake_run_ingestion(progress_callback=None, embedding_progress=None, **_kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            status_at_start_of_second.update(helpers["get_ingestion_status"]())

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    helpers["run_initial_ingestion_async"]()
    helpers["set_ingestion_progress"](5, 10)
    helpers["run_initial_ingestion_async"]()

    assert status_at_start_of_second["progress"] is None


def test_run_tracked_publishes_running_and_completed():
    """run_tracked sets running/step inside fn, completed/done after, and returns fn's value."""
    from src.utils.ingestion_status import build_ingestion_helpers

    status_inside = {}

    def fn():
        status_inside.update(helpers["get_ingestion_status"]())
        return 42

    helpers = build_ingestion_helpers(lambda **_: None, threading.RLock())
    result = helpers["run_tracked"]("upload", fn)

    assert status_inside["state"] == "running"
    assert status_inside["step"] == "upload"
    assert status_inside["error"] is None
    assert status_inside["progress"] is None
    assert result == 42
    after = helpers["get_ingestion_status"]()
    assert after["state"] == "completed"
    assert after["step"] == "done"


def test_run_tracked_resets_progress():
    """run_tracked resets progress to None before fn runs, even if set beforehand."""
    from src.utils.ingestion_status import build_ingestion_helpers

    progress_inside = {}

    def fn():
        progress_inside["value"] = helpers["get_ingestion_status"]()["progress"]

    helpers = build_ingestion_helpers(lambda **_: None, threading.RLock())
    helpers["set_ingestion_progress"](3, 10)
    helpers["run_tracked"]("upload", fn)

    assert progress_inside["value"] is None


def test_run_tracked_publishes_error_on_exception():
    """run_tracked re-raises the exception and sets state=error/step=failed/error=msg."""
    from src.utils.ingestion_status import build_ingestion_helpers

    def failing_fn():
        raise RuntimeError("boom")

    helpers = build_ingestion_helpers(lambda **_: None, threading.RLock())

    with pytest.raises(RuntimeError, match="boom"):
        helpers["run_tracked"]("upload", failing_fn)

    status = helpers["get_ingestion_status"]()
    assert status["state"] == "error"
    assert status["step"] == "failed"
    assert status["error"] == "boom"


def test_run_tracked_waits_for_lock():
    """run_tracked waits for the ingestion lock; status is not mutated while the lock is held."""
    from src.utils.ingestion_status import build_ingestion_helpers

    lock = threading.RLock()
    fn_ran = []
    helpers = build_ingestion_helpers(lambda **_: None, lock)

    lock.acquire()
    try:
        helpers["set_ingestion_status"]("running", step="embedding")
        t = threading.Thread(
            target=lambda: helpers["run_tracked"](
                "upload", lambda: fn_ran.append(True)
            ),
            daemon=True,
        )
        t.start()
        time.sleep(0.2)
        assert helpers["get_ingestion_status"]()["step"] == "embedding"
        assert not fn_ran
    finally:
        lock.release()

    t.join(timeout=5)
    assert helpers["get_ingestion_status"]()["state"] == "completed"


def test_run_tracked_is_reentrant():
    """run_tracked completes without deadlock when the same thread already holds the lock."""
    from src.utils.ingestion_status import build_ingestion_helpers

    lock = threading.RLock()
    helpers = build_ingestion_helpers(lambda **_: None, lock)
    result = {}

    def do_run():
        with lock:
            helpers["run_tracked"]("upload", lambda: result.update({"ok": True}))

    t = threading.Thread(target=do_run, daemon=True)
    t.start()
    t.join(timeout=5)
    assert result.get("ok") is True
    assert helpers["get_ingestion_status"]()["state"] == "completed"
