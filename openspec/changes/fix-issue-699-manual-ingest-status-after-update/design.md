## Context

The uploader (`src/interfaces/uploader_app/app.py`, class `FlaskAppWrapper`) runs inside
the data-manager process. `post_update_hook` is `trigger_update`
(`src/bin/service_data_manager.py:114-115`), which runs `run_upload_update`
(`src/utils/ingestion_status.py:112-113`) SYNCHRONOUSLY: it takes `ingestion_lock`, runs
`update_vectorstore(force=True)`, and raises when the update raises or returns `"failed"`.
So a handler already blocks until the update ends; this change moves only the status write
and stops hiding the failure.

The per-source status file is written by `_update_source_status` (`:527-556`), which does a
read-modify-write of `ingestion_status.json` and catches and logs EVERY exception (it never
raises). `GET /api/schedules` reads `state` and `last_run` from that file for each schedule
job (`service_data_manager.py:283-285`).

The scheduled path already has the target behavior (#650): `run_source_refresh`
(`ingestion_status.py:115-140`) writes `idle` + `last_run` only after the update, and
`error` (no `last_run`) on failure.

## Goals / Non-Goals

**Goals:** for each of the five manual-ingest handlers, write the terminal status only after
the update returns; on a failed update write `error`, keep `last_run`, and answer non-2xx.

**Non-Goals:**
- The scheduled-refresh path (#650, done).
- Locking the status file (#700).
- Status at the START of an upload's collection (#648).
- A failure in COLLECTION (before the update): unchanged — the handler answers its existing
  500 (`ingest_failed`, `delete_failed`, `upload_failed`), writes no status, and does not run
  the update.
- `delete` and `delete_source` (they redirect a browser and keep `_notify_update`).
- Any chat-app or UI change.

## Decisions

### D1 — One helper method, `_finish_manual_ingest(source, ok_body)`
Add it to `FlaskAppWrapper` directly after `_notify_update`. Exact body (copied from the
staged run, see tasks.md Appendix B):

```python
    def _finish_manual_ingest(self, source: str, ok_body: Dict[str, object]):
        """Run the vector update of a manual ingest, then record its outcome.

        The source status is written only after the update returns. Unlike
        _notify_update, a failed update is not swallowed: the source is set to
        error, its last_run is kept, and the caller gets a 500 answer.
        """
        if self.post_update_hook:
            try:
                self.post_update_hook()
            except Exception as exc:
                logger.error("Update after a manual %s ingest failed: %s", source, exc)
                self._update_source_status(source, state="error")
                return jsonify({"error": "update_failed", "detail": str(exc)}), 500
        self._update_source_status(source, state="idle", last_run=self._now_iso())
        return jsonify(ok_body)
```

It returns the Flask answer in both cases, so each call site is one `return` line. A method
(not a new module) is correct here: unit tests already import this file and build the
wrapper with `object.__new__` (`tests/unit/test_uploader_embed_endpoint.py:17-24`), so
diff-cover sees it.

### D2 — Call sites
Each handler replaces its idle write + `_notify_update()` + `return jsonify(<body>)` with
`return self._finish_manual_ingest("<key>", <body>)`, with the SAME key and the SAME 200
body as today:

| Handler | Key | 200 body |
|---|---|---|
| `add_git_repo` | `git` | `{"status": "ok"}` |
| `remove_git_repo` | `git` | `{"status": "ok", "repo": repo_name, "deleted": len(to_remove)}` |
| `add_jira_project` | `jira` | `{"status": "ok"}` |
| `upload` | `local_files` | `{"status": "ok", "path": str(stored_path)}` |
| `upload_url` | `web` | `{"status": "ok", "resources_scraped": scraped_count}` |

In the first four, the call stays inside the existing `try`. The helper catches the hook's
exception itself, so the handler's own `except` still sees only collection errors.
In `upload_url`, delete the `web` idle write from inside the `try` (keep
`added_to_urls = True`), and replace `self._notify_update()` + the `return jsonify(...)` in
the `if added_to_urls:` arm with the helper call.

### D3 — Failure answer: 500 `{"error": "update_failed", "detail": str(exc)}`
`update_failed` is new and distinct from the collection errors, so a caller can tell
"collected but not searchable" from "not collected". It matches the shape of `embed`
(`embed_failed`, `:493-506`). The chat-app proxies already forward a non-200 JSON `error`
(`chat_app/app.py` near `:5986`, `:6060`, `:6120`, `:6390`).

### D4 — No extra guard around the `error` write
`_update_source_status` never raises (it logs a warning), so writing `error` cannot mask
the update failure, and a `try` around it would be dead, uncovered code. `last_run` is
kept by not passing it, as in #650's D3.

### D5 — The docs contract test follows the call sites
`tests/unit/test_api_reference_schedule_state_contract.py:22-24` finds the uploader's keys
with the regex `_update_source_status\(\s*"(\w+)",\s*state="idle"`. After D2 no call site
matches it, so `assert "web" in uploader_keys` fails. Replace the regex with
`_finish_manual_ingest\(\s*"(\w+)"` (finds `git`, `jira`, `local_files`, `web` — the same
set as today), and update the docstring sentence that names `state="idle"`. This edit and
D2 MUST be in the same commit. It is the only edit to an existing test.

### D6 — Docs (`docs/docs/api_reference.md`, `GET /api/schedules`)
Add one paragraph after the "A URL upload writes its status under `web`" paragraph, and edit
the `idle` and `error` table rows (exact text in tasks.md Appendix C). Two constraints from
the contract test: the paragraph that contains `last writer` must stay the ONLY one that
contains it, and its backticked words (minus `state`) must stay exactly `git`, `jira`,
`local_files`. So do NOT edit that paragraph, and do not write `last writer` anywhere else in
the section.

### D7 — Order with #648 and #700
Neither has a PR on 2026-11-03 (`origin/dev` `e83160e6`). Per the issue's Decision, use
`_update_source_status` as it is. #700 can make that method locked and atomic without
touching the call sites; #648 can wrap collection plus `_finish_manual_ingest` in its
begin/end pair.

## Risks / Trade-offs

- The terminal write runs after `run_tracked` released `ingestion_lock`. A scheduled refresh
  of the same key that starts in that gap can write `running`, and then this write lands.
  This is the same class of race as today and much narrower; closing it needs the status
  write under the ingestion lock (a `service_data_manager.py` change) or #700's lock. Out of
  scope.
- A handler now answers 500 where it answered 200 before, for the same failed update. That
  is the point of the issue. The documents ARE collected in this case; the 500 detail says
  the update failed, and the next successful update makes them searchable.
