"""The per-source status file has one locked, atomic writer (#700).

``ingestion_status.json`` had three writers, and each one did an unlocked
read-modify-write of the whole file with a non-atomic ``write_text``. Two writes
that overlapped could lose one update, and a reader could see a partly written
file. ``src/utils/source_status_file.py`` now owns every write.
"""

import json
import logging
import os
import threading
from pathlib import Path

import pytest

import src.interfaces.uploader_app.app as uploader_app_module
from src.utils import source_status_file
from src.utils.source_status_file import (
    load_source_status,
    seed_source_schedules,
    set_source_schedules,
    update_source_status,
)

ROOT = Path(__file__).resolve().parents[2]
DATA_MANAGER = ROOT / "src" / "bin" / "service_data_manager.py"
UPLOADER = ROOT / "src" / "interfaces" / "uploader_app" / "app.py"


def _status_path(tmp_path, content):
    path = tmp_path / "ingestion_status.json"
    path.write_text(json.dumps(content))
    return path


def _pause_first_read_in(monkeypatch, thread_name, resume):
    """Make the first ``json.loads`` in ``thread_name`` wait for ``resume``.

    Return an event that is set when that read has happened. The wait has a
    timeout: with the lock, the other writer cannot finish while this one waits.
    """
    real_loads = json.loads
    read_done = threading.Event()

    def loads(*args, **kwargs):
        result = real_loads(*args, **kwargs)
        if threading.current_thread().name == thread_name and not read_done.is_set():
            read_done.set()
            resume.wait(1.0)
        return result

    monkeypatch.setattr(json, "loads", loads)
    return read_done


