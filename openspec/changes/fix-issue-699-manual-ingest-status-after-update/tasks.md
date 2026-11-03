## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-699-manual-ingest-status-after-update` exists, cut from
      `origin/dev` at `e83160e6`. You are on it. Do not re-branch, do not rebase, and do not
      merge `dev`.
- [x] 0.2 `openspec validate fix-issue-699-manual-ingest-status-after-update --strict`
      passed on the host. Do not run the `openspec` CLI in the container, and do not add a
      task that does.
- [x] 0.3 Every design decision is made in `design.md` (D1–D7). Follow the design; where the
      issue body and the design differ, the design wins.
- [x] 0.4 Loop 1 ran every check of tasks 1.1 and 2.1 on the host, red and green, with the
      exact text in the appendices: Appendix A red on the unmodified code is
      `10 failed, 2 passed`; with Appendix B applied, A plus the contract test plus
      `tests/unit/test_uploader_embed_endpoint.py` pass, and the patch coverage of
      `src/interfaces/uploader_app/app.py` is 100% (26 of 26 added lines). Copy the
      appendices; do not rewrite them.

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed.
- Each of tasks 1.1 and 2.1 ends with exactly one commit (tick its checkbox in this file in
  the same commit).
- Run the gate and every `git commit` in the FOREGROUND. Never put a command in the
  background and end the turn to wait for it.
- `tests/unit/test_uploader_manual_ingest_status.py` is a NEW file. Before you create it,
  `test -e tests/unit/test_uploader_manual_ingest_status.py` must fail.
