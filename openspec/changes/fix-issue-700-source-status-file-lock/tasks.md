# Tasks — lock and atomically write the per-source status file (#700)

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
tests, watch them fail, write the smallest fix, run the gate, commit — in ONE task. Never end a
task with the suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** Files this change may edit: `src/utils/source_status_file.py` (new),
  `src/bin/service_data_manager.py`, `src/interfaces/uploader_app/app.py`,
  `tests/unit/test_source_status_file.py` (new),
  `tests/unit/test_uploader_source_status_lock.py` (new),
  `tests/unit/test_service_data_manager_source_status.py` (new), and this change's
  `tasks.md`. Do NOT edit `src/utils/ingestion_status.py`,
  `tests/unit/test_api_reference_schedule_state_contract.py`, `docs/**`, `deploy/**`,
  `.github/**`, or any control-plane file.
- **Read `design.md` first.** Loop 1 ran every code block in the Appendix below in a scratch
  worktree of `origin/dev` `e83160e6`, and three independent reviewers re-ran them (red: see
  task 1.1; green: the three new files `22 passed`, the fast loop `61 passed`, the full unit
  suite `7927 passed, 60 skipped, 1 xfailed` on the host, patch coverage 100 %, 82 lines).
  **Copy the Appendix code exactly. Do not rewrite it.**
- **Fast loop:** `python -m pytest -q tests/unit/test_source_status_file.py tests/unit/test_uploader_source_status_lock.py tests/unit/test_service_data_manager_source_status.py tests/unit/test_api_reference_schedule_state_contract.py tests/unit/test_ingestion_status_lock.py tests/unit/test_service_data_manager_parent_id_index.py`
- **The race tests wait 1.0 s each by design** (design D8): with the lock, the second writer
  cannot finish while the first one waits, so the wait always runs to its timeout. Do not
  shorten it, remove the timeout, or replace the event with a `threading.Barrier`.
- **Stage before you gate.** Run `black` and `isort` on the changed `.py` files, then
  `git add` all six files, THEN run `bash scripts/gate.sh`, then commit, and confirm
  `git status --porcelain` is empty. The order matters: diff-cover does not score untracked
  files, so a gate run while the three new files are untracked leaves out
  `src/utils/source_status_file.py` and can report a false low patch coverage.

## 1. One locked, atomic writer for the status file

