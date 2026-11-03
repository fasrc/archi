## Why

A manual ingest through the uploader writes its terminal source status BEFORE its vector
update runs, and a failed update is hidden. In `src/interfaces/uploader_app/app.py` (on
`origin/dev` `e83160e6`), each manual-ingest handler calls
`self._update_source_status(<key>, state="idle", last_run=self._now_iso())` and only then
`self._notify_update()`:

- `add_git_repo` — `:265` then `:266` (key `git`);
- `remove_git_repo` — `:304` then `:305` (key `git`);
- `add_jira_project` — `:324` then `:325` (key `jira`);
- `upload` — `:351-353` then `:354` (key `local_files`);
- `upload_url` — `:394-396` then `:405` (key `web`).

`_notify_update` (`:508-514`) catches every exception from `post_update_hook` and only
logs a warning, so the handler still answers `{"status": "ok"}`. `post_update_hook` is
`trigger_update` (`src/bin/service_data_manager.py:114-115`) → `run_upload_update`
(`src/utils/ingestion_status.py:112-113`), which raises when the update fails.

Two results today:
1. A manual ingest whose vector update fails still shows `idle` with a new `last_run` in
   `GET /api/schedules`, and the request answers `ok`.
2. A scheduled refresh of the same key that holds the ingestion lock and fails writes
   `error` (#650). The manual ingest has already written `idle`, so the board shows a
   status that matches neither run.

Source: issue #699 (found in the review of PR #694 / #650). The operator decision on the
issue (2026-10-20) is to implement this on the default tier.

## What Changes

- A new `FlaskAppWrapper._finish_manual_ingest(source, ok_body)` runs the vector update,
  then writes the terminal source status: `idle` with a new `last_run` on success; `error`
  with the old `last_run` on failure, and a 500 `{"error": "update_failed", "detail": ...}`
  answer instead of `ok`.
- The five manual-ingest handlers above call it in place of their
  `_update_source_status(..., state="idle", ...)` + `_notify_update()` + `jsonify(...)`.
- `_notify_update` stays as it is for `delete` and `delete_source` (out of scope).
- The docs contract test `tests/unit/test_api_reference_schedule_state_contract.py` reads
  the uploader's keys from the new call sites.
- `docs/docs/api_reference.md` (`GET /api/schedules`) states the new order and the 500
  answer.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `ingestion-source-status` (introduced by #650's change, not yet archived into
  `openspec/specs/`): this change ADDS two requirements for the manual-ingest path. It does
  not change the scheduled-refresh requirements.

## Impact

- `src/interfaces/uploader_app/app.py` (five handlers and one new method).
- `tests/unit/test_uploader_manual_ingest_status.py` (new).
- `tests/unit/test_api_reference_schedule_state_contract.py` (one regex and its docstring).
- `docs/docs/api_reference.md` (`GET /api/schedules` section only).
- No chat-app change: its proxies for these endpoints
  (`src/interfaces/chat_app/app.py` `/document_index/upload` `:5945`, `upload_url` `:6028`,
  `add_git_repo` `:6096`, `add_jira_project` `:6370`) already pass a non-200 JSON `error`
  through to the browser.
- Related armed issues: #648 (status at the START of an upload) and #700 (locked status-file
  writes). Neither has a PR on 2026-11-03, so this change uses `_update_source_status` as it
  is (issue #699 "Decision"); the one new helper is the single place each of them rebases on.