- Do not delete an existing test. Edit an existing test only where task 1.1 says so (the
  regex and docstring of `tests/unit/test_api_reference_schedule_state_contract.py`). After
  each commit, `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- Run `black` and `isort` on every changed `.py` file **before** `git add` (on the staged
  run they changed nothing). After each commit, `git status --porcelain` must be empty.
- Never bypass the commit hook. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Do not edit `src/interfaces/chat_app/**`, `src/bin/service_data_manager.py`,
  `src/utils/ingestion_status.py`, any UI file, or any control-plane file. Under `docs/`,
  edit only `docs/docs/api_reference.md` (task 2.1). The PR body goes in `/tmp`, never in the
  repo. Do not add an entry to `docs/questions.md`.

## 1. Write the source status after the update

- [x] 1.1 Test first, then implement design D1, D2 and D5, one commit
      (`fix: write a manual ingest's source status after its vector update`).
      (a) Create `tests/unit/test_uploader_manual_ingest_status.py` with the content of
      Appendix A, unchanged.
      (b) Red: run
      `python -m pytest -q tests/unit/test_uploader_manual_ingest_status.py` and see exactly
      `10 failed, 2 passed`. The 10 failures are the five
      `test_failed_update_sets_error_keeps_last_run_and_answers_500[...]` cases
      (`assert 200 == 500`) and the five
      `test_successful_update_writes_idle_only_after_the_update_ran[...]` cases (the hook
      saw a new `last_run`). The 2 passes are
      `test_manual_ingest_without_a_hook_writes_idle_and_answers_ok` and
      `test_failed_collection_skips_the_update_and_keeps_the_status` (they pin behavior that
      must not change).
      (c) Apply every hunk of Appendix B: the five handler edits and the new
      `_finish_manual_ingest` method in `src/interfaces/uploader_app/app.py`, and the regex
      plus docstring edit in `tests/unit/test_api_reference_schedule_state_contract.py`. To
      apply it as a patch, copy the block into `/tmp/699-impl.diff` and run
      `git apply /tmp/699-impl.diff`; if it does not apply, make the same edits by hand.
      (d) Green: run
      `python -m pytest -q tests/unit/test_uploader_manual_ingest_status.py tests/unit/test_api_reference_schedule_state_contract.py tests/unit/test_uploader_embed_endpoint.py`
      and see `0 failed`. Then
      `grep -c '_finish_manual_ingest(' src/interfaces/uploader_app/app.py` must print `6`
      (five call sites plus the `def`), and
      `grep -n 'state="idle"' src/interfaces/uploader_app/app.py` must print exactly one line
      (inside `_finish_manual_ingest`).
      (e) Tick this box, `git add` the three files and this file, and commit through the hook.

## 2. Docs and publish

- [x] 2.1 Apply Appendix C to `docs/docs/api_reference.md` (design D6): one new paragraph and
      two edited table rows in the `GET /api/schedules` section; nothing else changes. Then
      `python -m pytest -q tests/unit/test_api_reference_schedule_state_contract.py` must
      show `2 passed`, and
      `awk '/### .GET \/api\/schedules./,/^---/' docs/docs/api_reference.md | grep -c 'last writer'`
      must print `1`. Tick this box and commit through the hook
      (`docs: a manual ingest writes its schedule state after the update`).
- [ ] 2.2 Do the steps of this task in this order: check, tick this box, commit the tick
      through the hook, push, then open the PR. The tick must ride the push that opens the
      PR; after the PR opens, `git status --porcelain` must be empty and
      `git rev-parse HEAD` must equal
      `git rev-parse origin/fix/issue-699-manual-ingest-status-after-update`.
      Run `bash scripts/gate.sh` in the foreground on the tip and confirm it exits 0. Confirm
      `git diff origin/dev --stat` lists only `src/interfaces/uploader_app/app.py`,
      `tests/unit/test_uploader_manual_ingest_status.py`,
      `tests/unit/test_api_reference_schedule_state_contract.py`,
      `docs/docs/api_reference.md`, and files under
      `openspec/changes/fix-issue-699-manual-ingest-status-after-update/`. Push with
      `git push -u origin fix/issue-699-manual-ingest-status-after-update`. Write the PR body
      to `/tmp/pr-699.md` with `Closes #699` in the body (never the title), a per-task
      summary, the line "Collection failures are unchanged; #648 (status at upload start)
      and #700 (status-file lock) rebase on `_finish_manual_ingest`", a line that names the
      one edited existing test (the regex of
      `tests/unit/test_api_reference_schedule_state_contract.py`, design D5), and a
      "Post-merge operator check" line: after a redeploy, stop the vector update (for example,
      make the embedding endpoint fail), add a git repository in the uploader, and confirm the
      answer is HTTP 500 `update_failed` and that
      `curl -s http://localhost:7871/api/schedules` shows `"state": "error"` for the `git`
      job with its old `last_run`. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-699-manual-ingest-status-after-update --title "uploader: write a manual ingest's status after its vector update" --body-file /tmp/pr-699.md`.
      The only file this task changes is this checkbox, and that change is committed and
      pushed before `gh pr create`. The task is complete when
      `gh pr view --repo fasrc/archi fix/issue-699-manual-ingest-status-after-update` prints a
      PR whose base repository is `fasrc/archi` (not a fork).

### Appendix A (for task 1.1): `tests/unit/test_uploader_manual_ingest_status.py`

```python
"""A manual ingest records its source status after its vector update (issue #699).

Each uploader handler that collects or removes documents runs the vectorstore update
through ``post_update_hook``. The per-source status in ``ingestion_status.json`` must
be written only after that update returns: ``idle`` with a new ``last_run`` when it
succeeds, and ``error`` with the old ``last_run`` and a 500 answer when it fails.
"""

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask

from src.interfaces.uploader_app import app as uploader_app_module

OLD_RUN = "2026-01-01T00:00:00+00:00"
SOURCE_KEYS = ("git", "jira", "local_files", "web")

# (handler name, form data, source key, 200 answer body)
HANDLERS = [
    (
        "add_git_repo",
        {"repo_url": "https://github.com/org/repo"},
        "git",
        {"status": "ok"},
    ),
    (
        "remove_git_repo",
        {"repo": "repo"},
        "git",
        {"status": "ok", "repo": "repo", "deleted": 1},
    ),
    ("add_jira_project", {"project_key": "PROJ"}, "jira", {"status": "ok"}),
    ("upload", None, "local_files", {"status": "ok", "path": "/data/a.txt"}),
    (
        "upload_url",
        {"url": "https://example.org/page"},
        "web",
        {"status": "ok", "resources_scraped": 3},
    ),
]
HANDLER_IDS = [handler[0] for handler in HANDLERS]


class _Collectors:
    """Stand-in for the scraper, ticket and local-file managers."""

    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.jira_client = object()

    def _collect(self):
        if self.fail_with is not None:
            raise self.fail_with

    def collect_git(self, persistence, urls):
        self._collect()

    def collect_jira(self, persistence, keys):
        self._collect()

    def collect_links(self, persistence, link_urls, max_depth):
        self._collect()
        return 3

    def ingest_uploaded_file(self, upload, persistence):
        self._collect()
        return Path("/data/a.txt")


class _Persistence:
    def flush_index(self):
        pass

    def delete_resource(self, resource_hash, flush=True):
        pass


class _Catalog:
    metadata_index = {"h1": None}

    def refresh(self):
        pass

    def get_metadata_for_hash(self, resource_hash):
        return {"source_type": "git", "parent": "repo"}


def _read_status(tmp_path):
    return json.loads((tmp_path / "ingestion_status.json").read_text())


def _recording_hook(tmp_path, seen, error=None):
    def hook():
        seen.append(_read_status(tmp_path))
        if error is not None:
            raise error

    return hook


def _client(tmp_path, hook, collectors=None):
    status_file = tmp_path / "ingestion_status.json"
    status_file.write_text(
        json.dumps({key: {"state": "idle", "last_run": OLD_RUN} for key in SOURCE_KEYS})
    )
    collectors = collectors or _Collectors()
    wrapper = object.__new__(uploader_app_module.FlaskAppWrapper)
    wrapper.status_file = status_file
    wrapper.post_update_hook = hook
    wrapper.global_config = {"ACCEPTED_FILES": [".txt"]}
    wrapper.persistence = _Persistence()
    wrapper.catalog = _Catalog()
    wrapper.scraper_manager = collectors
    wrapper.ticket_manager = collectors
    wrapper.localfile_manager = collectors
    flask_app = Flask(__name__)
    for name in HANDLER_IDS:
        flask_app.add_url_rule(
            f"/document_index/{name}", name, getattr(wrapper, name), methods=["POST"]
        )
    return flask_app.test_client()


def _post(client, name, form):
    if name == "upload":
        return client.post(
            "/document_index/upload",
            data={"file": (io.BytesIO(b"hello"), "a.txt")},
            content_type="multipart/form-data",
        )
    return client.post(f"/document_index/{name}", data=form)


@pytest.mark.parametrize("name, form, key, ok_body", HANDLERS, ids=HANDLER_IDS)
def test_failed_update_sets_error_keeps_last_run_and_answers_500(
    tmp_path, name, form, key, ok_body
):
    seen = []
    hook = _recording_hook(tmp_path, seen, RuntimeError("sync down"))

    resp = _post(_client(tmp_path, hook), name, form)

    assert resp.status_code == 500
    assert resp.get_json() == {"error": "update_failed", "detail": "sync down"}
    assert len(seen) == 1
    assert _read_status(tmp_path)[key] == {"state": "error", "last_run": OLD_RUN}


@pytest.mark.parametrize("name, form, key, ok_body", HANDLERS, ids=HANDLER_IDS)
def test_successful_update_writes_idle_only_after_the_update_ran(
    tmp_path, name, form, key, ok_body
):
    seen = []
    before = datetime.now(timezone.utc)

    resp = _post(_client(tmp_path, _recording_hook(tmp_path, seen)), name, form)

    assert resp.status_code == 200
    assert resp.get_json() == ok_body
    assert len(seen) == 1
    assert seen[0][key] == {"state": "idle", "last_run": OLD_RUN}
    entry = _read_status(tmp_path)[key]
    assert entry["state"] == "idle"
    assert datetime.fromisoformat(entry["last_run"]) >= before


def test_manual_ingest_without_a_hook_writes_idle_and_answers_ok(tmp_path):
    before = datetime.now(timezone.utc)

    resp = _post(
        _client(tmp_path, None),
        "add_git_repo",
        {"repo_url": "https://github.com/org/repo"},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
    entry = _read_status(tmp_path)["git"]
    assert entry["state"] == "idle"
    assert datetime.fromisoformat(entry["last_run"]) >= before


def test_failed_collection_skips_the_update_and_keeps_the_status(tmp_path):
    seen = []
    collectors = _Collectors(fail_with=RuntimeError("clone down"))

    resp = _post(
        _client(tmp_path, _recording_hook(tmp_path, seen), collectors),
        "add_git_repo",
        {"repo_url": "https://github.com/org/repo"},
    )

    assert resp.status_code == 500
    assert resp.get_json() == {"error": "ingest_failed", "detail": "clone down"}
    assert seen == []
    assert _read_status(tmp_path)["git"] == {"state": "idle", "last_run": OLD_RUN}
```

### Appendix B (for task 1.1): the `app.py` and contract-test edits, as a patch on `e83160e6`

```diff
diff --git i/src/interfaces/uploader_app/app.py w/src/interfaces/uploader_app/app.py
index 265938a7..5c8f014d 100644
--- i/src/interfaces/uploader_app/app.py
+++ w/src/interfaces/uploader_app/app.py
@@ -262,9 +262,7 @@ class FlaskAppWrapper:
 
         try:
             self.scraper_manager.collect_git(self.persistence, [repo_url.strip()])
-            self._update_source_status("git", state="idle", last_run=self._now_iso())
-            self._notify_update()
-            return jsonify({"status": "ok"})
+            return self._finish_manual_ingest("git", {"status": "ok"})
         except Exception as exc:
             logger.error("Failed to add git repo %s: %s", repo_url, exc)
             return jsonify({"error": "ingest_failed", "detail": str(exc)}), 500
@@ -301,10 +299,8 @@ class FlaskAppWrapper:
             for resource_hash in to_remove:
                 self.persistence.delete_resource(resource_hash, flush=False)
             self.persistence.flush_index()
-            self._update_source_status("git", state="idle", last_run=self._now_iso())
-            self._notify_update()
-            return jsonify(
-                {"status": "ok", "repo": repo_name, "deleted": len(to_remove)}
+            return self._finish_manual_ingest(
+                "git", {"status": "ok", "repo": repo_name, "deleted": len(to_remove)}
             )
         except Exception as exc:
             logger.error("Failed to remove git repo %s: %s", repo_name, exc)
@@ -321,9 +317,7 @@ class FlaskAppWrapper:
         try:
             self.ticket_manager.collect_jira(self.persistence, [project_key.strip()])
             self.persistence.flush_index()
-            self._update_source_status("jira", state="idle", last_run=self._now_iso())
-            self._notify_update()
-            return jsonify({"status": "ok"})
+            return self._finish_manual_ingest("jira", {"status": "ok"})
         except Exception as exc:
             logger.error("Failed to add JIRA project %s: %s", project_key, exc)
             return jsonify({"error": "ingest_failed", "detail": str(exc)}), 500
@@ -348,11 +342,9 @@ class FlaskAppWrapper:
                 upload, self.persistence
             )
             self.persistence.flush_index()
-            self._update_source_status(
-                "local_files", state="idle", last_run=self._now_iso()
+            return self._finish_manual_ingest(
+                "local_files", {"status": "ok", "path": str(stored_path)}
             )
-            self._notify_update()
-            return jsonify({"status": "ok", "path": str(stored_path)})
         except Exception as exc:
             logger.error("Failed to ingest uploaded file %s: %s", filename, exc)
             return jsonify({"error": "upload_failed", "detail": str(exc)}), 500
@@ -391,9 +383,6 @@ class FlaskAppWrapper:
                     self.persistence, link_urls=[url], max_depth=depth
                 )
                 self.persistence.flush_index()
-                self._update_source_status(
-                    "web", state="idle", last_run=self._now_iso()
-                )
                 added_to_urls = True
             except Exception as exc:
                 logger.exception("Failed to upload URL: %s", exc)
@@ -402,8 +391,9 @@ class FlaskAppWrapper:
 
             if added_to_urls:
                 logger.info("URL uploaded successfully")
-                self._notify_update()
-                return jsonify({"status": "ok", "resources_scraped": scraped_count})
+                return self._finish_manual_ingest(
+                    "web", {"status": "ok", "resources_scraped": scraped_count}
+                )
             else:
                 return jsonify({"error": "upload_failed", "detail": upload_error}), 500
         else:
@@ -513,6 +503,23 @@ class FlaskAppWrapper:
         except Exception as exc:
             logger.warning("Post-update hook failed: %s", exc)
 
+    def _finish_manual_ingest(self, source: str, ok_body: Dict[str, object]):
+        """Run the vector update of a manual ingest, then record its outcome.
+
+        The source status is written only after the update returns. Unlike
+        _notify_update, a failed update is not swallowed: the source is set to
+        error, its last_run is kept, and the caller gets a 500 answer.
+        """
+        if self.post_update_hook:
+            try:
+                self.post_update_hook()
+            except Exception as exc:
+                logger.error("Update after a manual %s ingest failed: %s", source, exc)
+                self._update_source_status(source, state="error")
+                return jsonify({"error": "update_failed", "detail": str(exc)}), 500
+        self._update_source_status(source, state="idle", last_run=self._now_iso())
+        return jsonify(ok_body)
+
     def _load_source_status(self) -> Dict[str, Dict[str, str]]:
         if not self.status_file.exists():
             return {}
diff --git i/tests/unit/test_api_reference_schedule_state_contract.py w/tests/unit/test_api_reference_schedule_state_contract.py
index 54e12b7f..f08d2f15 100644
--- i/tests/unit/test_api_reference_schedule_state_contract.py
+++ w/tests/unit/test_api_reference_schedule_state_contract.py
@@ -7,8 +7,8 @@ link schedule job reads ``links``, so a URL upload never changes the ``links`` j
 review, PR #694).
 
 This reads the backticked keys out of the docs sentence and compares them, as a set,
-against the keys the uploader writes with ``state="idle"`` that are also schedule job
-names in ``service_data_manager.py``.
+against the keys the uploader passes to ``_finish_manual_ingest`` (which writes their
+status) that are also schedule job names in ``service_data_manager.py``.
 """
 
 import re
@@ -19,9 +19,7 @@ API_REFERENCE = ROOT / "docs" / "docs" / "api_reference.md"
 UPLOADER = ROOT / "src" / "interfaces" / "uploader_app" / "app.py"
 DATA_MANAGER = ROOT / "src" / "bin" / "service_data_manager.py"
 
-_UPLOADER_IDLE_KEY = re.compile(
-    r'_update_source_status\(\s*"(\w+)",\s*state="idle"', re.MULTILINE
-)
+_UPLOADER_IDLE_KEY = re.compile(r'_finish_manual_ingest\(\s*"(\w+)"', re.MULTILINE)
 _SCHEDULE_KEY = re.compile(r'^\s*"(\w+)": lambda last_run', re.MULTILINE)
 _BACKTICKED = re.compile(r"`([^`]+)`")
 
```

### Appendix C (for task 2.1): the `docs/docs/api_reference.md` edit, as a patch on `e83160e6`

```diff
diff --git i/docs/docs/api_reference.md w/docs/docs/api_reference.md
index b338b0a0..a5b38e52 100644
--- i/docs/docs/api_reference.md
+++ w/docs/docs/api_reference.md
@@ -789,11 +789,13 @@ Get current schedule status.
 
 A URL upload writes its status under `web`, not `links`. As a result, a URL upload does not change the `state` or `last_run` of the `links` job.
 
+A manual ingest writes its status only after its vector update returns. If the update succeeds, it writes `idle` and a new `last_run`. If the update fails, it writes `error` and keeps the old `last_run`, and the uploader answers HTTP 500 with `{"error": "update_failed", "detail": "<message>"}`, not `{"status": "ok"}`. The same order applies to a git repository removal and to a URL upload (under `web`).
+
 | `state` | Meaning |
 |---|---|
-| `idle` | The last scheduled refresh or manual ingest completed. `last_run` is its completion time. This is also the value for a source that has not run; then `last_run` is `null`. |
+| `idle` | The last scheduled refresh or manual ingest completed, including its vector update. `last_run` is its completion time. This is also the value for a source that has not run; then `last_run` is `null`. |
 | `running` | A refresh is in progress. |
-| `error` | The last scheduled refresh failed. `last_run` keeps the time of the last success. The next scheduled run tries again. A later successful manual ingest of a shared key (`git`, `jira`, `local_files`) sets `idle` again. |
+| `error` | The last scheduled refresh failed, or the vector update after the last manual ingest failed. `last_run` keeps the time of the last success. The next scheduled run tries again. A later successful manual ingest of a shared key (`git`, `jira`, `local_files`) sets `idle` again. |
 
 The cause of a failure is in the data-manager log. `GET /api/ingestion/status` also shows it, but only for a short time: that endpoint keeps one status in memory, so the next ingestion run or a service restart clears the error. The `error` state on this endpoint stays until a later write for that source.
 
```
