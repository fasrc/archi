## Why

A scheduled source refresh that fails leaves its per-source status at `running` for ever.
`run_source_refresh` in `src/utils/ingestion_status.py:115-133` writes
`set_source_status(name, state="running")`, runs the collector `func()`, runs
`_update_or_raise(update_vectorstore)`, and then writes `state="idle"`. The `idle` write is
the only terminal write, and it runs only on success. When `func()` raises, or the sync
returns `"failed"` (which `_raise_if_failed` turns into a `RuntimeError`), the global
`/api/ingestion/status` shows `error`, but the source entry in `ingestion_status.json`
stays `running`. `/api/schedules` (`src/bin/service_data_manager.py:239-248`) reports that
entry as the job `state`, so an operator cannot tell a failed refresh from an active one.

Source: issue #650 (deferred finding from the Codex review of PR #645, merged 2026-10-09).

## What Changes

- `run_source_refresh` writes `set_source_status(name, state="error")` when any step of the
  refresh raises, then re-raises the original exception. The global status still becomes
  `error` through `run_tracked`.
- A failed refresh does not write `last_run`, so the next scheduled run does not skip the
  window that failed.
- If the `error` write itself raises, the refresh logs it and re-raises the ORIGINAL
  exception, so the real cause is not masked.

## Capabilities

### New Capabilities
- `ingestion-source-status`: the per-source status entry reaches a terminal state
  (`idle` on success, `error` on failure) after every scheduled refresh.

### Modified Capabilities
- None. (`ingestion-refresh-status` from #560 is not archived into `openspec/specs/`, so
  this change adds a new capability and does not modify it.)

## Impact

- `src/utils/ingestion_status.py` (`run_source_refresh` only).
- `tests/unit/test_ingestion_status_lock.py` (new tests at the end of the file).
- No UI change: the chat-app upload page (`src/interfaces/chat_app/static/modules/upload.js`)
  renders only `last_run` from `/api/schedules`, not `state`, and the uploader
  (`src/interfaces/uploader_app/app.py:495-533`) stores `state` as an opaque string.
