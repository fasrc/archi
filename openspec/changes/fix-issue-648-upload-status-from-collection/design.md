## Context

The uploader (`src/interfaces/uploader_app/app.py`, `FlaskAppWrapper`) runs in the
data-manager process. Each manual-ingest handler collects (or, for `remove_git_repo`,
deletes) and then calls `_finish_manual_ingest` (`:507`), which runs `post_update_hook` =
`trigger_update` (`src/bin/service_data_manager.py:103`) → `run_upload_update`
(`src/utils/ingestion_status.py:144`) → `run_tracked("upload", ...)` (`:122`): `_join`
(`:90`) counts the run in and publishes `running`/`upload` if no run is shown running, the
run takes `ingestion_lock`, re-publishes `running`, runs the update, and `_finish` (`:107`)
counts it out and publishes `completed` (when no run is in flight) or `error`. Nothing
reports the collection that precedes the hook.

The in-flight counter (`_inflight` `:74`, `_join`, `_finish`) is the mechanism the issue
names. It already keeps `completed` unpublished while another run is counted in, which is
what the collection needs: count the upload in before the collection, let the update join
and finish as a run of its own, and count the upload out after.

Base: `origin/dev` `f457d3d1` with `origin/fix/issue-559-ingest-run-identity-v2` `97543dd2`
merged (PR #777; #559 task 1.1: `run_id`, `started_at`, `finished_at`, and a `now` seam on
`build_ingestion_helpers`). `git merge-tree` is clean. The line numbers above are those of
the merged tree.

## Goals / Non-Goals

**Goals**

- `GET /api/ingestion/status` reports `running` / `step=upload` from the start of a manual
  ingest's collection to the end of its vectorstore update, and never `completed` between
  the two.
- The collection takes no `ingestion_lock`.
- A failed collection or a failed update ends the run in `error`; no `error` is turned into
  `completed`.
- `app.py` call sites stay thin; the logic lives in `src/utils/ingestion_status.py` and one
  small wrapper method.

**Non-Goals**

- `delete` (`:353`) and `delete_source` (`:358`): they redirect a browser and keep
  `_notify_update`; a removal of one document is not a collection.
- The chat app, `POST /document_index/embed` (`embed` `:484` runs only the update; unchanged),
  and the per-source status file (#699, #700).
- #777's unfinished tasks (its 2.1–2.3) and its docs sentence "The response has four keys".
- A step of its own for a collection that starts while another run is shown `running`: the
  existing rule applies (the step of a queued run is not published over a running one), and
  the status is `running` either way.

## Decisions

### D1 — `track_upload()` is a context manager returned by `build_ingestion_helpers`

Exact code in tasks.md Appendix B. A generator-based `@contextmanager` closed over `_join`
and `_finish`, so it shares the counter and `_status_lock` with the tracked runs:

```python
        _join("upload")
        try:
            yield
        except Exception as exc:
            _finish("error", "failed", str(exc))
            raise
        _finish("completed", "done", keep_error=True)
```

`_join("upload")` publishes `running`/`upload` only when no run is shown running (as a run
that joins does today). The update inside the block (`run_upload_update` → `run_tracked`)
joins (counter 2), takes the lock, re-publishes `running`/`upload`, and finishes (counter 1:
it publishes nothing on success and `error` on failure). The block's exit counts the upload
out (counter 0) and publishes `completed`.

A context manager, not a `begin_upload()`/`end_upload()` pair, because the exit must run on
every path: a `return` from inside the handler's `try` and an exception from the collector.
`except Exception` (not `BaseException`) matches `run_tracked`; a `KeyboardInterrupt`
through the block leaves the counter at 1, as it does through `run_tracked`.

### D2 — `_finish` gets a keyword-only `keep_error=False`; the block's normal exit passes `True`

Why: `_finish_manual_ingest` swallows the hook's exception (it answers 500 and does not
re-raise), so after a failed update the block ends normally. Without the guard, the exit's
`_finish("completed", "done")` would overwrite the `error` the update just published
(counter 0 → publish). With `keep_error`, `_finish` decrements the counter and returns when
the state is already `error`.

For every existing caller the guard is off, so nothing else changes. A lock-taking run never
needs it: it re-publishes `running` after it takes the lock, so the state it finishes from
is `running`. The tracker never takes the lock and never re-publishes, which is why it needs
the guard. The queued-run rule is unchanged: a run that errors while another is queued still
publishes `error` at once, and the queued run still replaces it with `completed` when it
finishes (the queued run re-published `running` first).

