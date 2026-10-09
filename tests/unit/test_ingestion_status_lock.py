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


def test_run_source_refresh_publishes_running_and_completed():
    """Inside func the status is running/scheduled:git; after, completed/done."""
    from src.utils.ingestion_status import build_ingestion_helpers

    status_inside = {}
    calls = []

    def func():
        status_inside.update(helpers["get_ingestion_status"]())

    def fake_update_vectorstore(**kwargs):
        calls.append(("update_vectorstore", kwargs))

    def fake_set_source_status(source, **kwargs):
        calls.append(("set_source_status", source, kwargs))

    helpers = build_ingestion_helpers(lambda **_: None, threading.RLock())
    helpers["run_source_refresh"](
        "git", func, fake_update_vectorstore, fake_set_source_status
    )

    assert status_inside["state"] == "running"
    assert status_inside["step"] == "scheduled:git"
    after = helpers["get_ingestion_status"]()
    assert after["state"] == "completed"


def test_run_source_refresh_call_order():
    """Call order: set_source_status running, func, update_vectorstore, set_source_status idle."""
    from datetime import datetime, timezone

    from src.utils.ingestion_status import build_ingestion_helpers

    log = []

    def func():
        log.append("func")

    def fake_update_vectorstore(**kwargs):
        log.append(("update_vectorstore", kwargs))

    def fake_set_source_status(source, **kwargs):
        log.append(("set_source_status", source, kwargs))

    helpers = build_ingestion_helpers(lambda **_: None, threading.RLock())
    helpers["run_source_refresh"](
        "git", func, fake_update_vectorstore, fake_set_source_status
    )

    assert log[0] == ("set_source_status", "git", {"state": "running"})
    assert log[1] == "func"
    assert log[2] == ("update_vectorstore", {"force": True})
    name, source, kwargs = log[3]
    assert name == "set_source_status"
    assert source == "git"
    assert kwargs["state"] == "idle"
    last_run = kwargs["last_run"]
    parsed = datetime.fromisoformat(last_run)
    assert parsed.utcoffset().total_seconds() == 0


def test_run_source_refresh_error_from_update_vectorstore():
    """If update_vectorstore raises, the status is error and idle set_source_status is skipped."""
    from src.utils.ingestion_status import build_ingestion_helpers

    idle_calls = []

    def func():
        pass

    def fake_update_vectorstore(**kwargs):
        raise RuntimeError("embed down")

    def fake_set_source_status(source, **kwargs):
        if kwargs.get("state") == "idle":
            idle_calls.append(kwargs)

    helpers = build_ingestion_helpers(lambda **_: None, threading.RLock())
    with pytest.raises(RuntimeError, match="embed down"):
        helpers["run_source_refresh"](
            "git", func, fake_update_vectorstore, fake_set_source_status
        )

    status = helpers["get_ingestion_status"]()
    assert status["state"] == "error"
    assert status["error"] == "embed down"
    assert idle_calls == []


class _RecordingLock:
    """An RLock with hooks just before and just after each outermost release."""

    def __init__(self):
        self._lock = threading.RLock()
        self._depth = 0
        self.on_release = lambda: None
        self.after_release = lambda: None

    def acquire(self, *args, **kwargs):
        got = self._lock.acquire(*args, **kwargs)
        if got:
            self._depth += 1
        return got

    def release(self):
        self._depth -= 1
        if self._depth == 0:
            self.on_release()
            self._lock.release()
            self.after_release()
        else:
            self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def test_initial_ingest_completion_does_not_overwrite_a_queued_run():
    """The initial ingest publishes its terminal state before it releases the lock.

    Otherwise a run queued behind it publishes running, and the initial thread
    then overwrites that with completed while the queued run mutates the corpus.
    """
    from src.utils.ingestion_status import build_ingestion_helpers

    release_initial = threading.Event()
    initial_started = threading.Event()
    tracked_inside = threading.Event()

    def fake_run_ingestion(progress_callback=None, **_kwargs):
        initial_started.set()
        release_initial.wait(timeout=5)

    lock = _RecordingLock()
    helpers = build_ingestion_helpers(fake_run_ingestion, lock)
    initial = threading.Thread(
        target=helpers["run_initial_ingestion_async"], daemon=True
    )
    # Hold the initial thread between its release and anything after it, so a
    # publication made outside the lock lands after the queued run's running.
    lock.after_release = lambda: (
        threading.current_thread() is initial and tracked_inside.wait(timeout=5)
    )
    initial.start()
    assert initial_started.wait(timeout=5)

    seen = {}

    def tracked_fn():
        tracked_inside.set()
        initial.join(timeout=5)
        seen.update(helpers["get_ingestion_status"]())

    tracked = threading.Thread(
        target=lambda: helpers["run_tracked"]("upload", tracked_fn), daemon=True
    )
    tracked.start()
    time.sleep(0.2)
    release_initial.set()
    tracked.join(timeout=5)

    assert seen["state"] == "running"
    assert seen["step"] == "upload"
    assert helpers["get_ingestion_status"]()["state"] == "completed"


