## Context

Anchors at `origin/dev` `5564e016` (the issue body cites `0ddc96e1`; some line numbers
moved, the code did not):

- `src/utils/ingestion_status.py:29-33` — the status dict; `:35-39`
  `set_ingestion_status` replaces `state`, `step`, `error`; `:45-57`
  `run_initial_ingestion_async` calls `run_ingestion_fn(progress_callback=...)`.
- `src/data_manager/data_manager.py:69-110` — `run_ingestion(progress_callback=None)`; the
  last line calls `self.vector_manager.update_vectorstore()`.
- `src/data_manager/vectorstore/manager.py:300-314` `update_vectorstore`, `:316`
  `_sync_vectorstore`, `:372` the `_add_to_postgres(files_to_add)` call, `:558`
  `_add_to_postgres`. The database loop starts at `:742` (`total_files = ...`). Commit
  sites: `:809-815` (hierarchical branch), `:838-844` (embed failure), `:912-918` (normal
  file), `:926-931` (final batch).
- `src/bin/service_benchmark.py:1401` `_ingest_is_progressing(state, step)`; `:2586`
  `wait_for_ingestion_completion`; `:2720` the call site in the poll loop; `:2674-2687` the
  `BENCH_INGEST_MAX_WAIT=0` warning comment that says the payload has no counter.
- All four source files are black-clean at `5564e016` (`black --check` passes), so in-place
  edits do not reflow nearby code.

## Goals / Non-Goals

Goals: a counter that moves during the embedding loop; a stall budget that trips when that
counter stops; no change for a payload without the counter.

Non-goals: `run_id` and timestamps (#559); status for scheduled or upload-triggered
updates (#560); new budget defaults; a counter for the scrape or file-processing phases.

## Decisions

### D1 — Payload shape

`progress` is `None` or `{"done": int, "total": int | None}`. The initial dict holds
`"progress": None`. `set_ingestion_progress(done, total=None)` stores a NEW dict each call
(so `get_ingestion_status`'s shallow copy never shares a dict that a later call mutates).
`run_initial_ingestion_async` sets `progress` back to `None` before it publishes
`"initializing"`, so a second ingest never shows the first ingest's counter.
`set_ingestion_status` keeps its exact signature and behaviour and does not touch
`progress`; the `completed` and `error` payloads keep the last counter.

### D2 — One callback name end to end: `embedding_progress`

The callback is `embedding_progress(done: int, total: int | None) -> None`.

- `build_ingestion_helpers` calls
  `run_ingestion_fn(progress_callback=<step lambda>, embedding_progress=set_ingestion_progress)`.
- `DataManager.run_ingestion(self, progress_callback=None, embedding_progress=None)` passes
  it on: `self.vector_manager.update_vectorstore(embedding_progress=embedding_progress)`.
  The other two `update_vectorstore()` calls in `data_manager.py` (`:128`, `:132`) and the
  one in `app.py` are unchanged — those paths do not report to the status dict (#560).
- `VectorStoreManager.update_vectorstore(self, embedding_progress=None)` →
  `self._sync_vectorstore(embedding_progress=embedding_progress)` →
  `self._add_to_postgres(files_to_add, embedding_progress=embedding_progress)`.

Existing test fakes that this breaks, and which a task widens (assertions unchanged):
the `fake_run_ingestion(progress_callback=None)` functions in
`tests/unit/test_ingestion_status_lock.py` (5 sites) gain `**_kwargs`; the two
`_sync_vectorstore` lambdas in `tests/unit/test_ingest_run.py` (`:322`, `:340`) gain
`**_`. `MagicMock` fakes of `_add_to_postgres` accept the keyword already.

### D3 — What `done` and `total` count

`total` is `len(files_to_add_items)` — the files queued for embedding. `done` is the
number of files the database loop has committed so far: each commit site adds its
`files_since_commit` to a running `files_committed` before it resets
`files_since_commit` to `0`, then reports `(files_committed, total)`. The loop first
reports `(0, total)` once, just before the `for file_idx ...` loop (after the
hierarchical-schema commit), so the payload shows the counter as soon as the embedding
loop starts.

Files that failed in `process_file` are skipped by the loop (`if not processed: continue`)
and are never committed, so `done` can end below `total`. That is correct: `done` counts
committed work, not attempted work. The docs say so.

The counter is NOT reported during the parallel `process_file` phase or the
`_remove_from_postgres` phase. During those phases `progress` is `None` and the old stall
rule applies. This is deliberate: the issue scopes the counter to the embedding phase.

### D4 — A failing callback never aborts the ingest

All five report points go through one private helper in `manager.py`:

```python
def _report_embedding_progress(embedding_progress, done, total):
    if embedding_progress is None:
        return
    try:
        embedding_progress(done, total)
    except Exception:
        logger.warning("Embedding progress callback failed", exc_info=True)
```

So each commit site gains two lines (the running sum and the helper call) and no new
branch.

### D5 — Benchmark stall rule

A new pure helper in `service_benchmark.py`:

```python
def _ingest_progress_done(payload) -> Optional[int]:
```

returns `payload["progress"]["done"]` when `progress` is a dict and `done` is an `int`
that is not a `bool` and is `>= 0`; otherwise `None` (absent key, `null`, a non-dict, a
missing or malformed `done`). Malformed is treated as absent, so a bad payload falls back
to today's rule and never kills a run on its own.

`_ingest_is_progressing(state, step, done=None, last_done=None)`:

1. `state` not in `_INGEST_PROGRESS_STATES` → `False` (unchanged).
2. `step` is `"initializing"` → `False` (unchanged).
3. `done is None` → `True` (today's rule, the older-data-manager path).
4. otherwise → `done != last_done`.

The wait loop keeps `last_done: Optional[int] = None`. When `_ingest_is_progressing`
accepts a poll, the loop sets `last_done = done`. The first poll that carries a counter
is therefore accepted (`0 != None`), and later polls are accepted only when `done` moves.
A poll whose counter goes back to `None` (a new ingest that reset it) uses rule 3.

The poll log line adds `progress=%s` (the `done` value or `None`). The timeout message
helper is not changed.

### D6 — Text that becomes false

- `service_benchmark.py` `wait_for_ingestion_completion` docstring and the
  `BENCH_INGEST_MAX_WAIT=0` warning comment say the payload has no counter; reword both to
  say the counter exists from a data manager that publishes it, and that without it only
  the ceiling catches a wedge. The warning message text itself stays (it is still true for
  an older data manager).
- `_ingest_is_progressing` docstring gains rules 3 and 4.
- `docs/docs/benchmarking.md`: the `BENCH_INGEST_WAIT_TIMEOUT` table row and the
  "alive but stuck" bullet.
- `docs/docs/api_reference.md` `GET /api/ingestion/status`: list the four keys and the
  `progress` shape.

## Risks / Trade-offs

- **A batch that takes longer than the stall budget now trips it.** One batch is 25 files
  (`commit_batch_size`). At the measured rate (about 106 minutes for the dev corpus) a
  batch takes minutes, far below 7200 s. An operator with very large files can raise
  `BENCH_INGEST_WAIT_TIMEOUT`; the docs say so.
- **Older benchmark image, newer data manager:** the old reader ignores the extra key.
- **Newer benchmark image, older data manager:** no `progress` key → today's rule.
- **Concurrent writers:** only the ingestion thread calls `set_ingestion_progress`, under
  `_status_lock`, as with `set_ingestion_status`.