Not chosen: "publish `completed` only if the state is `running`" for all callers. It is
equivalent for today's callers but widens the change to shared code that no test needs.

### D3 — The uploader wraps the collection and `_finish_manual_ingest` in `with self._tracked_upload():`

`_tracked_upload()` returns `self.track_upload()`, or `contextlib.nullcontext()` when the
wrapper has no tracker. The `with` sits inside each handler's existing `try`, so a collector
exception leaves the block (the tracker publishes `error`), reaches the handler's `except`,
and gets the same answer as today (`ingest_failed`, `delete_failed`, `upload_failed`, 500).
The `return self._finish_manual_ingest(...)` stays inside the block: the `with` exit runs
after the update and the source-status write, before the answer leaves the handler.

| Handler | Tracked span |
|---|---|
| `add_git_repo` | `collect_git` → `_finish_manual_ingest("git", ...)` |
| `remove_git_repo` | the `delete_resource` loop → `flush_index` → `_finish_manual_ingest("git", ...)`; the catalog scan and the 404 answer stay outside, so a not-found removal publishes nothing |
| `add_jira_project` | `collect_jira` → `flush_index` → `_finish_manual_ingest("jira", ...)` |
| `upload` | `ingest_uploaded_file` → `flush_index` → `_finish_manual_ingest("local_files", ...)` |
| `upload_url` | `collect_links` → `flush_index` → the "URL uploaded successfully" log → `_finish_manual_ingest("web", ...)` |

`upload_url` loses its `added_to_urls` / `upload_error` flags: the `except` answers the 500
directly. Same log lines, same answers. `remove_git_repo` is included because its deletes and
its update are reported as `upload` today (`run_upload_update`), and a benchmark wait must not
return while they run.

### D4 — `track_upload` is a keyword-only constructor argument with a class-level default of `None`

`FlaskAppWrapper.__init__` (`:46`) gains `track_upload: Optional[Callable[[],
ContextManager[None]]] = None` and stores it next to `post_update_hook` (`:110`). The class
attribute `track_upload = None` exists because the unit tests build the wrapper with
`object.__new__` (`tests/unit/test_uploader_manual_ingest_status.py`,
`tests/unit/test_uploader_embed_endpoint.py`) and never run `__init__`; without the class
default every handler would raise `AttributeError` in those tests. `service_data_manager.py`
passes `track_upload=_ing["track_upload"]` in the `FlaskAppWrapper(...)` call (`:219`).

### D5 — Tests