- [ ] 1.1 Create `tests/unit/test_uploader_source_status_lock.py` from Appendix C, `tests/unit/test_source_status_file.py` from Appendix B, and `tests/unit/test_service_data_manager_source_status.py` from Appendix E. Do NOT create the module yet. Record the red results: `python -m pytest -q tests/unit/test_uploader_source_status_lock.py` gives exactly `1 failed, 2 passed` (`test_overlapping_uploader_writes_keep_both_changes` fails: the file ends with `'state': 'running'`, the lost update of issue #700); `python -m pytest -q tests/unit/test_source_status_file.py` stops at collection with an error that starts with `ImportError: cannot import name 'source_status_file' from 'src.utils'`; and `python -m pytest -q tests/unit/test_service_data_manager_source_status.py` gives `4 passed` (it pins the `main()` wiring, which is already correct on dev; it fails if a later edit routes a writer wrongly). Then create `src/utils/source_status_file.py` from Appendix A and make the source edits of Appendix D (design D7): save the Appendix D block to `/tmp/700-src.patch` and run `git apply /tmp/700-src.patch`; if it does not apply, make the same edits by hand. Run `python -m pytest -q tests/unit/test_source_status_file.py tests/unit/test_uploader_source_status_lock.py tests/unit/test_service_data_manager_source_status.py` green: exactly `22 passed`. Run the fast loop green: exactly `61 passed`. Run `grep -n "write_text\|json.loads(self.status_file" src/bin/service_data_manager.py src/interfaces/uploader_app/app.py` and confirm it prints nothing. Format and `git add` the six files, run `bash scripts/gate.sh` green, then commit tests and source together as `fix(#700): lock and atomically write the per-source status file` (the red runs and the fix are one commit).

## 2. Publish

- [ ] 2.1 Run `bash scripts/gate.sh` one more time on the branch tip and confirm it is green. Tick this box (2.1) in this `tasks.md`, commit the tick through the hook as `chore(#700): mark the publish task done`, and only then push: `git push -u origin fix/issue-700-source-status-file-lock` (`-u` sets the upstream to the branch's own remote name). Confirm `git rev-parse HEAD` equals `git rev-parse origin/fix/issue-700-source-status-file-lock`. Open the PR with `gh pr create --repo fasrc/archi --base dev --head fix/issue-700-source-status-file-lock --title "fix: lock and atomically write the per-source status file (#700)" --body-file <file>`. The PR body must contain the line `closes #700` (in the body, not only the title), and must state: (a) the new module `src/utils/source_status_file.py`, one process-wide `threading.Lock` around read → change → write, and the atomic write (same-directory temp file, `fsync`, `os.replace`); (b) the four writers routed through it (`set_source_status`, the start-up seed, `reload_schedules`, `FlaskAppWrapper._update_source_status`), with every uploader call site unchanged, and the new `main()` test that drives the three data-manager writers; (c) the red evidence: on `origin/dev` the overlapping-writes test ends with `state == "running"` (the `error` write is lost); (d) the merge order: the issue's Decision says to land this before #699 (PR #802); Loop 1 merged this change with PR #802's head `7b6517aa` with no conflict and the tests of both passed on the merged tree, so merge this PR first and rebase #802; (e) no docs change, because `GET /api/schedules` returns the same fields with the same meaning (AGENTS.md docs rule); (f) three points for the reviewer to decide, from design D3, D6 and the Non-Goals: readers take no lock; an unreadable file still reads as `{}` and the next write replaces it — this includes a transient read `OSError` (EIO, EACCES) on an intact file, which erases the other sources' entries exactly as on `dev` today, and a strict read on the write path would stop that but makes a refresh or the start-up fail on that error instead; and with `services.data_manager.flask_debug_mode: true` Werkzeug's reloader runs `main()` in two processes, so the lock covers only one of them (default off, unchanged). Link the first use of "ingest" to `https://github.com/fasrc/archi/blob/dev/docs/docs/glossary.md#ingest-ingestion`. No `Co-Authored-By` trailer. Do not merge. Confirm the PR links the issue: `gh pr view <n> --repo fasrc/archi --json closingIssuesReferences` lists `700`.

## Appendix (reference only — no checkboxes)

### A. New file `src/utils/source_status_file.py`

```python
"""One owner for the per-source status file ``ingestion_status.json`` (#700).

``GET /api/schedules`` reads this file. Three writers change it: the data
manager's ``set_source_status`` (scheduled refreshes), its ``reload_schedules``
and start-up seed, and the uploader's ``FlaskAppWrapper._update_source_status``.
All of them run in the data-manager process (``service_data_manager.main``
builds the uploader), so one process-wide lock is enough. That is true while
``services.data_manager.flask_debug_mode`` is false (the default): in debug mode
Werkzeug's reloader runs ``main()`` again in a second process, and this lock
does not cover writes from the other process.

Each write holds ``_LOCK`` around read -> change -> write, so two writes that
overlap cannot lose an update. The write is atomic: the new content goes to a
temporary file in the same directory, then ``os.replace`` puts it in place, so a
reader sees the old file or the new file, never a part of one. Readers do not
take the lock. The lock is a leaf: code that holds it takes no other lock of
this service (a log call takes only the logging handler's own lock).
"""

import json
import os
import threading
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional

from src.utils.logging import get_logger

logger = get_logger(__name__)

SourceStatus = Dict[str, Dict[str, str]]

_LOCK = threading.Lock()


def load_source_status(path: Path) -> SourceStatus:
    """Return the file's content, or ``{}`` when it is absent or unreadable.

    An absent file is not logged. An unreadable file, or content that is not a
    JSON object, logs a WARNING. A write after it replaces the whole file.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("Failed to read source status file %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Source status file %s is not a JSON object; ignored", path)
        return {}
    return data


def _write_atomic(path: Path, data: SourceStatus) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _mutate(path: Path, change: Callable[[SourceStatus], None]) -> None:
    path = Path(path)
    with _LOCK:
        data = load_source_status(path)
        change(data)
        _write_atomic(path, data)


def update_source_status(
    path: Path,
    source: str,
    *,
    state: Optional[str] = None,
    last_run: Optional[str] = None,
    schedule: Optional[str] = None,
) -> None:
    """Change the given fields of one source's entry; keep every other field.

    An empty ``schedule`` removes the entry's schedule. A write error raises.
    """

    def change(data: SourceStatus) -> None:
        entry = data.get(source)
        if not isinstance(entry, dict):
            entry = {}
        if state is not None:
            entry["state"] = state
        if last_run is not None:
            entry["last_run"] = last_run
        if schedule is not None:
            if schedule:
                entry["schedule"] = schedule
            else:
                entry.pop("schedule", None)
        data[source] = entry

    _mutate(path, change)


def seed_source_schedules(path: Path, schedules: Mapping[str, str]) -> None:
    """Give each scheduled source an entry; keep the values an entry already has.

    A new entry gets the schedule and ``state == "idle"``.
    """

    def change(data: SourceStatus) -> None:
        for source, schedule in schedules.items():
            if not schedule:
                continue
            entry = data.get(source)
            if not isinstance(entry, dict):
                entry = {}
            entry.setdefault("schedule", schedule)
            entry.setdefault("state", "idle")
            data[source] = entry

    _mutate(path, change)


def set_source_schedules(path: Path, schedules: Mapping[str, str]) -> None:
    """Set the schedule of each source that already has an entry."""

    def change(data: SourceStatus) -> None:
        for source, schedule in schedules.items():
            entry = data.get(source)
            if isinstance(entry, dict):
                entry["schedule"] = schedule

    _mutate(path, change)
```

### B. New file `tests/unit/test_source_status_file.py`

```python
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
```

### C. New file `tests/unit/test_uploader_source_status_lock.py`

```python
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
```

### D. Source edits (`git apply` this block from the repository root)

Made with `git diff` against `origin/dev` `e83160e6`. It changes only
`src/bin/service_data_manager.py` and `src/interfaces/uploader_app/app.py`.

```diff
diff --git a/src/bin/service_data_manager.py b/src/bin/service_data_manager.py
index da136923..f9c78e51 100644
--- a/src/bin/service_data_manager.py
+++ b/src/bin/service_data_manager.py
@@ -1,5 +1,4 @@
 #!/bin/python
-import json
 import os
 import threading
 from pathlib import Path
@@ -22,6 +21,12 @@ from src.utils.env import read_secret
 from src.utils.ingestion_status import build_ingestion_helpers
 from src.utils.logging import get_logger, setup_logging
 from src.utils.postgres_service_factory import PostgresServiceFactory
+from src.utils.source_status_file import (
+    load_source_status,
+    seed_source_schedules,
+    set_source_schedules,
+    update_source_status,
+)
 from src.utils.telemetry import instrument_flask_app
 
 logger = get_logger(__name__)
@@ -84,28 +89,12 @@ def main() -> None:
     _ing = build_ingestion_helpers(data_manager.run_ingestion, lock)
 
     def load_status() -> Dict[str, Dict[str, str]]:
-        if not status_file.exists():
-            return {}
-        try:
-            return json.loads(status_file.read_text())
-        except Exception:
-            return {}
-
-    def save_status(data: Dict[str, Dict[str, str]]) -> None:
-        status_file.parent.mkdir(parents=True, exist_ok=True)
-        status_file.write_text(json.dumps(data))
+        return load_source_status(status_file)
 
     def set_source_status(
         source: str, *, state: str | None = None, last_run: str | None = None
     ) -> None:
-        data = load_status()
-        entry = data.get(source, {})
-        if state is not None:
-            entry["state"] = state
-        if last_run is not None:
-            entry["last_run"] = last_run
-        data[source] = entry
-        save_status(data)
+        update_source_status(status_file, source, state=state, last_run=last_run)
 
     def run_locked(name: str, func: Callable[[], None]) -> None:
         update = data_manager.update_vectorstore
@@ -200,20 +189,15 @@ def main() -> None:
     logger.info("Loaded source schedules: %s", db_schedules)
 
     # seed status with schedules
-    initial_status = load_status()
+    seed_source_schedules(status_file, db_schedules)
 
     for source_name, schedule in db_schedules.items():
         if schedule:
-            entry = initial_status.get(source_name, {})
-            entry.setdefault("schedule", schedule)
-            entry.setdefault("state", "idle")
-            initial_status[source_name] = entry
             scheduler.add_job(
                 name=source_name,
                 cron=schedule,
                 callback=create_job_callback(source_name),
             )
-    save_status(initial_status)
 
     if scheduler.jobs:
         scheduler.start()
@@ -254,11 +238,7 @@ def main() -> None:
             if scheduler.jobs:
                 scheduler.start()
             # Update status file with new schedules
-            status = load_status()
-            for source_name, schedule in new_schedules.items():
-                if source_name in status:
-                    status[source_name]["schedule"] = schedule
-            save_status(status)
+            set_source_schedules(status_file, new_schedules)
             return jsonify(
                 {
                     "success": True,
diff --git a/src/interfaces/uploader_app/app.py b/src/interfaces/uploader_app/app.py
index 265938a7..0dcbdeec 100644
--- a/src/interfaces/uploader_app/app.py
+++ b/src/interfaces/uploader_app/app.py
@@ -35,6 +35,7 @@ from src.utils.catalog_query import parse_metadata_query
 from src.utils.config_access import get_full_config
 from src.utils.env import read_secret
 from src.utils.logging import get_logger
+from src.utils.source_status_file import load_source_status, update_source_status
 
 logger = get_logger(__name__)
 
@@ -514,15 +515,7 @@ class FlaskAppWrapper:
             logger.warning("Post-update hook failed: %s", exc)
 
     def _load_source_status(self) -> Dict[str, Dict[str, str]]:
-        if not self.status_file.exists():
-            return {}
-        try:
-            import json
-
-            return json.loads(self.status_file.read_text())
-        except Exception as exc:
-            logger.warning("Failed to read source status file: %s", exc)
-            return {}
+        return load_source_status(self.status_file)
 
     def _update_source_status(
         self,
@@ -533,25 +526,16 @@ class FlaskAppWrapper:
         schedule: Optional[str] = None,
     ) -> None:
         try:
-            import json
-
-            data = self._load_source_status()
-            entry = data.get(source, {})
-            if state is not None:
-                entry["state"] = state
-            if last_run is not None:
-                entry["last_run"] = last_run
-            if schedule is not None:
-                if schedule:
-                    entry["schedule"] = schedule
-                else:
-                    entry.pop("schedule", None)
-            data[source] = entry
+            update_source_status(
+                self.status_file,
+                source,
+                state=state,
+                last_run=last_run,
+                schedule=schedule,
+            )
             logger.debug(
                 f"Updated source status with state {state}, last_run: {last_run}, schedule: {schedule}"
             )
-            self.status_file.parent.mkdir(parents=True, exist_ok=True)
-            self.status_file.write_text(json.dumps(data))
         except Exception as exc:
             logger.warning("Failed to update source status: %s", exc)
 
```

### E. New file `tests/unit/test_service_data_manager_source_status.py`

```python
"""The data-manager main() writes the per-source status through one module (#700).

``main()`` has three status writers: the start-up seed, ``set_source_status``
(called by a scheduled refresh through ``run_source_refresh``) and the
``/api/reload-schedules`` route. This drives ``main()`` with fakes for the
database, the collectors, the scheduler and the uploader, and checks what each
writer leaves in ``ingestion_status.json``.
"""

import json

import pytest

import src.bin.service_data_manager as service


class _FakeFactory:
    @classmethod
    def from_env(cls, password_override=None):
        return cls()

    @classmethod
    def set_instance(cls, factory):
        pass


class _FakeScraperManager:
    def __init__(self, calls):
        self.calls = calls

    def schedule_collect_git(self, persistence, last_run=None):
        self.calls.append(("collect_git", last_run))


class _FakeDataManager:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.persistence = object()
        self.scraper_manager = _FakeScraperManager(self.calls)
        self.update_result = None

    def run_ingestion(self, progress_callback=None, embedding_progress=None):
        return None

    def update_vectorstore(self, force=False):
        return self.update_result


class _FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.reload_result = {}

    def set_config_loader(self, loader, job_factory):
        pass

    def add_job(self, name, cron, callback):
        self.jobs[name] = callback

    def start(self):
        pass

    def reload_schedules(self):
        return self.reload_result

    def get_job_status(self):
        return [{"name": name} for name in self.jobs]


@pytest.fixture
def started(monkeypatch, tmp_path):
    """Run main() to its end; return the fakes, the Flask app and the file."""
    made = {}
    status_file = tmp_path / "ingestion_status.json"
    status_file.write_text(
        json.dumps({"git": {"state": "idle", "last_run": "t0"}, "web": {"state": "x"}})
    )

    def data_manager(*args, **kwargs):
        made["data_manager"] = _FakeDataManager()
        return made["data_manager"]

    def scheduler():
        made["scheduler"] = _FakeScheduler()
        return made["scheduler"]

    class _FakeUploader:
        def __init__(self, app, *, post_update_hook=None, status_file=None):
            made["app"] = app
            made["status_file"] = status_file

        def run(self, **kwargs):
            pass

    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.setenv(key, "unchanged")
    sources = {"git": {"schedule": "0 3 * * *"}, "jira": {"schedule": "0 4 * * *"}}
    monkeypatch.setattr(service, "setup_logging", lambda: None)
    monkeypatch.setattr(service, "read_secret", lambda name: "secret")
    monkeypatch.setattr(service, "PostgresServiceFactory", _FakeFactory)
    monkeypatch.setattr(service, "build_parent_id_index_at_startup", lambda f: True)
    monkeypatch.setattr(
        service, "get_full_config", lambda: {"data_manager": {"sources": sources}}
    )
    monkeypatch.setattr(
        service,
        "get_services_config",
        lambda: {"data_manager": {"flask_debug_mode": False, "port": 0, "host": "x"}},
    )
    monkeypatch.setattr(
        service, "get_global_config", lambda: {"DATA_PATH": str(tmp_path)}
    )
    monkeypatch.setattr(service, "DataManager", data_manager)
    monkeypatch.setattr(service, "CronScheduler", scheduler)
    monkeypatch.setattr(service, "instrument_flask_app", lambda app: None)
    monkeypatch.setattr(service, "FlaskAppWrapper", _FakeUploader)

    service.main()

    assert made["status_file"] == status_file
    return made, status_file


def _read(status_file):
    return json.loads(status_file.read_text())


def test_start_seeds_scheduled_sources_and_keeps_existing_entries(started):
    _, status_file = started

    assert _read(status_file) == {
        "git": {"state": "idle", "last_run": "t0", "schedule": "0 3 * * *"},
        "web": {"state": "x"},
        "jira": {"schedule": "0 4 * * *", "state": "idle"},
    }


def test_scheduled_refresh_writes_error_then_idle_through_the_file(started):
    made, status_file = started
    data_manager = made["data_manager"]
    refresh_git = made["scheduler"].jobs["git"]

    data_manager.update_result = "failed"
    with pytest.raises(RuntimeError):
        refresh_git()

    assert data_manager.calls == [("collect_git", "t0")]
    assert _read(status_file)["git"] == {
        "state": "error",
        "last_run": "t0",
        "schedule": "0 3 * * *",
    }

    data_manager.update_result = None
    refresh_git()

    git = _read(status_file)["git"]
    assert git["state"] == "idle"
    assert git["last_run"] not in (None, "t0")
    assert git["schedule"] == "0 3 * * *"


def test_reload_sets_schedules_only_on_existing_entries(started):
    made, status_file = started
    made["scheduler"].reload_result = {"git": "0 5 * * *", "links": "0 6 * * *"}

    response = made["app"].test_client().post("/api/reload-schedules")

    assert response.status_code == 200
    status = _read(status_file)
    assert status["git"]["schedule"] == "0 5 * * *"
    assert "links" not in status


def test_schedules_route_reads_the_state_from_the_file(started):
    made, _ = started

    response = made["app"].test_client().get("/api/schedules")

    jobs = {job["name"]: job for job in response.get_json()["jobs"]}
    assert jobs["git"]["state"] == "idle"
    assert jobs["git"]["last_run"] == "t0"
    assert jobs["jira"]["state"] == "idle"
    assert jobs["jira"]["last_run"] is None
```
