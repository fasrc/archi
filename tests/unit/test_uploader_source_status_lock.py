"""Two uploader status writes that overlap keep both changes (#700).

``FlaskAppWrapper._update_source_status`` did an unlocked read-modify-write of
``ingestion_status.json``. A write that read the file before an overlapping
write finished saved its old copy over it, and that update was lost. The method
now writes through ``src.utils.source_status_file``, which holds a lock around
read -> change -> write. It still logs a write failure and does not raise.
"""

import json
import logging
import threading

import src.interfaces.uploader_app.app as uploader_app_module


def _wrapper(status_file):
    wrapper = object.__new__(uploader_app_module.FlaskAppWrapper)
    wrapper.status_file = status_file
    return wrapper


def test_overlapping_uploader_writes_keep_both_changes(monkeypatch, tmp_path):
    path = tmp_path / "ingestion_status.json"
    path.write_text(json.dumps({"git": {"state": "running"}}))
    wrapper = _wrapper(path)
    real_loads = json.loads
    first_read = threading.Event()
    second_done = threading.Event()

    def loads(*args, **kwargs):
        result = real_loads(*args, **kwargs)
        if threading.current_thread().name == "writer-a" and not first_read.is_set():
            first_read.set()
            # With the lock, the second write cannot finish while this one waits.
            second_done.wait(1.0)
        return result

    monkeypatch.setattr(json, "loads", loads)

    def second():
        if first_read.wait(5):
            wrapper._update_source_status("git", state="error")
        second_done.set()

    threads = [
        threading.Thread(
            target=lambda: wrapper._update_source_status("git", schedule="0 4 * * *"),
            name="writer-a",
        ),
        threading.Thread(target=second, name="writer-b"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert not any(thread.is_alive() for thread in threads)
    assert first_read.is_set()
    assert real_loads(path.read_text()) == {
        "git": {"state": "error", "schedule": "0 4 * * *"}
    }


def test_load_source_status_reads_the_status_file(tmp_path):
    path = tmp_path / "ingestion_status.json"
    path.write_text(json.dumps({"web": {"state": "idle"}}))

    assert _wrapper(path)._load_source_status() == {"web": {"state": "idle"}}


def test_a_failed_status_write_is_logged_and_not_raised(tmp_path, caplog):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    wrapper = _wrapper(blocker / "ingestion_status.json")

    with caplog.at_level(logging.WARNING, logger=uploader_app_module.logger.name):
        wrapper._update_source_status("git", state="idle")

    assert any(
        "Failed to update source status" in r.getMessage() for r in caplog.records
    )