def test_initial_ingest_does_not_mask_an_active_refresh():
    """A queued initial ingest must not publish initializing over the lock owner."""
    from src.utils.ingestion_status import build_ingestion_helpers

    refresh_inside = threading.Event()
    release_refresh = threading.Event()
    helpers = build_ingestion_helpers(lambda **_: None, threading.RLock())

    def refresh_fn():
        refresh_inside.set()
        release_refresh.wait(timeout=5)

    refresh = threading.Thread(
        target=lambda: helpers["run_tracked"]("scheduled:git", refresh_fn),
        daemon=True,
    )
    refresh.start()
    assert refresh_inside.wait(timeout=5)

    initial = threading.Thread(
        target=helpers["run_initial_ingestion_async"], daemon=True
    )
    initial.start()
    time.sleep(0.2)
    try:
        status = helpers["get_ingestion_status"]()
        assert status["state"] == "running"
        assert status["step"] == "scheduled:git"
    finally:
        release_refresh.set()
        refresh.join(timeout=5)
        initial.join(timeout=5)
    assert helpers["get_ingestion_status"]()["state"] == "completed"


def test_completed_is_not_published_while_a_run_is_queued():
    """A run that finishes with another tracked run queued keeps the state running."""
    from src.utils.ingestion_status import build_ingestion_helpers

    lock = _RecordingLock()
    helpers = build_ingestion_helpers(lambda **_: None, lock)
    first_inside = threading.Event()
    release_first = threading.Event()
    at_release = []

    def first_fn():
        first_inside.set()
        release_first.wait(timeout=5)

    first = threading.Thread(
        target=lambda: helpers["run_tracked"]("scheduled:git", first_fn),
        daemon=True,
    )
    first.start()
    assert first_inside.wait(timeout=5)

    second = threading.Thread(
        target=lambda: helpers["run_tracked"]("upload", lambda: None), daemon=True
    )
    second.start()
    time.sleep(0.2)

    lock.on_release = lambda: at_release.append(helpers["get_ingestion_status"]())
    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert at_release[0]["state"] == "running"
    assert at_release[-1]["state"] == "completed"
    assert helpers["get_ingestion_status"]()["state"] == "completed"


def test_error_is_published_even_with_a_run_queued():
    """A failure is never hidden behind a queued run; the queued run replaces it later."""
    from src.utils.ingestion_status import build_ingestion_helpers

    lock = _RecordingLock()
    helpers = build_ingestion_helpers(lambda **_: None, lock)
    first_inside = threading.Event()
    release_first = threading.Event()
    at_release = []

    def first_fn():
        first_inside.set()
        release_first.wait(timeout=5)
        raise RuntimeError("embed down")

    def run_first():
        with pytest.raises(RuntimeError):
            helpers["run_tracked"]("scheduled:git", first_fn)

    first = threading.Thread(target=run_first, daemon=True)
    first.start()
    assert first_inside.wait(timeout=5)
    second = threading.Thread(
        target=lambda: helpers["run_tracked"]("upload", lambda: None), daemon=True
    )
    second.start()
    time.sleep(0.2)

    lock.on_release = lambda: at_release.append(helpers["get_ingestion_status"]())
    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert at_release[0]["state"] == "error"
    assert at_release[0]["error"] == "embed down"
    assert helpers["get_ingestion_status"]()["state"] == "completed"


def test_run_source_refresh_publishes_error_when_the_update_failed():
    """A sync that returns "failed" (documents not added) is an error, not completed."""
    from src.utils.ingestion_status import build_ingestion_helpers

    idle_calls = []

    def fake_set_source_status(source, **kwargs):
        if kwargs.get("state") == "idle":
            idle_calls.append(kwargs)

    helpers = build_ingestion_helpers(lambda **_: None, threading.RLock())
    with pytest.raises(RuntimeError, match="failed"):
        helpers["run_source_refresh"](
            "git", lambda: None, lambda **_: "failed", fake_set_source_status
        )

    status = helpers["get_ingestion_status"]()
    assert status["state"] == "error"
    assert status["step"] == "failed"
    assert idle_calls == []


def test_run_upload_update_publishes_upload_lifecycle():
    from src.utils.ingestion_status import build_ingestion_helpers

    seen = {}
    calls = []

    def fake_update_vectorstore(**kwargs):
        calls.append(kwargs)
        seen.update(helpers["get_ingestion_status"]())
        return "updated"

    helpers = build_ingestion_helpers(lambda **_: None, threading.RLock())
    helpers["run_upload_update"](fake_update_vectorstore)

    assert calls == [{"force": True}]
    assert seen["state"] == "running"
    assert seen["step"] == "upload"
    assert helpers["get_ingestion_status"]()["state"] == "completed"


def test_run_upload_update_publishes_error_when_the_update_failed():
    from src.utils.ingestion_status import build_ingestion_helpers

    helpers = build_ingestion_helpers(lambda **_: None, threading.RLock())
    with pytest.raises(RuntimeError, match="failed"):
        helpers["run_upload_update"](lambda **_: "failed")

    assert helpers["get_ingestion_status"]()["state"] == "error"