- `tests/unit/test_ingestion_status_upload_tracking.py` (new, 6 tests): running/upload during
  the block and completed after; no `completed` between collection and update; the collection
  runs while a `run_tracked` refresh holds the lock, and the status stays `running` after the
  block while the refresh runs; a raising block publishes `error`/`failed`/message; a failed
  update inside the block stays `error`; one `run_id` and `started_at` from the block's start
  through the update, `finished_at` set at the exit (uses #777's `now` seam).
- `tests/unit/test_uploader_upload_tracking.py` (new, 22 tests; the five handlers
  parametrized): the recorded order is `enter, collect, hook, exit`; a failed collection
  records `enter, collect, exit:<message>` and answers the handler's own 500; a failed update
  records `enter, collect, hook, exit` and answers 500 `update_failed`; without a tracker the
  order is `collect, hook`; a not-found removal records nothing and answers 404; the
  constructed wrapper keeps the tracker and the class default is `None`.
- `tests/unit/test_service_data_manager_source_status.py`: `_FakeUploader` accepts and records
  `track_upload` (the only edit to an existing test; without it `main()` raises `TypeError` on
  the new keyword), plus one test that the tracker `main()` hands over publishes
  `running`/`upload` on `/api/ingestion/status` and `completed` after. The test waits (5 s
  bound) for the fake initial ingest to publish `completed` first, so it cannot race the
  ingestion thread `main()` starts.

Mutation check on the staged run (each mutation reverted): no `keep_error` guard → 1 failure;
no `_join` at the block start → 4; `_tracked_upload` always a no-op → 15; no class default →
6; the `service_data_manager.py` keyword dropped → 1; a collection error not published → 1.

### D6 — Docs

`docs/docs/api_reference.md`, `GET /api/ingestion/status` (`:738` paragraph): a manual ingest
is reported with `step` `upload` from the start of its collection to the end of its update,
takes no ingestion lock for the collection, and ends in `error` on a failed collection or a
failed update; the queued-step sentence also names an upload's collection.
`docs/docs/benchmarking.md` (`:230` and `:238`): the "Scheduled source refreshes ..." sentence
and the "What this wait does **not** cover" paragraph say the same from the benchmark's side.
Exact text in tasks.md Appendix C. No test pins these sentences;
`tests/unit/test_api_reference_schedule_state_contract.py` and
`tests/unit/test_benchmarking_docs_inline_bundle.py` still pass. The "four keys" sentence is
#777's docs task, not this change's.

### D7 — Stacked on PR #777

The branch is `origin/dev` `f457d3d1` plus a merge of
`origin/fix/issue-559-ingest-run-identity-v2` `97543dd2`. The PR targets `dev`, so its diff
shows #777's three commits until #777 merges; the PR body says so (task 2.2). If #777 merges
first (squash), this branch's copy of the same content merges clean; if this PR merges first,
#777's diff shrinks to nothing and a human closes it against #559's remaining tasks. Both are
human decisions; neither is made here.

### D8 — Review round 1 (2026-11-09): the step after the lock owner ends, and the identity after a failed collection

Two Codex P2 findings on `b90ee6ba`, both reproduced on the host before the change.

1. **Only collections remain.** When the lock-owning run (the initial ingestion or a
   `run_tracked` refresh) finished while a tracked upload still collected, `_finish` saw
   `_inflight == 1` and published nothing: the status kept the finished run's step and its
   frozen embedding counter. `_ingest_is_progressing` (`src/bin/service_benchmark.py`)
   returns `False` on a frozen counter, so a collection longer than
   `BENCH_INGEST_WAIT_TIMEOUT` aborted a benchmark as stalled, and a shorter one was
   charged to `ingest_wall_seconds` because the wait saw no upload step until the update.
   Fix: a second counter, `_collecting`, counts the tracked uploads among `_inflight`
   (`_join`/`_finish` take `collecting=True` from the tracker). A `_finish` that leaves
   `_inflight == _collecting` with the state `running` publishes `step="upload"`,
   `progress=None` and nothing else: no identity change, no `finished_at`, because the
   run (shared with the collection) is not over. A published `error` is left in place,
   and a remaining lock-taker (`_inflight != _collecting`) still publishes its own step
   after it takes the lock, as before.
2. **A step after a failed collection.** A collection that failed during the initial
   ingestion published `error` and `finished_at` (the queued-run rule: an error is never
   hidden). The ingestion's next `progress_callback` → `set_ingestion_status("running",
   step=...)` bypassed `_set_running`, so the status read `running` with the failed run's
   `run_id` and a non-null `finished_at`. Fix: `set_ingestion_status` applies the same
   transition as `_set_running` — `running` from a non-`running` state gets a new
   `run_id`/`started_at` and `finished_at = None` (`_new_run_identity()`, shared by
   both) — and keeps `progress`, which `test_set_ingestion_status_preserves_progress`
   requires. A step update inside a live run is unchanged
   (`test_progress_callback_step_update_keeps_run_identity`).

Not chosen: routing the progress callback through `_publish_running` (it resets
`progress` on every step); publishing `running`/`upload` with a new identity when only
collections remain after an `error` (it would hide the failed collection's error).

3. **Round 2 (sync adversarial pass on `7146c180`), a join after a published error.**
   `_join` published `running` only for the first run in (`_inflight == 1`). A tracked
   collection that failed while a sibling still collected published `error` (the
   queued-run rule: a failure is never hidden); a third collection that joined after that
   left the status at `error` with `finished_at` set for its whole collection, until its
   update took the lock. The benchmark wait raises on `error`, which is the right outcome
   for the failure itself, but the status also said "finished" while new work ran. Fix:
   `_join` publishes `running` whenever the shown state is not `running`, whatever the
   count in flight. The first-run guard was a proxy for "no run is shown running"
   (`docs/docs/api_reference.md`: a run that starts when no run is reported as `running`
   publishes its step at once), and the two differ only in this case: a terminal state
   with runs in flight is always an `error` left by a failed sibling. The sibling that was
   already collecting when the error was published stays under that `error` until its
   update re-publishes `running`: that is the existing queued-run rule, kept on purpose,
   because the error must be shown at least until a later run starts. Not chosen: a
   separate field that keeps the earlier failure (a queued lock-taker on `dev` erases it the
   same way when it publishes `running`; the data-manager log keeps the message). Test:
   `test_a_collection_that_starts_after_a_siblings_failure_is_running`; the first-run guard
   restored → 1 failure.

   **Known limitation (final adversarial pass on `41481966`, tracked as #828).** Rule 3 lets
   a newer run identity start (a collection that joins after a sibling's `error`) while an
   older lock-holding run is still active; that older run's later terminal publish then
   lands on the newer identity with a `finished_at`, while the newer collection still runs.
   It needs two failures in one window (the sibling's collection and the older run). The
   remedy, a per-operation run generation with the older failure kept visible separately,
   is a change to the identity model and is the work order of #828; the state before
   `41481966` had the inverse wart with one failure (a new collection shown as a finished
   error).

   **The same limitation with one failure (adversarial pass on `36c1c436`, after item 4;
   recorded on #828).** A collection fails while the initial ingestion holds the lock and
   publishes `error`; a scheduled refresh that joins the queue then publishes `running`
   (rule 3) before it does any work, as the ingestion's next progress step does (item 2).
   After both succeed the status is `completed` with no trace of the failure, and a
   benchmark poll that missed the short `error` accepts it. Restricting rule 3 to
   collections would only move the erasure to the lock take (`_publish_running`, the `dev`
   behaviour), so it is not done here. The failure-aware status the pass asks for, a
   failure kept apart from the shown run and the benchmark wait rejecting it until a later
   run resolves it, is #828's Design A; the acceptance test (a concurrent
   failure-and-queued-refresh sequence) is listed there.

4. **Async Codex pass on `b10a28a0` (P2, thread `PRRT_kwDORx3_1861RTae`): `completed` over
   a collection's error.** A collection that failed during the initial ingestion's last
   phase, with no sibling in flight and no later progress step, published `error`; the
   ingestion's `_finish("completed")` then saw `_inflight == 0` and replaced it with
   `completed` under the failed run's `run_id` and a new `finished_at`. The benchmark wait
   (`wait_for_ingestion_completion`) accepts `completed` and raises on `error` with no
   `run_id` check, so the outcome depended on which side of the overwrite it polled. D2's
   `keep_error` guard covered only the tracker's own exit; a `run_tracked` refresh that ends
   after a failed collection had the same hole. Fix: the rule is general. A `completed`
   finish that finds `error` shown returns after the count-out, whatever the count in
   flight. The condition is exact without extra state: every `running` publish clears
   `error`, so an `error` shown at a `completed` finish was published after the finishing
   run's last `running`, by work inside its window (a failed collection, or the update
   inside a tracker's block). The `keep_error` flag is removed; the tracker's exit passes
   nothing. A later failure still replaces an earlier one (`state == "error"` publishes as
   before; the attribution of that case is #828). Not chosen: publishing `completed` with a
   new identity (the failure would be hidden, which the queued-run rule forbids). Tests:
   `test_the_ingest_ending_after_a_failed_collection_alone_keeps_the_error`,
   `test_a_refresh_ending_after_a_failed_collection_alone_keeps_the_error`, and the guard
   `test_the_ingest_failing_after_a_failed_collection_publishes_its_own_error`. Mutation
   check: no guard → 4 failures (the two D2 tests and the two new ones); guard on every
   finish, the `completed` check dropped → 1 (the guard test); guard only for the tracker,
   D2's semantic → 2.

Tests (`tests/unit/test_ingestion_status_upload_tracking.py`):
`test_the_ingest_ending_during_a_collection_shows_the_upload_step`,
`test_a_step_after_a_failed_collection_starts_a_new_run`, and the two guards
`test_the_collection_ending_during_a_refresh_keeps_the_refresh_step` and
`test_the_ingest_ending_after_a_failed_collection_keeps_the_error`. Mutation check: no
`elif` → 1 failure; `elif` without the `running` guard → 1; `elif` on any remaining run →
1; no identity transition in `set_ingestion_status` → 1.

## Risks / Trade-offs

- A failed collection now publishes `error` on `/api/ingestion/status`, where it published
  nothing before. A benchmark wait treats `error` as terminal; that is honest (the upload run
  failed) and the next run clears it. The alternative, no publish, would leave `running`
  forever: the counter must reach zero with a terminal publish.
- An upload that starts while an ingest is shown `running` is counted in without a step of
  its own (the existing queued-run rule). The status is `running` throughout, which is what a
  wait needs.
- `remove_git_repo`'s catalog scan runs before the tracked span; it is an in-process catalog
  walk, not a crawl.
- Reviewers see #777's diff in this PR until #777 merges. Findings on `run_id`, `started_at`
  or `finished_at` belong to #777.
