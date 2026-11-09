## Why

`GET /api/ingestion/status` reports an upload only while its vectorstore update runs. Each
uploader handler (`src/interfaces/uploader_app/app.py`: `add_git_repo` `:259`,
`remove_git_repo` `:271`, `add_jira_project` `:310`, `upload` `:326`, `upload_url` `:363`)
collects or deletes first and calls `_finish_manual_ingest` (`:507`) after. That method runs
`post_update_hook` (`trigger_update`, `src/bin/service_data_manager.py:103`), which runs
`run_upload_update` (`src/utils/ingestion_status.py:144`), and only then the status becomes
`running` / `step=upload`. A long crawl (URL, git, Jira) leaves the endpoint at its previous
`completed`, so a benchmark can pass its one-time ingest wait and have the vectorstore update
start mid-run (Codex finding on PR #645; issue #648).

## What Changes

- **A `track_upload()` context manager in `build_ingestion_helpers`**
  (`src/utils/ingestion_status.py`). It counts an upload into the existing in-flight counter
  before the block and out after it, and takes no `ingestion_lock`. The status shows `running`
  / `step=upload` from the block's start; the vectorstore update inside the block joins and
  finishes as a run of its own, so `completed` is never published between the collection and
  the update. A block that raises publishes `error` with the message. A block that ends
  normally publishes `completed` when no run is in flight, unless its update already
  published `error` (`_finish` gains a keyword-only `keep_error`).
- **Every manual-ingest handler runs inside the tracker.** `FlaskAppWrapper` takes a
  keyword-only `track_upload` and wraps the collection and `_finish_manual_ingest` of
  `add_git_repo`, `add_jira_project`, `upload` and `upload_url`, and the deletes of
  `remove_git_repo`, in `with self._tracked_upload():`. Without a tracker (the class default
  `None`) the handlers run as before. `service_data_manager.py` passes `_ing["track_upload"]`.
- **Docs.** `docs/docs/api_reference.md` (`GET /api/ingestion/status`) and
  `docs/docs/benchmarking.md` ("What this wait does not cover") describe the earlier start.
- **Stacked on PR #777 (#559).** The branch merges `origin/fix/issue-559-ingest-run-identity-v2`
  (`97543dd2`: `run_id`, `started_at`, `finished_at`) because both changes edit
  `src/utils/ingestion_status.py`; the issue's Decision allows it ("rebase on it and keep both
  changes"). With #777, an upload's run identity is assigned at the start of its collection and
  kept through its update.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingestion-refresh-status`: an upload is reported as a running ingest from the start of its
  collection, not only during its vectorstore update.

## Impact

- Code: `src/utils/ingestion_status.py` (`track_upload`, `_finish`),
  `src/interfaces/uploader_app/app.py` (`FlaskAppWrapper.__init__`, five handlers,
  `_tracked_upload`), `src/bin/service_data_manager.py` (one keyword argument).
- Tests: new `tests/unit/test_ingestion_status_upload_tracking.py` and
  `tests/unit/test_uploader_upload_tracking.py`; one edit to
  `tests/unit/test_service_data_manager_source_status.py` (the fake uploader accepts and
  records `track_upload`; one new test).
- API: no new keys. `state` and `step` are published earlier for uploads. A failed collection
  now ends the run in `error`, where it published nothing before.
- Out of scope: `delete` and `delete_source` (browser redirects through `_notify_update`), the
  chat app, the per-source status file (#699, #700), and #777's unfinished benchmark and docs
  tasks.