def _race(monkeypatch, first_writer, second_writer):
    """Run ``first_writer`` paused after its read while ``second_writer`` runs."""
    second_done = threading.Event()
    first_read = _pause_first_read_in(monkeypatch, "writer-a", second_done)

    def second():
        if first_read.wait(5):
            second_writer()
        second_done.set()

    threads = [
        threading.Thread(target=first_writer, name="writer-a"),
        threading.Thread(target=second, name="writer-b"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not any(thread.is_alive() for thread in threads)
    assert first_read.is_set()


def test_reload_does_not_lose_an_error_written_during_its_read(monkeypatch, tmp_path):
    path = _status_path(
        tmp_path, {"git": {"state": "running", "schedule": "0 1 * * *"}}
    )

    _race(
        monkeypatch,
        lambda: set_source_schedules(path, {"git": "0 4 * * *"}),
        lambda: update_source_status(path, "git", state="error"),
    )

    assert load_source_status(path) == {
        "git": {"state": "error", "schedule": "0 4 * * *"}
    }


def test_uploader_write_does_not_lose_a_refresh_error(monkeypatch, tmp_path):
    path = _status_path(tmp_path, {"git": {"state": "running"}})
    wrapper = object.__new__(uploader_app_module.FlaskAppWrapper)
    wrapper.status_file = path

    _race(
        monkeypatch,
        lambda: wrapper._update_source_status("git", schedule="0 4 * * *"),
        lambda: update_source_status(path, "git", state="error"),
    )

    assert load_source_status(path) == {
        "git": {"state": "error", "schedule": "0 4 * * *"}
    }


def test_write_replaces_the_whole_file_in_one_step(monkeypatch, tmp_path):
    old = {"git": {"state": "idle", "last_run": "2026-11-01T00:00:00+00:00"}}
    path = _status_path(tmp_path, old)
    real_replace = os.replace
    seen = []

    def replace(src, dst):
        src, dst = Path(src), Path(dst)
        seen.append(
            (src, dst, json.loads(dst.read_text()), json.loads(src.read_text()))
        )
        real_replace(src, dst)

    monkeypatch.setattr(source_status_file.os, "replace", replace)

    update_source_status(path, "git", state="error")

    new = {"git": {"state": "error", "last_run": "2026-11-01T00:00:00+00:00"}}
    assert len(seen) == 1
    src, dst, target_before, temp_content = seen[0]
    assert dst == path
    assert src != path and src.parent == path.parent
    assert target_before == old
    assert temp_content == new
    assert load_source_status(path) == new
    assert [p.name for p in tmp_path.iterdir()] == ["ingestion_status.json"]


def test_a_failed_write_keeps_the_old_file_and_leaves_no_temp_file(
    monkeypatch, tmp_path
):
    old = {"git": {"state": "idle"}}
    path = _status_path(tmp_path, old)

    def fsync(_fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(source_status_file.os, "fsync", fsync)

    with pytest.raises(OSError):
        update_source_status(path, "git", state="error")

    assert json.loads(path.read_text()) == old
    assert [p.name for p in tmp_path.iterdir()] == ["ingestion_status.json"]


def test_update_changes_only_the_given_fields(tmp_path):
    path = _status_path(
        tmp_path,
        {
            "git": {"state": "idle", "last_run": "t0", "schedule": "0 1 * * *"},
            "jira": {"state": "idle"},
        },
    )

    update_source_status(path, "git", state="running")
    update_source_status(path, "web", state="idle", last_run="t1")

    assert load_source_status(path) == {
        "git": {"state": "running", "last_run": "t0", "schedule": "0 1 * * *"},
        "jira": {"state": "idle"},
        "web": {"state": "idle", "last_run": "t1"},
    }


def test_an_empty_schedule_removes_the_schedule(tmp_path):
    path = _status_path(tmp_path, {"git": {"state": "idle", "schedule": "0 1 * * *"}})

    update_source_status(path, "git", schedule="")

    assert load_source_status(path) == {"git": {"state": "idle"}}


def test_update_creates_the_file_and_its_directory(tmp_path):
    path = tmp_path / "data" / "ingestion_status.json"

    update_source_status(path, "git", state="idle")

    assert load_source_status(path) == {"git": {"state": "idle"}}


def test_update_replaces_an_entry_that_is_not_a_mapping(tmp_path):
    path = _status_path(tmp_path, {"git": "broken", "jira": {"state": "idle"}})

    update_source_status(path, "git", state="error")

    assert load_source_status(path) == {
        "git": {"state": "error"},
        "jira": {"state": "idle"},
    }


def test_load_returns_empty_for_an_absent_file(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert load_source_status(tmp_path / "ingestion_status.json") == {}
    assert not caplog.records


def test_load_returns_empty_and_warns_for_an_unreadable_file(tmp_path, caplog):
    path = tmp_path / "ingestion_status.json"
    path.write_text('{"git": {"state": "id')

    with caplog.at_level(logging.WARNING, logger=source_status_file.__name__):
        assert load_source_status(path) == {}
    assert any("source status" in r.getMessage() for r in caplog.records)


def test_load_returns_empty_and_warns_for_content_that_is_not_a_mapping(
    tmp_path, caplog
):
    path = _status_path(tmp_path, ["git"])

    with caplog.at_level(logging.WARNING, logger=source_status_file.__name__):
        assert load_source_status(path) == {}
    assert any("not a JSON object" in r.getMessage() for r in caplog.records)


def test_seed_adds_scheduled_sources_and_keeps_existing_values(tmp_path):
    path = _status_path(
        tmp_path,
        {"git": {"state": "error", "schedule": "0 1 * * *", "last_run": "t0"}},
    )

    seed_source_schedules(path, {"git": "0 2 * * *", "jira": "0 3 * * *", "links": ""})

    assert load_source_status(path) == {
        "git": {"state": "error", "schedule": "0 1 * * *", "last_run": "t0"},
        "jira": {"schedule": "0 3 * * *", "state": "idle"},
    }


def test_seed_creates_the_file_when_no_source_is_scheduled(tmp_path):
    path = tmp_path / "ingestion_status.json"

    seed_source_schedules(path, {})

    assert path.exists()
    assert load_source_status(path) == {}


def test_set_schedules_changes_only_sources_that_have_an_entry(tmp_path):
    path = _status_path(
        tmp_path, {"git": {"state": "idle", "schedule": "0 1 * * *"}, "web": "bad"}
    )

    set_source_schedules(path, {"git": "0 4 * * *", "jira": "0 5 * * *", "web": "x"})

    assert load_source_status(path) == {
        "git": {"state": "idle", "schedule": "0 4 * * *"},
        "web": "bad",
    }


def test_no_writer_reads_or_writes_the_status_file_directly():
    for path in (DATA_MANAGER, UPLOADER):
        text = path.read_text()
        assert "write_text" not in text, path
        assert "json.loads(self.status_file" not in text, path
        assert "status_file.read_text" not in text, path
        assert "from src.utils.source_status_file import" in text, path
