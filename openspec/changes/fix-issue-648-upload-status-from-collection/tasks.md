## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-648-upload-status-from-collection` exists, cut from
      `origin/dev` at `f457d3d1`, with `origin/fix/issue-559-ingest-run-identity-v2`
      (`97543dd2`, PR #777) merged in as its first commit. You are on it. Do not re-branch, do
      not rebase, and do not merge `dev` or any other branch.
- [x] 0.2 `openspec validate fix-issue-648-upload-status-from-collection --strict` passed on
      the host. Do not run the `openspec` CLI in the container, and do not add a task that
      does.
- [x] 0.3 Every design decision is made in `design.md` (D1–D7). Follow the design; where the
      issue body and the design differ, the design wins.
- [x] 0.4 Loop 1 ran every check of tasks 1.1 and 2.1 on the host, red and green, with the
      exact text in the appendices: Appendix A red on the unmodified tip is
      `23 failed, 10 passed`; with Appendix B applied, the nine-file set of task 1.1(d) is
      `100 passed`, the patch coverage of `src/interfaces/uploader_app/app.py` and
      `src/utils/ingestion_status.py` is 100% (50 of 50 lines), and `black --check src tests`
      and `isort --check-only src tests` are clean. Copy the appendices; do not rewrite them.

## Rules that apply to EVERY task below

- **Loop image gap (#776).** The `archi-loop` image can lack `slack_sdk`; without it pytest
  stops at collection in `tests/unit/test_slack_*.py` and the pre-commit hook refuses every
  commit. At the start of each turn run `python -c "import slack_sdk"`; if it fails, run
  `pip install slack_sdk==3.45.0` before any test or gate run. Do not edit any requirements
  file for this.
- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed.
- Each of tasks 1.1 and 2.1 ends with exactly one commit (tick its checkbox in this file in
  the same commit).
- Run the gate and every `git commit` in the FOREGROUND, with no pipe and no redirect. Never
  put a command in the background and end the turn to wait for it.
- `tests/unit/test_ingestion_status_upload_tracking.py` and
  `tests/unit/test_uploader_upload_tracking.py` are NEW files. Before task 1.1 creates them,
  `test -e` on each must fail.
- Do not delete an existing test. Edit an existing test only where task 1.1 says so
  (`tests/unit/test_service_data_manager_source_status.py`: the `_FakeUploader` signature, an
  `import time`, and two new functions at the end). After each commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- Run `black` and `isort` on every changed `.py` file **before** `git add` (on the staged run
  they changed nothing after the appendices). After each commit, `git status --porcelain`
  must be empty.
- Never bypass the commit hook. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Do not edit `src/interfaces/chat_app/**`, any UI file,
  `openspec/changes/fix-issue-559-ingest-run-identity-v2/**`,
  `tests/unit/test_ingestion_status_lock.py`, or any control-plane file. Under `docs/`, edit
  only `docs/docs/api_reference.md` and `docs/docs/benchmarking.md` (task 2.1). The PR body
  goes in `/tmp`, never in the repo. Do not add an entry to `docs/questions.md`.

## 1. Report an upload from the start of its collection

- [x] 1.1 Test first, then implement design D1–D5, one commit
      (`fix(#648): report an upload from the start of its collection`).
      (a) Apply Appendix A: copy the block into `/tmp/648-tests.diff` and run
      `git apply /tmp/648-tests.diff`. It creates the two new test files and edits
      `tests/unit/test_service_data_manager_source_status.py`. If it does not apply, make the
      same edits by hand, byte for byte.
      (b) Red: run
      `python -m pytest -q tests/unit/test_ingestion_status_upload_tracking.py tests/unit/test_uploader_upload_tracking.py tests/unit/test_service_data_manager_source_status.py`
      and see exactly `23 failed, 10 passed` (a thread-exception warning from the lock test is
      expected). Per file: the status file `6 failed` (five `KeyError: 'track_upload'`, one
      `IndexError`); the uploader file `16 failed, 6 passed` (the 6 passes are the five
      `test_handler_without_a_tracker_runs_as_before[...]` cases and
      `test_remove_git_repo_not_found_does_not_enter_the_tracker`, which pin behavior that
      must not change); the data-manager file `1 failed, 4 passed`
      (`TypeError: 'NoneType' object is not callable`).
      (c) Apply Appendix B the same way (`/tmp/648-impl.diff`): `src/utils/ingestion_status.py`
      (`track_upload`, `_finish` with `keep_error`, imports, docstrings, the returned dict),
      `src/interfaces/uploader_app/app.py` (imports, the class default, the `__init__`
      keyword, five handlers, `_tracked_upload`), and `src/bin/service_data_manager.py` (one
      keyword).
      (d) Green: run
      `python -m pytest -q tests/unit/test_ingestion_status_upload_tracking.py tests/unit/test_uploader_upload_tracking.py tests/unit/test_service_data_manager_source_status.py tests/unit/test_ingestion_status_lock.py tests/unit/test_uploader_manual_ingest_status.py tests/unit/test_uploader_embed_endpoint.py tests/unit/test_api_reference_schedule_state_contract.py tests/unit/test_service_data_manager_parent_id_index.py tests/unit/test_uploader_source_status_lock.py`
      and see `100 passed`. Then `grep -c '_tracked_upload(' src/interfaces/uploader_app/app.py`
      must print `6` (five call sites plus the `def`),
      `grep -c 'keep_error' src/utils/ingestion_status.py` must print `4`, and
      `grep -c 'track_upload' src/bin/service_data_manager.py` must print `1`.
      (e) Tick this box, `git add` the six files and this file, and commit through the hook.

## 2. Docs and publish

- [x] 2.1 Apply Appendix C (`/tmp/648-docs.diff`) to `docs/docs/api_reference.md` and
      `docs/docs/benchmarking.md` (design D6); nothing else changes. Then
      `python -m pytest -q tests/unit/test_api_reference_schedule_state_contract.py tests/unit/test_benchmarking_docs_inline_bundle.py`
      must show `10 passed`,
      `grep -c 'from the start of its collection' docs/docs/api_reference.md` and
      `grep -c 'from the start of its collection' docs/docs/benchmarking.md` must each
      print `1`, and `grep -c 'collection phase of an upload' docs/docs/benchmarking.md` must
      print `0`. Tick this box and commit through the hook
      (`docs(#648): an upload is reported from the start of its collection`).
- [x] 2.2 Do the steps of this task in this order: check, tick this box, commit the tick
      through the hook, push, then open the PR. The tick must ride the push that opens the
      PR; after the PR opens, `git status --porcelain` must be empty and
      `git rev-parse HEAD` must equal
      `git rev-parse origin/fix/issue-648-upload-status-from-collection`.
      Run `bash scripts/gate.sh` in the foreground on the tip and confirm it exits 0. Confirm
      `git diff origin/dev --stat` lists only `src/utils/ingestion_status.py`,
      `src/interfaces/uploader_app/app.py`, `src/bin/service_data_manager.py`,
      `tests/unit/test_ingestion_status_upload_tracking.py`,
      `tests/unit/test_uploader_upload_tracking.py`,
      `tests/unit/test_service_data_manager_source_status.py`,
      `tests/unit/test_ingestion_status_lock.py`, `docs/docs/api_reference.md`,
      `docs/docs/benchmarking.md`, and files under
      `openspec/changes/fix-issue-648-upload-status-from-collection/` and
      `openspec/changes/fix-issue-559-ingest-run-identity-v2/`
      (`test_ingestion_status_lock.py` and the #559 folder come from the merged PR #777; do
      not touch them). Push with `git push -u origin fix/issue-648-upload-status-from-collection`.
      Write the PR body to `/tmp/pr-648.md`: line 1 is `Closes #648` (in the body, never in
      the title); then the line "**Stacked on #777 (#559):** this branch merges
      `fix/issue-559-ingest-run-identity-v2`, so the diff shows #777's three commits until
      #777 merges. Merge #777 first; this PR's diff then reduces to its own files. Findings on
      `run_id`, `started_at` or `finished_at` belong to #777."; a per-task summary; the D3
      table from `design.md`; the gate result; a line that names the one edited existing test
      (`tests/unit/test_service_data_manager_source_status.py`, design D5); and a
      "Post-merge operator check" line: after a redeploy, add a URL with depth 2 in the
      uploader and, while it crawls, confirm
      `curl -s http://localhost:7871/api/ingestion/status` shows `"state": "running"` and
      `"step": "upload"` before the data-manager log prints `Updating vectorstore`, and
      `"completed"` only after the update ends. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-648-upload-status-from-collection --title "ingestion status: report an upload from the start of its collection" --body-file /tmp/pr-648.md`.
      The only file this task changes is this checkbox, and that change is committed and
      pushed before `gh pr create`. The task is complete when
      `gh pr view --repo fasrc/archi fix/issue-648-upload-status-from-collection` prints a PR
      whose base repository is `fasrc/archi` (not a fork).

### Appendix A (for task 1.1): tests — `git apply`

````diff
diff --git c/tests/unit/test_ingestion_status_upload_tracking.py w/tests/unit/test_ingestion_status_upload_tracking.py
new file mode 100644
index 00000000..3e7d777b
--- /dev/null
+++ w/tests/unit/test_ingestion_status_upload_tracking.py
@@ -0,0 +1,143 @@
+"""An upload is reported as running from the start of its collection (issue #648).
+
+``track_upload`` counts an upload into the ingestion status before its
+collection starts, without taking the ingestion lock, and counts it out after
+its vectorstore update ended. Between the two the status never shows
+``completed``. A block that raises publishes ``error``; a block whose update
+published ``error`` keeps that error.
+"""
+
+import threading
+from datetime import datetime, timezone
+
+import pytest
+
+from src.utils.ingestion_status import build_ingestion_helpers
+
+
+def _helpers(lock=None, **kwargs):
+    return build_ingestion_helpers(
+        lambda **_: None, lock or threading.RLock(), **kwargs
+    )
+
+
+def _fixed_now(times):
+    iterator = iter(times)
+    return lambda: next(iterator)
+
+
+def test_collection_is_reported_as_a_running_upload():
+    helpers = _helpers()
+
+    with helpers["track_upload"]():
+        during = helpers["get_ingestion_status"]()
+    after = helpers["get_ingestion_status"]()
+
+    assert during["state"] == "running"
+    assert during["step"] == "upload"
+    assert during["progress"] is None
+    assert after["state"] == "completed"
+    assert after["step"] == "done"
+    assert after["error"] is None
+
+
+def test_completed_is_not_published_between_collection_and_update():
+    helpers = _helpers()
+    seen = []
+
+    with helpers["track_upload"]():
+        seen.append(helpers["get_ingestion_status"]())
+        helpers["run_upload_update"](lambda **_: "updated")
+        seen.append(helpers["get_ingestion_status"]())
+
+    assert [status["state"] for status in seen] == ["running", "running"]
+    assert [status["step"] for status in seen] == ["upload", "upload"]
+    assert helpers["get_ingestion_status"]()["state"] == "completed"
+
+
+def test_collection_does_not_wait_for_the_ingestion_lock():
+    lock = threading.RLock()
+    helpers = _helpers(lock)
+    refresh_inside = threading.Event()
+    release_refresh = threading.Event()
+
+    def refresh_fn():
+        refresh_inside.set()
+        release_refresh.wait(timeout=5)
+
+    refresh = threading.Thread(
+        target=lambda: helpers["run_tracked"]("scheduled:git", refresh_fn),
+        daemon=True,
+    )
+    refresh.start()
+    assert refresh_inside.wait(timeout=5)
+
+    during = []
+
+    def collect():
+        with helpers["track_upload"]():
+            during.append(helpers["get_ingestion_status"]())
+
+    upload = threading.Thread(target=collect, daemon=True)
+    upload.start()
+    upload.join(timeout=5)
+    collected_while_locked = not upload.is_alive()
+    while_refresh_runs = helpers["get_ingestion_status"]()
+    release_refresh.set()
+    refresh.join(timeout=5)
+
+    assert collected_while_locked, "the collection waited for the ingestion lock"
+    assert during[0]["state"] == "running"
+    assert during[0]["step"] == "scheduled:git"
+    assert while_refresh_runs["state"] == "running"
+    assert helpers["get_ingestion_status"]()["state"] == "completed"
+
+
+def test_a_failed_collection_publishes_error():
+    helpers = _helpers()
+
+    with pytest.raises(RuntimeError, match="crawl down"):
+        with helpers["track_upload"]():
+            raise RuntimeError("crawl down")
+
+    status = helpers["get_ingestion_status"]()
+    assert status["state"] == "error"
+    assert status["step"] == "failed"
+    assert status["error"] == "crawl down"
+
+
+def test_a_failed_update_inside_the_block_keeps_the_error():
+    helpers = _helpers()
+
+    with helpers["track_upload"]():
+        with pytest.raises(RuntimeError):
+            helpers["run_upload_update"](lambda **_: "failed")
+        inside = helpers["get_ingestion_status"]()
+    after = helpers["get_ingestion_status"]()
+
+    assert inside["state"] == "error"
+    assert after["state"] == "error"
+    assert after["step"] == "failed"
+    assert "documents not added" in after["error"]
+
+
+def test_the_upload_keeps_one_run_identity_from_collection_to_update():
+    times = [
+        datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc),
+        datetime(2026, 11, 9, 3, 5, tzinfo=timezone.utc),
+    ]
+    helpers = _helpers(now=_fixed_now(times))
+
+    with helpers["track_upload"]():
+        at_collection = helpers["get_ingestion_status"]()
+        helpers["run_upload_update"](lambda **_: "updated")
+        after_update = helpers["get_ingestion_status"]()
+    done = helpers["get_ingestion_status"]()
+
+    assert at_collection["run_id"] is not None
+    assert at_collection["started_at"] == "2026-11-09T03:00:00+00:00"
+    assert at_collection["finished_at"] is None
+    assert after_update["run_id"] == at_collection["run_id"]
+    assert after_update["finished_at"] is None
+    assert done["run_id"] == at_collection["run_id"]
+    assert done["finished_at"] == "2026-11-09T03:05:00+00:00"
diff --git c/tests/unit/test_service_data_manager_source_status.py w/tests/unit/test_service_data_manager_source_status.py
index 0b67e677..6790a5c3 100644
--- c/tests/unit/test_service_data_manager_source_status.py
+++ w/tests/unit/test_service_data_manager_source_status.py
@@ -8,6 +8,7 @@ writer leaves in ``ingestion_status.json``.
 """
 
 import json
+import time
 
 import pytest
 
@@ -85,9 +86,12 @@ def started(monkeypatch, tmp_path):
         return made["scheduler"]
 
     class _FakeUploader:
-        def __init__(self, app, *, post_update_hook=None, status_file=None):
+        def __init__(
+            self, app, *, post_update_hook=None, status_file=None, track_upload=None
+        ):
             made["app"] = app
             made["status_file"] = status_file
+            made["track_upload"] = track_upload
 
         def run(self, **kwargs):
             pass
@@ -182,3 +186,25 @@ def test_schedules_route_reads_the_state_from_the_file(started):
     assert jobs["git"]["last_run"] == "t0"
     assert jobs["jira"]["state"] == "idle"
     assert jobs["jira"]["last_run"] is None
+
+
+def _wait_for_the_initial_ingest(client):
+    deadline = time.monotonic() + 5
+    while client.get("/api/ingestion/status").get_json()["state"] != "completed":
+        assert time.monotonic() < deadline, "the initial ingest did not finish"
+        time.sleep(0.01)
+
+
+def test_the_uploader_gets_the_data_managers_upload_tracker(started):
+    """The tracker main() hands to the uploader publishes on the status endpoint (#648)."""
+    made, _ = started
+    client = made["app"].test_client()
+    _wait_for_the_initial_ingest(client)
+
+    with made["track_upload"]():
+        during = client.get("/api/ingestion/status").get_json()
+    after = client.get("/api/ingestion/status").get_json()
+
+    assert during["state"] == "running"
+    assert during["step"] == "upload"
+    assert after["state"] == "completed"
diff --git c/tests/unit/test_uploader_upload_tracking.py w/tests/unit/test_uploader_upload_tracking.py
new file mode 100644
index 00000000..6e8357ab
--- /dev/null
+++ w/tests/unit/test_uploader_upload_tracking.py
@@ -0,0 +1,249 @@
+"""Each manual ingest handler runs inside the data manager's upload tracker (issue #648).
+
+``FlaskAppWrapper`` enters ``track_upload`` before a handler's collection (for a git
+removal, before its deletes) and leaves it after ``_finish_manual_ingest`` returned,
+so the ingestion status reports the whole handler as one running upload. A wrapper
+without a tracker runs the handlers as before.
+"""
+
+import io
+import json
+from contextlib import contextmanager
+from pathlib import Path
+from unittest.mock import patch
+
+import pytest
+from flask import Flask
+
+from src.interfaces.uploader_app import app as uploader_app_module
+
+SOURCE_KEYS = ("git", "jira", "local_files", "web")
+
+# (handler name, form data, 200 answer body, error code of a failed collection)
+HANDLERS = [
+    (
+        "add_git_repo",
+        {"repo_url": "https://github.com/org/repo"},
+        {"status": "ok"},
+        "ingest_failed",
+    ),
+    (
+        "remove_git_repo",
+        {"repo": "repo"},
+        {"status": "ok", "repo": "repo", "deleted": 1},
+        "delete_failed",
+    ),
+    ("add_jira_project", {"project_key": "PROJ"}, {"status": "ok"}, "ingest_failed"),
+    ("upload", None, {"status": "ok", "path": "/data/a.txt"}, "upload_failed"),
+    (
+        "upload_url",
+        {"url": "https://example.org/page"},
+        {"status": "ok", "resources_scraped": 3},
+        "upload_failed",
+    ),
+]
+HANDLER_IDS = [handler[0] for handler in HANDLERS]
+
+
+class _Stubs:
+    """Stand-in for the collectors, the persistence and the catalog.
+
+    Every store change appends "collect" to ``events``; with ``fail_with`` set, it
+    raises instead.
+    """
+
+    metadata_index = {"h1": None}
+
+    def __init__(self, events, fail_with=None):
+        self.events = events
+        self.fail_with = fail_with
+        self.jira_client = object()
+
+    def _collect(self):
+        self.events.append("collect")
+        if self.fail_with is not None:
+            raise self.fail_with
+
+    def collect_git(self, persistence, urls):
+        self._collect()
+
+    def collect_jira(self, persistence, keys):
+        self._collect()
+
+    def collect_links(self, persistence, link_urls, max_depth):
+        self._collect()
+        return 3
+
+    def ingest_uploaded_file(self, upload, persistence):
+        self._collect()
+        return Path("/data/a.txt")
+
+    def delete_resource(self, resource_hash, flush=True):
+        self._collect()
+
+    def flush_index(self):
+        pass
+
+    def refresh(self):
+        pass
+
+    def get_metadata_for_hash(self, resource_hash):
+        return {"source_type": "git", "parent": "repo"}
+
+
+def _recording_tracker(events):
+    @contextmanager
+    def tracker():
+        events.append("enter")
+        try:
+            yield
+        except Exception as exc:
+            events.append(f"exit:{exc}")
+            raise
+        events.append("exit")
+
+    return tracker
+
+
+def _client(tmp_path, events, *, tracker, hook_error=None, fail_with=None, stubs=None):
+    status_file = tmp_path / "ingestion_status.json"
+    status_file.write_text(
+        json.dumps({key: {"state": "idle", "last_run": "t0"} for key in SOURCE_KEYS})
+    )
+    stubs = stubs or _Stubs(events, fail_with)
+
+    def hook():
+        events.append("hook")
+        if hook_error is not None:
+            raise hook_error
+
+    wrapper = object.__new__(uploader_app_module.FlaskAppWrapper)
+    wrapper.status_file = status_file
+    wrapper.post_update_hook = hook
+    wrapper.global_config = {"ACCEPTED_FILES": [".txt"]}
+    wrapper.persistence = stubs
+    wrapper.catalog = stubs
+    wrapper.scraper_manager = stubs
+    wrapper.ticket_manager = stubs
+    wrapper.localfile_manager = stubs
+    if tracker is not None:
+        wrapper.track_upload = tracker
+    flask_app = Flask(__name__)
+    for name in HANDLER_IDS:
+        flask_app.add_url_rule(
+            f"/document_index/{name}", name, getattr(wrapper, name), methods=["POST"]
+        )
+    return flask_app.test_client()
+
+
+def _post(client, name, form):
+    if name == "upload":
+        return client.post(
+            "/document_index/upload",
+            data={"file": (io.BytesIO(b"hello"), "a.txt")},
+            content_type="multipart/form-data",
+        )
+    return client.post(f"/document_index/{name}", data=form)
+
+
+@pytest.mark.parametrize("name, form, ok_body, error_code", HANDLERS, ids=HANDLER_IDS)
+def test_handler_collects_and_updates_inside_the_tracker(
+    tmp_path, name, form, ok_body, error_code
+):
+    events = []
+    client = _client(tmp_path, events, tracker=_recording_tracker(events))
+
+    resp = _post(client, name, form)
+
+    assert resp.status_code == 200
+    assert resp.get_json() == ok_body
+    assert events == ["enter", "collect", "hook", "exit"]
+
+
+@pytest.mark.parametrize("name, form, ok_body, error_code", HANDLERS, ids=HANDLER_IDS)
+def test_a_failed_collection_leaves_the_tracker_with_the_error(
+    tmp_path, name, form, ok_body, error_code
+):
+    events = []
+    client = _client(
+        tmp_path,
+        events,
+        tracker=_recording_tracker(events),
+        fail_with=RuntimeError("crawl down"),
+    )
+
+    resp = _post(client, name, form)
+
+    assert resp.status_code == 500
+    assert resp.get_json() == {"error": error_code, "detail": "crawl down"}
+    assert events == ["enter", "collect", "exit:crawl down"]
+
+
+@pytest.mark.parametrize("name, form, ok_body, error_code", HANDLERS, ids=HANDLER_IDS)
+def test_a_failed_update_leaves_the_tracker_normally(
+    tmp_path, name, form, ok_body, error_code
+):
+    events = []
+    client = _client(
+        tmp_path,
+        events,
+        tracker=_recording_tracker(events),
+        hook_error=RuntimeError("sync down"),
+    )
+
+    resp = _post(client, name, form)
+
+    assert resp.status_code == 500
+    assert resp.get_json() == {"error": "update_failed", "detail": "sync down"}
+    assert events == ["enter", "collect", "hook", "exit"]
+
+
+@pytest.mark.parametrize("name, form, ok_body, error_code", HANDLERS, ids=HANDLER_IDS)
+def test_handler_without_a_tracker_runs_as_before(
+    tmp_path, name, form, ok_body, error_code
+):
+    events = []
+    client = _client(tmp_path, events, tracker=None)
+
+    resp = _post(client, name, form)
+
+    assert resp.status_code == 200
+    assert resp.get_json() == ok_body
+    assert events == ["collect", "hook"]
+
+
+def test_remove_git_repo_not_found_does_not_enter_the_tracker(tmp_path):
+    events = []
+    stubs = _Stubs(events)
+    stubs.metadata_index = {}
+    client = _client(tmp_path, events, tracker=_recording_tracker(events), stubs=stubs)
+
+    resp = client.post("/document_index/remove_git_repo", data={"repo": "repo"})
+
+    assert resp.status_code == 404
+    assert events == []
+
+
+def test_the_constructed_wrapper_keeps_the_tracker(tmp_path):
+    config = {
+        "global": {"DATA_PATH": str(tmp_path)},
+        "services": {"postgres": {}, "data_manager": {"auth": {"enabled": False}}},
+        "data_manager": {},
+    }
+    tracker = object()
+    m = uploader_app_module
+    with (
+        patch.object(m, "get_full_config", return_value=config),
+        patch.object(m, "read_secret", return_value=""),
+        patch.object(m, "build_persistence"),
+        patch.object(m, "PostgresCatalogService"),
+        patch.object(m, "ScraperManager"),
+        patch.object(m, "TicketManager"),
+        patch.object(m, "LocalFileManager"),
+    ):
+        wrapper = m.FlaskAppWrapper(
+            Flask(__name__), post_update_hook=None, track_upload=tracker
+        )
+
+    assert wrapper.track_upload is tracker
+    assert uploader_app_module.FlaskAppWrapper.track_upload is None
````

### Appendix B (for task 1.1): implementation — `git apply`

````diff
diff --git c/src/bin/service_data_manager.py w/src/bin/service_data_manager.py
index f9c78e51..52ea721e 100644
--- c/src/bin/service_data_manager.py
+++ w/src/bin/service_data_manager.py
@@ -217,7 +217,10 @@ def main() -> None:
     ingestion_thread.start()
 
     uploader = FlaskAppWrapper(
-        app, post_update_hook=trigger_update, status_file=status_file
+        app,
+        post_update_hook=trigger_update,
+        status_file=status_file,
+        track_upload=_ing["track_upload"],
     )
 
     def get_ingestion_status():
diff --git c/src/interfaces/uploader_app/app.py w/src/interfaces/uploader_app/app.py
index 0421faad..1391ae76 100644
--- c/src/interfaces/uploader_app/app.py
+++ w/src/interfaces/uploader_app/app.py
@@ -4,9 +4,10 @@ import os
 import re
 import secrets
 import time
+from contextlib import nullcontext
 from functools import wraps
 from pathlib import Path
-from typing import Callable, Dict, List, Optional, Tuple
+from typing import Callable, ContextManager, Dict, List, Optional, Tuple
 
 from flask import (
     Flask,
@@ -43,12 +44,18 @@ logger = get_logger(__name__)
 class FlaskAppWrapper:
     """Uploader UI + API wrapper for the data manager service."""
 
+    # The data manager's upload tracker (``track_upload`` of
+    # ``build_ingestion_helpers``). A class default, so a wrapper built with
+    # ``object.__new__`` in tests runs the handlers without one.
+    track_upload: Optional[Callable[[], ContextManager[None]]] = None
+
     def __init__(
         self,
         app: Flask,
         *,
         post_update_hook: Optional[Callable[[], None]] = None,
         status_file: Optional[Path] = None,
+        track_upload: Optional[Callable[[], ContextManager[None]]] = None,
     ) -> None:
         self.app = app
         self.config = get_full_config()
@@ -108,6 +115,7 @@ class FlaskAppWrapper:
             dm_config=self.config.get("data_manager")
         )
         self.post_update_hook = post_update_hook
+        self.track_upload = track_upload
 
         CORS(self.app)
 
@@ -262,8 +270,9 @@ class FlaskAppWrapper:
             return jsonify({"error": "missing_repo_url"}), 400
 
         try:
-            self.scraper_manager.collect_git(self.persistence, [repo_url.strip()])
-            return self._finish_manual_ingest("git", {"status": "ok"})
+            with self._tracked_upload():
+                self.scraper_manager.collect_git(self.persistence, [repo_url.strip()])
+                return self._finish_manual_ingest("git", {"status": "ok"})
         except Exception as exc:
             logger.error("Failed to add git repo %s: %s", repo_url, exc)
             return jsonify({"error": "ingest_failed", "detail": str(exc)}), 500
@@ -297,12 +306,14 @@ class FlaskAppWrapper:
                     404,
                 )
 
-            for resource_hash in to_remove:
-                self.persistence.delete_resource(resource_hash, flush=False)
-            self.persistence.flush_index()
-            return self._finish_manual_ingest(
-                "git", {"status": "ok", "repo": repo_name, "deleted": len(to_remove)}
-            )
+            with self._tracked_upload():
+                for resource_hash in to_remove:
+                    self.persistence.delete_resource(resource_hash, flush=False)
+                self.persistence.flush_index()
+                return self._finish_manual_ingest(
+                    "git",
+                    {"status": "ok", "repo": repo_name, "deleted": len(to_remove)},
+                )
         except Exception as exc:
             logger.error("Failed to remove git repo %s: %s", repo_name, exc)
             return jsonify({"error": "delete_failed", "detail": str(exc)}), 500
@@ -316,9 +327,12 @@ class FlaskAppWrapper:
             return jsonify({"error": "jira_not_configured"}), 400
 
         try:
-            self.ticket_manager.collect_jira(self.persistence, [project_key.strip()])
-            self.persistence.flush_index()
-            return self._finish_manual_ingest("jira", {"status": "ok"})
+            with self._tracked_upload():
+                self.ticket_manager.collect_jira(
+                    self.persistence, [project_key.strip()]
+                )
+                self.persistence.flush_index()
+                return self._finish_manual_ingest("jira", {"status": "ok"})
         except Exception as exc:
             logger.error("Failed to add JIRA project %s: %s", project_key, exc)
             return jsonify({"error": "ingest_failed", "detail": str(exc)}), 500
@@ -339,13 +353,14 @@ class FlaskAppWrapper:
             return jsonify({"error": "unsupported_extension", "allowed": accepted}), 400
 
         try:
-            stored_path = self.localfile_manager.ingest_uploaded_file(
-                upload, self.persistence
-            )
-            self.persistence.flush_index()
-            return self._finish_manual_ingest(
-                "local_files", {"status": "ok", "path": str(stored_path)}
-            )
+            with self._tracked_upload():
+                stored_path = self.localfile_manager.ingest_uploaded_file(
+                    upload, self.persistence
+                )
+                self.persistence.flush_index()
+                return self._finish_manual_ingest(
+                    "local_files", {"status": "ok", "path": str(stored_path)}
+                )
         except Exception as exc:
             logger.error("Failed to ingest uploaded file %s: %s", filename, exc)
             return jsonify({"error": "upload_failed", "detail": str(exc)}), 500
@@ -380,23 +395,18 @@ class FlaskAppWrapper:
         if url:
             logger.info("Uploading the following URL: %s", url)
             try:
-                scraped_count = self.scraper_manager.collect_links(
-                    self.persistence, link_urls=[url], max_depth=depth
-                )
-                self.persistence.flush_index()
-                added_to_urls = True
+                with self._tracked_upload():
+                    scraped_count = self.scraper_manager.collect_links(
+                        self.persistence, link_urls=[url], max_depth=depth
+                    )
+                    self.persistence.flush_index()
+                    logger.info("URL uploaded successfully")
+                    return self._finish_manual_ingest(
+                        "web", {"status": "ok", "resources_scraped": scraped_count}
+                    )
             except Exception as exc:
                 logger.exception("Failed to upload URL: %s", exc)
-                added_to_urls = False
-                upload_error = str(exc)
-
-            if added_to_urls:
-                logger.info("URL uploaded successfully")
-                return self._finish_manual_ingest(
-                    "web", {"status": "ok", "resources_scraped": scraped_count}
-                )
-            else:
-                return jsonify({"error": "upload_failed", "detail": upload_error}), 500
+                return jsonify({"error": "upload_failed", "detail": str(exc)}), 500
         else:
             return jsonify({"error": "missing_url"}), 400
 
@@ -504,6 +514,12 @@ class FlaskAppWrapper:
         except Exception as exc:
             logger.warning("Post-update hook failed: %s", exc)
 
+    def _tracked_upload(self) -> ContextManager[None]:
+        """The data manager's upload tracker, or a no-op without one."""
+        if self.track_upload is None:
+            return nullcontext()
+        return self.track_upload()
+
     def _finish_manual_ingest(self, source: str, ok_body: Dict[str, object]):
         """Run the vector update of a manual ingest, then record its outcome.
 
diff --git c/src/utils/ingestion_status.py w/src/utils/ingestion_status.py
index 6f3cfae4..d2c64347 100644
--- c/src/utils/ingestion_status.py
+++ w/src/utils/ingestion_status.py
@@ -10,13 +10,19 @@ The status dict also carries a run identity: ``run_id``, ``started_at``, and
 a non-``running`` state, and ends at the next terminal publish (``completed``
 or ``error``). Runs that queue behind a running run share its ``run_id`` and
 ``started_at``; see ``_set_running`` and ``_finish``.
+
+An upload through the uploader is tracked from the start of its collection by
+``track_upload``: the collection takes no ingestion lock, and the vectorstore
+update inside the block is counted as a run of its own, so ``completed`` is
+never published between the two.
 """
 
 import logging
 import threading
 import uuid
+from contextlib import contextmanager
 from datetime import datetime, timezone
-from typing import Any, Callable, Dict, Optional
+from typing import Any, Callable, Dict, Iterator, Optional
 
 logger = logging.getLogger(__name__)
 
@@ -37,6 +43,8 @@ def build_ingestion_helpers(
       - ``run_tracked(step, fn)``
       - ``run_source_refresh(name, func, update_vectorstore, set_source_status)``
       - ``run_upload_update(update_vectorstore)``
+      - ``track_upload()`` — a context manager around an upload's collection
+        and vectorstore update
       - ``ingestion_lock`` — the caller's ingestion mutual-exclusion lock
 
     ``now`` is a keyword-only seam, a callable returning an aware UTC
@@ -104,11 +112,26 @@ def build_ingestion_helpers(
         with _status_lock:
             _set_running(step)
 
-    def _finish(state: str, step: str, error: Optional[str] = None) -> None:
-        """Count a run out. Call it while the run still holds ingestion_lock."""
+    def _finish(
+        state: str,
+        step: str,
+        error: Optional[str] = None,
+        *,
+        keep_error: bool = False,
+    ) -> None:
+        """Count a run out.
+
+        A run that holds ingestion_lock calls it before the release; the
+        upload tracker calls it without the lock. With ``keep_error``, an
+        ``error`` already published by another run is kept: the tracker never
+        re-publishes ``running``, so it must not turn the ``error`` of the
+        update inside its block into ``completed``.
+        """
         nonlocal _inflight
         with _status_lock:
             _inflight -= 1
+            if keep_error and _status["state"] == "error":
+                return
             if state == "error" or _inflight == 0:
                 _status.update(
                     {
@@ -144,6 +167,26 @@ def build_ingestion_helpers(
     def run_upload_update(update_vectorstore: Callable[..., Any]) -> None:
         run_tracked("upload", lambda: _update_or_raise(update_vectorstore))
 
+    @contextmanager
+    def track_upload() -> Iterator[None]:
+        """Report an upload as running from the start of its collection.
+
+        The block takes no ingestion_lock, so a collection never waits behind
+        an ingest. It counts the upload in before the block and out after it;
+        the vectorstore update inside the block joins and finishes as a run of
+        its own, so the counter keeps the status at ``running`` between the
+        two. A block that raises publishes ``error`` with the message. A block
+        that ends normally publishes ``completed`` when no other run is in
+        flight, unless its update already published ``error``.
+        """
+        _join("upload")
+        try:
+            yield
+        except Exception as exc:
+            _finish("error", "failed", str(exc))
+            raise
+        _finish("completed", "done", keep_error=True)
+
     def run_source_refresh(
         name: str,
         func: Callable[[], None],
@@ -198,5 +241,6 @@ def build_ingestion_helpers(
         "run_tracked": run_tracked,
         "run_source_refresh": run_source_refresh,
         "run_upload_update": run_upload_update,
+        "track_upload": track_upload,
         "ingestion_lock": ingestion_lock,
     }
````

### Appendix C (for task 2.1): docs — `git apply`

````diff
diff --git c/docs/docs/api_reference.md w/docs/docs/api_reference.md
index f1304d30..46ab4b19 100644
--- c/docs/docs/api_reference.md
+++ w/docs/docs/api_reference.md
@@ -737,14 +737,21 @@ ingest ends, `progress` keeps its last value.
 
 **Scheduled refreshes and upload runs** also move through this endpoint. While a
 scheduled source refresh is running, `state` is `running` and `step` is
-`scheduled:<source>` (for example `scheduled:git`). While a file upload is being
-ingested, or when the chat app's Process Documents action (`POST /api/upload/embed`) is
-running, `step` is `upload`. In both cases `progress` is `null` for the duration of
-the run. When the run ends, `state` becomes `completed` and `step` becomes `done`; on
+`scheduled:<source>` (for example `scheduled:git`). A manual ingest through the
+uploader (a file upload, a URL, a git repository, a JIRA project, or a git repository
+removal) is reported with `step` `upload` from the start of its collection, before its
+vectorstore update, until that update ends; the endpoint does not show `completed`
+between the two. The collection takes no ingestion lock, so it never waits behind a
+running ingest; only its vectorstore update does. A collection that fails ends the run
+in `error` with the collection's message, and a failed vectorstore update leaves
+`error` in place. The chat app's Process Documents action (`POST /api/upload/embed`)
+is reported with `step` `upload` while its update runs. In all cases `progress` is
+`null` for the duration of the run. When the run ends, `state` becomes `completed` and `step` becomes `done`; on
 failure, `state` becomes `error` and `step` becomes `failed`. A run whose vectorstore
 sync could not add its documents also ends in `error`. If another run is queued for the
 ingestion lock, the endpoint keeps reporting `running` instead of `completed`, and it
-does not publish a queued run's step over a running one. A run that starts when no run
+does not publish the step of a queued run, or of an upload's collection, over a
+running one. A run that starts when no run
 is reported as `running` publishes its step at once, so `completed` never stays visible
 while that run waits for the lock. Any benchmark that
 polls this endpoint to wait for an idle system will also wait during scheduled
diff --git c/docs/docs/benchmarking.md w/docs/docs/benchmarking.md
index 39a4e8ca..ad0d8edc 100644
--- c/docs/docs/benchmarking.md
+++ w/docs/docs/benchmarking.md
@@ -227,19 +227,22 @@ plain "give up after N seconds":
   a phase before the embedding loop starts), a wedged ingest is byte-for-byte
   the same as a working one, and only the ceiling can catch it.
 
-Scheduled source refreshes and upload-triggered vectorstore updates also report
-through this endpoint (`step=scheduled:<source>` or `step=upload`), so a
-benchmark that starts while one runs waits for it, and the refresh's polls keep
+Scheduled source refreshes and uploads (from the start of their collection to the
+end of their vectorstore update) also report through this endpoint
+(`step=scheduled:<source>` or `step=upload`), so a benchmark that starts while one
+runs waits for it, and the refresh's polls keep
 the stall budget alive. The endpoint reports `completed` only when no such run is
 queued. A refresh is not the corpus build, so its time is not counted in
 `ingest_wall_seconds`. This includes a refresh that runs after the ingest and
 before `completed`: the timing stops at the first poll that shows the refresh.
 
 What this wait does **not** cover: a corpus change that starts *after* the
-benchmark's wait returned. That includes the collection phase of an upload,
-which runs before its vectorstore update begins to report. The harness detects
-those changes after the fact, by fingerprinting the corpus on both sides of each
-arm and recording `corpus_unchanged_at_endpoints` in the results.
+benchmark's wait returned. An upload is reported from the start of its collection,
+not only during its vectorstore update, so a wait that saw `completed` has no upload
+collecting behind it; an upload that starts after the wait returned is still
+invisible to it. The harness detects those changes after the fact, by fingerprinting
+the corpus on both sides of each arm and recording `corpus_unchanged_at_endpoints`
+in the results.
 
 A long-but-healthy ingest hits none of them. CPU-only ingest of the full FASRC
 corpus takes ~64 min; with `processing.categorization.enabled: true` it runs one
````

## 3. Review

- [x] 3.1 Round 1 (2026-11-09, Codex P2 thread `PRRT_kwDORx3_1861O1bP`): when the
      lock-owning run ends and only tracked uploads remain, `_finish` publishes
      `step="upload"`, `progress=None` (design D8). Test first:
      `test_the_ingest_ending_during_a_collection_shows_the_upload_step`, with the guards
      `test_the_collection_ending_during_a_refresh_keeps_the_refresh_step` and
      `test_the_ingest_ending_after_a_failed_collection_keeps_the_error`.
- [x] 3.2 Round 1 (2026-11-09, Codex P2 thread `PRRT_kwDORx3_1861O1bL`):
      `set_ingestion_status("running")` from a non-running state assigns a new run
      identity (design D8). Test first:
      `test_a_step_after_a_failed_collection_starts_a_new_run`.
- [x] 3.3 Round 2 (2026-11-09, sync adversarial pass on `7146c180`, high): `_join` publishes
      `running` whenever the shown state is not `running`, so a collection that starts after
      a sibling's published `error` is shown as a new run (design D8, item 3). Test first:
      `test_a_collection_that_starts_after_a_siblings_failure_is_running`.
- [x] 3.4 Async Codex pass on `b10a28a0` (P2 thread `PRRT_kwDORx3_1861RTae`): a `completed`
      finish never replaces a shown `error`, whatever the count in flight; the `keep_error`
      flag is removed (design D8, item 4). Test first:
      `test_the_ingest_ending_after_a_failed_collection_alone_keeps_the_error` and
      `test_a_refresh_ending_after_a_failed_collection_alone_keeps_the_error`, with the guard
      `test_the_ingest_failing_after_a_failed_collection_publishes_its_own_error`.
