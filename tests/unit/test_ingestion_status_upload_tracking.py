"""An upload is reported as running from the start of its collection (issue #648).

``track_upload`` counts an upload into the ingestion status before its
collection starts, without taking the ingestion lock, and counts it out after
its vectorstore update ended. Between the two the status never shows
``completed``. A block that raises publishes ``error``; a block whose update
published ``error`` keeps that error.
"""

import threading
from datetime import datetime, timezone

import pytest

from src.utils.ingestion_status import build_ingestion_helpers


def _helpers(lock=None, **kwargs):
    return build_ingestion_helpers(
        lambda **_: None, lock or threading.RLock(), **kwargs
    )


def _fixed_now(times):
    iterator = iter(times)
    return lambda: next(iterator)


def test_collection_is_reported_as_a_running_upload():
    helpers = _helpers()

    with helpers["track_upload"]():
        during = helpers["get_ingestion_status"]()
    after = helpers["get_ingestion_status"]()

    assert during["state"] == "running"
    assert during["step"] == "upload"
    assert during["progress"] is None
    assert after["state"] == "completed"
    assert after["step"] == "done"
    assert after["error"] is None


def test_completed_is_not_published_between_collection_and_update():
    helpers = _helpers()
    seen = []

    with helpers["track_upload"]():
        seen.append(helpers["get_ingestion_status"]())
        helpers["run_upload_update"](lambda **_: "updated")
        seen.append(helpers["get_ingestion_status"]())

    assert [status["state"] for status in seen] == ["running", "running"]
    assert [status["step"] for status in seen] == ["upload", "upload"]
    assert helpers["get_ingestion_status"]()["state"] == "completed"


def test_collection_does_not_wait_for_the_ingestion_lock():
    lock = threading.RLock()
    helpers = _helpers(lock)
    refresh_inside = threading.Event()
    release_refresh = threading.Event()

    def refresh_fn():
        refresh_inside.set()
        release_refresh.wait(timeout=5)

    refresh = threading.Thread(
        target=lambda: helpers["run_tracked"]("scheduled:git", refresh_fn),
        daemon=True,
    )
    refresh.start()
    assert refresh_inside.wait(timeout=5)

    during = []

    def collect():
        with helpers["track_upload"]():
            during.append(helpers["get_ingestion_status"]())

    upload = threading.Thread(target=collect, daemon=True)
    upload.start()
    upload.join(timeout=5)
    collected_while_locked = not upload.is_alive()
    while_refresh_runs = helpers["get_ingestion_status"]()
    release_refresh.set()
    refresh.join(timeout=5)

    assert collected_while_locked, "the collection waited for the ingestion lock"
    assert during[0]["state"] == "running"
    assert during[0]["step"] == "scheduled:git"
    assert while_refresh_runs["state"] == "running"
    assert helpers["get_ingestion_status"]()["state"] == "completed"


def test_a_failed_collection_publishes_error():
    helpers = _helpers()

    with pytest.raises(RuntimeError, match="crawl down"):
        with helpers["track_upload"]():
            raise RuntimeError("crawl down")

    status = helpers["get_ingestion_status"]()
    assert status["state"] == "error"
    assert status["step"] == "failed"
    assert status["error"] == "crawl down"


def test_a_failed_update_inside_the_block_keeps_the_error():
    helpers = _helpers()

    with helpers["track_upload"]():
        with pytest.raises(RuntimeError):
            helpers["run_upload_update"](lambda **_: "failed")
        inside = helpers["get_ingestion_status"]()
    after = helpers["get_ingestion_status"]()

    assert inside["state"] == "error"
    assert after["state"] == "error"
    assert after["step"] == "failed"
    assert "documents not added" in after["error"]


def test_the_upload_keeps_one_run_identity_from_collection_to_update():
    times = [
        datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 9, 3, 5, tzinfo=timezone.utc),
    ]
    helpers = _helpers(now=_fixed_now(times))

    with helpers["track_upload"]():
        at_collection = helpers["get_ingestion_status"]()
        helpers["run_upload_update"](lambda **_: "updated")
        after_update = helpers["get_ingestion_status"]()
    done = helpers["get_ingestion_status"]()

    assert at_collection["run_id"] is not None
    assert at_collection["started_at"] == "2026-11-09T03:00:00+00:00"
    assert at_collection["finished_at"] is None
    assert after_update["run_id"] == at_collection["run_id"]
    assert after_update["finished_at"] is None
    assert done["run_id"] == at_collection["run_id"]
    assert done["finished_at"] == "2026-11-09T03:05:00+00:00"


