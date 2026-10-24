## Context

`build_ingestion_helpers` (`src/utils/ingestion_status.py`) returns `run_source_refresh`.
Its inner `body()` runs inside `run_tracked(f"scheduled:{name}", body)`, which publishes the
global status (`running`, then `completed`, or `error` + re-raise). The per-source status is
written through the injected `set_source_status(source, *, state=None, last_run=None)`
(`src/bin/service_data_manager.py:60-70`), which does a read-modify-write of
`ingestion_status.json` and does NOT swallow exceptions.

## Goals / Non-Goals

**Goals:** a failed refresh leaves the source entry at `error`, never `running`; the
exception still propagates; the global status is still `error`.

**Non-Goals:** an error-detail field in the source entry; any UI change; any change to
`run_tracked`, `run_upload_update`, or the initial ingest.

## Decisions

### D1 — Catch inside `body()`, around every step
Wrap the whole of `body()` (the `running` write, `func()`, `_update_or_raise`, and the
`idle` write) in `try/except Exception`. In the handler: call
`set_source_status(name, state="error")`, then `raise` (bare, to keep the traceback).
The handler runs inside `run_tracked`, so `run_tracked` still sees the exception and
publishes the global `error`.

Alternative rejected: catch around `run_tracked(...)` from the outside. That writes the
source status after the ingestion lock is released, so a queued run can write `running`
first and then be overwritten with `error`.

### D2 — `Exception`, not `BaseException`
Match `run_tracked`, which catches `Exception`. `KeyboardInterrupt` and `SystemExit` stop
the process; they are out of scope.

### D3 — Do not write `last_run` on failure
The `error` call passes only `state="error"`. `last_run` is the time of the last SUCCESSFUL
refresh; the scheduler passes it to the collectors (`schedule_map` in
`service_data_manager.py`), so a failed run must not advance it.

### D4 — A failure of the `error` write does not mask the cause
Put the `set_source_status(name, state="error")` call in its own `try/except Exception`
that calls `logger.exception("Failed to record error status for %s", name)` and continues
to the bare `raise` of the original exception. Test: a `set_source_status` fake that raises
`OSError` on `state="error"` while `func` raises `RuntimeError("collect down")` — the call
raises `RuntimeError("collect down")`, not `OSError`.

### D5 — No UI change
Checked by Loop 1: `upload.js` reads only `last_run` from each `/api/schedules` job
(`upload.js:1066`); no template or script renders the job `state`. The uploader stores
`state` as a plain string. An `error` value cannot crash either reader.

## Risks / Trade-offs

- The existing test `test_run_source_refresh_error_from_update_vectorstore` records only
  `idle` calls; it stays valid (no `idle` call on failure). Do not change it.
