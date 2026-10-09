## Context

Anchors at `origin/dev` `0189e2dd` (the issue body cites `0ddc96e1`; the code is the same):

- `src/bin/service_data_manager.py:45` — `lock = threading.RLock()`, the ingestion
  mutual-exclusion lock.
- `:71-80` `run_locked(name, func)`: `with lock:` → log → `set_source_status(name,
  state="running")` → `func()` → log → `data_manager.update_vectorstore(force=True)` →
  `set_source_status(name, state="idle", last_run=<utc iso>)`.
- `:82-84` `trigger_update()`: `with lock: data_manager.update_vectorstore(force=True)`.
- `:160` the scheduler job callback calls `run_locked`; `:188` `scheduler.start()`.
- `:197` `from src.utils.ingestion_status import build_ingestion_helpers`; `:199`
  `_ing = build_ingestion_helpers(data_manager.run_ingestion, lock)` — AFTER
  `scheduler.start()`.
- `:209` `FlaskAppWrapper(app, post_update_hook=trigger_update, ...)`.
- Callers already catch exceptions: `src/data_manager/scheduler.py:232-235` logs a failed
  job; `src/interfaces/uploader_app/app.py:487-493` `_notify_update` logs a failed hook.
- `src/utils/ingestion_status.py:16-81` — `build_ingestion_helpers`; `_status_lock`
  guards `_status` (`state`, `step`, `error`, `progress`); `run_initial_ingestion_async`
  resets `progress` to `None`, publishes `running`/`initializing`, runs under
  `ingestion_lock`, then `completed`/`done` or `error`/`failed`.
- `service_data_manager.py` is not imported by any unit test, so every added line there is
  uncovered for diff-cover. `src/utils/ingestion_status.py` and
  `src/bin/service_data_manager.py` are black-clean at `0189e2dd`.

## Goals / Non-Goals

Goals: during a scheduled refresh or an upload-triggered update the payload shows
`running` with a step that names the trigger; after it, `completed`; on failure, `error`
with the message. The initial ingest lifecycle is unchanged.

Non-goals: a progress counter for refreshes (the refresh calls
`update_vectorstore(force=True)` with no `embedding_progress`; see D3); `run_id` and
timestamps (#559); the benchmark wait loop; the per-source status file (a failed refresh
still leaves its per-source `state` at `running`, as today).

## Decisions

### D1 — `run_tracked(step, fn)` in `build_ingestion_helpers`

```python
def run_tracked(step: str, fn: Callable[[], Any]) -> Any:
    with ingestion_lock:
        with _status_lock:
            _status.update(
                {"state": "running", "step": step, "error": None, "progress": None}
            )
        try:
            result = fn()
        except Exception as exc:
            set_ingestion_status("error", step="failed", error=str(exc))
            raise
        set_ingestion_status("completed", step="done")
        return result
```

- The status changes to `running` only AFTER the lock is acquired. A refresh queued behind
  the initial ingest (or behind another refresh) must not overwrite the step of the run
  that holds the lock.
- `ingestion_lock` is the caller's `RLock`, so `run_tracked` is safe inside an outer
  `with lock:` (re-entrant).
- Re-raise keeps today's caller behavior: the scheduler and the uploader log the error.
- `completed`/`done` and `error`/`failed` mirror `run_initial_ingestion_async`.

### D2 — `run_source_refresh(name, func, update_vectorstore, set_source_status)`

Holds today's `run_locked` body, moved verbatim (the two `logger.info` lines, the two
`set_source_status` calls, `update_vectorstore(force=True)`), run through
`run_tracked(f"scheduled:{name}", body)`. `last_run` is
`datetime.now(timezone.utc).isoformat()`, as today. The module uses
`logging.getLogger(__name__)`; keep the same log messages.

The body lives in the tested module, not in `service_data_manager.py`, so that the
diff-cover gate scores it (see the gotcha in `CLAUDE.md` about untested entry modules).

### D3 — `progress` is reset to `null`, not threaded

`run_tracked` sets `progress` to `None` at start. Without the reset, the counter of the
initial ingest stays frozen in the payload during a refresh, and the benchmark stall rule
from #558 ("restart the budget only when `progress.done` changes") would time out a long
refresh as a stall. With `progress: null`, every `running` poll restarts the budget (the
pre-#558 rule). Threading `embedding_progress` into the refresh is a later change; the
issue puts the counter out of scope.

### D4 — Call sites in `service_data_manager.py`

1. Move the `build_ingestion_helpers` import to the module imports (isort order), and
   delete the local import at `:197`.
2. Move `_ing = build_ingestion_helpers(data_manager.run_ingestion, lock)` to directly
   after `lock = threading.RLock()` (`:45`). This removes a start-up window where a cron
   job can fire after `scheduler.start()` and before `_ing` exists (a `NameError`).
3. `run_locked` body → `_ing["run_source_refresh"](name, func,
   data_manager.update_vectorstore, set_source_status)`.
4. `trigger_update` body → `_ing["run_tracked"]("upload", lambda:
   data_manager.update_vectorstore(force=True))`.

Keep the `def` lines unchanged. The total of added lines in this file must stay at or
below 7, so that patch coverage stays above 80 %. If black wraps a call, count the wrapped
lines.

### D5 — Docs

`docs/docs/api_reference.md`, `GET /api/ingestion/status`: add that a scheduled refresh
reports `step` `scheduled:<source>` and an upload reports `step` `upload` while `running`,
then `completed`/`done` or `error`/`failed`. Add that a benchmark that waits on this
endpoint also waits during these runs.

## Risks / Trade-offs

- A benchmark started during a routine refresh now waits for it. The issue accepts this.
- A refresh that fails leaves `state == "error"` until the next run. The initial ingest
  behaves the same today.
- #559 also edits `src/utils/ingestion_status.py` (branch
  `fix/issue-559-ingest-run-identity`, unmerged). Whichever lands later rebases and keeps
  both; `run_tracked` is the one place where #559's `run_id`/`started_at` must also be set.