def test_a_step_after_a_failed_collection_starts_a_new_run():
    """A collection that fails during the initial ingestion ends the shared run in
    ``error``; the ingestion's next progress step is a new run, not a finished one."""
    times = [
        datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 9, 3, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 9, 3, 2, tzinfo=timezone.utc),
        datetime(2026, 11, 9, 3, 5, tzinfo=timezone.utc),
    ]
    seen = {}

    def run_ingestion(progress_callback, embedding_progress):
        progress_callback("Updating vectorstore")
        with pytest.raises(RuntimeError):
            with helpers["track_upload"]():
                raise RuntimeError("crawl down")
        seen["after_error"] = helpers["get_ingestion_status"]()
        progress_callback("Updating vectorstore")
        seen["after_step"] = helpers["get_ingestion_status"]()

    helpers = build_ingestion_helpers(
        run_ingestion, threading.RLock(), now=_fixed_now(times)
    )
    helpers["run_initial_ingestion_async"]()
    done = helpers["get_ingestion_status"]()

    assert seen["after_error"]["state"] == "error"
    assert seen["after_error"]["finished_at"] == "2026-11-09T03:01:00+00:00"
    assert seen["after_step"]["state"] == "running"
    assert seen["after_step"]["step"] == "Updating vectorstore"
    assert seen["after_step"]["error"] is None
    assert seen["after_step"]["finished_at"] is None
    assert seen["after_step"]["run_id"] != seen["after_error"]["run_id"]
    assert seen["after_step"]["started_at"] == "2026-11-09T03:02:00+00:00"
    assert done["state"] == "completed"
    assert done["run_id"] == seen["after_step"]["run_id"]
    assert done["finished_at"] == "2026-11-09T03:05:00+00:00"


def test_the_ingest_ending_during_a_collection_shows_the_upload_step():
    """When the initial ingestion ends while an upload still collects, the status
    shows the collection: ``step`` ``upload`` and no frozen embedding counter."""
    times = [
        datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 9, 3, 5, tzinfo=timezone.utc),
    ]
    ingest_inside = threading.Event()
    release_ingest = threading.Event()

    def run_ingestion(progress_callback, embedding_progress):
        progress_callback("Updating vectorstore")
        embedding_progress(40, 40)
        ingest_inside.set()
        release_ingest.wait(timeout=5)

    helpers = build_ingestion_helpers(
        run_ingestion, threading.RLock(), now=_fixed_now(times)
    )
    ingest = threading.Thread(
        target=helpers["run_initial_ingestion_async"], daemon=True
    )
    ingest.start()
    assert ingest_inside.wait(timeout=5)

    with helpers["track_upload"]():
        while_ingest_runs = helpers["get_ingestion_status"]()
        release_ingest.set()
        ingest.join(timeout=5)
        ingest_ended = not ingest.is_alive()
        after_ingest = helpers["get_ingestion_status"]()
    done = helpers["get_ingestion_status"]()

    assert ingest_ended, "the initial ingestion did not end"
    assert while_ingest_runs["step"] == "Updating vectorstore"
    assert while_ingest_runs["progress"] == {"done": 40, "total": 40}
    assert after_ingest["state"] == "running"
    assert after_ingest["step"] == "upload"
    assert after_ingest["progress"] is None
    assert after_ingest["run_id"] == while_ingest_runs["run_id"]
    assert after_ingest["finished_at"] is None
    assert done["state"] == "completed"
    assert done["step"] == "done"
    assert done["finished_at"] == "2026-11-09T03:05:00+00:00"


def test_the_collection_ending_during_a_refresh_keeps_the_refresh_step():
    """An upload whose collection ends while a refresh holds the lock leaves the
    refresh's step in place: only the lock owner publishes a step while it runs."""
    helpers = _helpers()
    refresh_inside = threading.Event()
    release_refresh = threading.Event()

    def refresh_fn():
        refresh_inside.set()
        release_refresh.wait(timeout=5)

    with helpers["track_upload"]():
        refresh = threading.Thread(
            target=lambda: helpers["run_tracked"]("scheduled:git", refresh_fn),
            daemon=True,
        )
        refresh.start()
        assert refresh_inside.wait(timeout=5)
    while_refresh_runs = helpers["get_ingestion_status"]()
    release_refresh.set()
    refresh.join(timeout=5)

    assert while_refresh_runs["state"] == "running"
    assert while_refresh_runs["step"] == "scheduled:git"
    assert helpers["get_ingestion_status"]()["state"] == "completed"


