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


def test_initial_status_has_run_identity_none():
    """Before any ingest, run_id, started_at, and finished_at are all None."""
    from src.utils.ingestion_status import build_ingestion_helpers

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        pass

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    status = helpers["get_ingestion_status"]()
    assert status["run_id"] is None
    assert status["started_at"] is None
    assert status["finished_at"] is None


def test_run_sets_run_id_and_started_at_while_running():
    """A running ingest has a UUID4 run_id, an ISO-8601 started_at, and finished_at None."""
    import uuid
    from datetime import datetime, timezone

    from src.utils.ingestion_status import build_ingestion_helpers

    fixed_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    observed = {}

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        observed.update(helpers["get_ingestion_status"]())

    helpers = build_ingestion_helpers(
        fake_run_ingestion, threading.RLock(), now=lambda: fixed_now
    )
    helpers["run_initial_ingestion_async"]()

    assert uuid.UUID(observed["run_id"]).version == 4
    assert observed["started_at"] == fixed_now.isoformat()
    assert observed["finished_at"] is None


def test_completed_sets_finished_at_and_keeps_run_id():
    """A completed ingest sets finished_at and keeps the run_id it started with."""
    from datetime import datetime, timezone

    from src.utils.ingestion_status import build_ingestion_helpers

    times = iter(
        [
            datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        ]
    )

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        pass

    helpers = build_ingestion_helpers(
        fake_run_ingestion, threading.RLock(), now=lambda: next(times)
    )
    helpers["run_initial_ingestion_async"]()

    status = helpers["get_ingestion_status"]()
    assert status["state"] == "completed"
    assert status["run_id"] is not None
    assert status["started_at"] == "2026-01-01T00:00:00+00:00"
    assert status["finished_at"] == "2026-01-01T00:05:00+00:00"


def test_error_sets_finished_at():
    """A failed ingest sets finished_at alongside the error state."""
    from datetime import datetime, timezone

    from src.utils.ingestion_status import build_ingestion_helpers

    times = iter(
        [
            datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        ]
    )

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        raise RuntimeError("disk full")

    helpers = build_ingestion_helpers(
        fake_run_ingestion, threading.RLock(), now=lambda: next(times)
    )
    helpers["run_initial_ingestion_async"]()

    status = helpers["get_ingestion_status"]()
    assert status["state"] == "error"
    assert status["finished_at"] == "2026-01-01T00:01:00+00:00"


def test_progress_callback_keeps_run_identity():
    """A progress_callback step update does not disturb run_id or started_at."""
    from src.utils.ingestion_status import build_ingestion_helpers

    before = {}
    after = {}

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        before.update(helpers["get_ingestion_status"]())
        if progress_callback:
            progress_callback("embedding")
        after.update(helpers["get_ingestion_status"]())

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())
    helpers["run_initial_ingestion_async"]()

    assert after["run_id"] == before["run_id"]
    assert after["started_at"] == before["started_at"]
    assert after["state"] == "running"
    assert after["step"] == "embedding"


def test_two_runs_get_different_run_ids():
    """Two consecutive run_initial_ingestion_async calls get distinct run_ids."""
    from src.utils.ingestion_status import build_ingestion_helpers

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        pass

    helpers = build_ingestion_helpers(fake_run_ingestion, threading.RLock())

    helpers["run_initial_ingestion_async"]()
    first_run_id = helpers["get_ingestion_status"]()["run_id"]

    helpers["run_initial_ingestion_async"]()
    second_run_id = helpers["get_ingestion_status"]()["run_id"]

    assert first_run_id is not None
    assert second_run_id is not None
    assert first_run_id != second_run_id


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
