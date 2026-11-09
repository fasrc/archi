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