def test_the_ingest_ending_after_a_failed_collection_keeps_the_error():
    """A collection fails while a second one, in flight before it, still collects: the
    error is shown. The ingest that ends then does not replace it with the upload step.
    """
    ingest_inside = threading.Event()
    release_ingest = threading.Event()

    def run_ingestion(progress_callback, embedding_progress):
        progress_callback("Updating vectorstore")
        ingest_inside.set()
        release_ingest.wait(timeout=5)

    helpers = build_ingestion_helpers(run_ingestion, threading.RLock())
    ingest = threading.Thread(
        target=helpers["run_initial_ingestion_async"], daemon=True
    )
    ingest.start()
    assert ingest_inside.wait(timeout=5)

    with helpers["track_upload"]():
        with pytest.raises(RuntimeError, match="crawl down"):
            with helpers["track_upload"]():
                raise RuntimeError("crawl down")
        release_ingest.set()
        ingest.join(timeout=5)
        after_ingest = helpers["get_ingestion_status"]()

    assert not ingest.is_alive(), "the initial ingestion did not end"
    assert after_ingest["state"] == "error"
    assert after_ingest["step"] == "failed"
    assert after_ingest["error"] == "crawl down"
    assert helpers["get_ingestion_status"]()["state"] == "error"


def test_a_collection_that_starts_after_a_siblings_failure_is_running():
    """A collection fails while a second one still collects: the error is shown.
    A third collection that starts then is live work, so the status shows it as a
    new running run and does not keep the finished failure over it."""
    times = [
        datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 9, 3, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 9, 3, 2, tzinfo=timezone.utc),
        datetime(2026, 11, 9, 3, 5, tzinfo=timezone.utc),
    ]
    helpers = _helpers(now=_fixed_now(times))

    with helpers["track_upload"]():
        with pytest.raises(RuntimeError, match="crawl down"):
            with helpers["track_upload"]():
                raise RuntimeError("crawl down")
        after_failure = helpers["get_ingestion_status"]()
        with helpers["track_upload"]():
            third = helpers["get_ingestion_status"]()
    done = helpers["get_ingestion_status"]()

    assert after_failure["state"] == "error"
    assert after_failure["error"] == "crawl down"
    assert after_failure["finished_at"] == "2026-11-09T03:01:00+00:00"
    assert third["state"] == "running"
    assert third["step"] == "upload"
    assert third["error"] is None
    assert third["finished_at"] is None
    assert third["run_id"] != after_failure["run_id"]
    assert third["started_at"] == "2026-11-09T03:02:00+00:00"
    assert done["state"] == "completed"
    assert done["run_id"] == third["run_id"]
    assert done["finished_at"] == "2026-11-09T03:05:00+00:00"


def test_the_ingest_ending_after_a_failed_collection_alone_keeps_the_error():
    """A collection fails during the initial ingestion's last phase: no sibling in
    flight, no later progress step. The ingest that ends then keeps the failure:
    ``completed`` never replaces an ``error`` the finishing run did not clear."""
    times = [
        datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 9, 3, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 9, 3, 5, tzinfo=timezone.utc),
    ]
    seen = {}

    def run_ingestion(progress_callback, embedding_progress):
        progress_callback("Updating vectorstore")
        with pytest.raises(RuntimeError, match="crawl down"):
            with helpers["track_upload"]():
                raise RuntimeError("crawl down")
        seen["after_error"] = helpers["get_ingestion_status"]()

    helpers = build_ingestion_helpers(
        run_ingestion, threading.RLock(), now=_fixed_now(times)
    )
    helpers["run_initial_ingestion_async"]()
    done = helpers["get_ingestion_status"]()

    assert seen["after_error"]["state"] == "error"
    assert done["state"] == "error"
    assert done["step"] == "failed"
    assert done["error"] == "crawl down"
    assert done["run_id"] == seen["after_error"]["run_id"]
    assert done["finished_at"] == "2026-11-09T03:01:00+00:00"


def test_a_refresh_ending_after_a_failed_collection_alone_keeps_the_error():
    """The same for a refresh that holds the lock: its normal end does not turn
    the collection's published ``error`` into ``completed``."""
    helpers = _helpers()

    def refresh_fn():
        with pytest.raises(RuntimeError, match="crawl down"):
            with helpers["track_upload"]():
                raise RuntimeError("crawl down")

    helpers["run_tracked"]("scheduled:git", refresh_fn)
    status = helpers["get_ingestion_status"]()

    assert status["state"] == "error"
    assert status["step"] == "failed"
    assert status["error"] == "crawl down"


def test_the_ingest_failing_after_a_failed_collection_publishes_its_own_error():
    """A kept failure never hides a later one: the ingest that fails after a
    collection failed publishes its own message."""

    def run_ingestion(progress_callback, embedding_progress):
        progress_callback("Updating vectorstore")
        with pytest.raises(RuntimeError, match="crawl down"):
            with helpers["track_upload"]():
                raise RuntimeError("crawl down")
        raise RuntimeError("embed down")

    helpers = build_ingestion_helpers(run_ingestion, threading.RLock())
    helpers["run_initial_ingestion_async"]()
    status = helpers["get_ingestion_status"]()

    assert status["state"] == "error"
    assert status["step"] == "failed"
    assert status["error"] == "embed down"
