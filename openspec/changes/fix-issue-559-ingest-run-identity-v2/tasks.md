## 0. Read first (no checkbox)

- **Loop image gap (#776).** The `archi-loop` image has no `slack_sdk`. Without it, pytest stops at collection with 3 errors in `tests/unit/test_slack_*.py`, and the pre-commit hook refuses every commit. At the start of each turn, run `python -c "import slack_sdk"`. If it fails, run `pip install slack_sdk==3.45.0` (the pin in `requirements/requirements-base.txt:172`) before any test or gate run. Do not edit any requirements file for this.
- **Gate.** Run `bash scripts/gate.sh` in the foreground, with no pipe and no redirect. Never put it in the background, and never end a turn while it runs. The pre-commit hook runs the same gate; commit only through the hook, never with `--no-verify`.
- **Scope.** The design of record is `design.md` D1–D4 in this folder. Do not reuse code from the old branch `fix/issue-559-ingest-run-identity`; the operator decided on 2026-10-19 to rebuild on the current module.
- Each checkbox below is one commit with the suite green. Tick the box in the same commit as its work.

## 1. Run identity in the ingestion status (D1)

- [x] 1.1 In `tests/unit/test_ingestion_status_lock.py`, write the failing tests, watch them fail, then make them pass in `src/utils/ingestion_status.py` in the same commit. Tests (pass a fixed `now` seam that returns known aware UTC datetimes, so the asserted strings are exact):
      (a) before any run, `run_id`, `started_at`, and `finished_at` are present and `None`;
      (b) inside a fake `run_ingestion_fn` of `run_initial_ingestion_async`, the status has a UUID4 `run_id` (`uuid.UUID(run_id).version == 4`), `started_at` equal to the seam's time in `isoformat()`, and `finished_at` `None`;
      (c) after `completed`, `finished_at` is set, is not earlier than `started_at`, and `run_id` is unchanged;
      (d) after a failing run, `state` is `error`, `error` carries the message, and `finished_at` is set;
      (e) a `progress_callback` step update inside the run keeps `run_id` and `started_at`, and `finished_at` stays `None`;
      (f) a first run completes, then a second run starts: the two `run_id`s differ;
      (g) a `run_tracked` run that queues behind a running run and gets the lock after that run's `completed` keeps the first run's `run_id` and `started_at`, and `finished_at` stays `None` until the queued run publishes `completed` (reuse the pattern of `test_completed_is_not_published_while_a_run_is_queued`);
      (h) a run publishes `error` while another run is queued; when the queued run gets the lock, it has a new `run_id` and a new `started_at`, and `finished_at` is `None` (reuse the pattern of `test_error_is_published_even_with_a_run_queued`);
      (i) every existing test in the file passes unchanged.
      Code: add the three keys (`None`) to the status dict; in `_set_running`, when the previous `_status["state"]` is not `"running"`, set `run_id = str(uuid.uuid4())`, `started_at = now().isoformat()`, and `finished_at = None`; in `_finish`, add `finished_at = now().isoformat()` to the update that publishes the terminal state. Do not change `set_ingestion_status` or `set_ingestion_progress`. Add the keyword-only `now` argument to `build_ingestion_helpers` (default `datetime.now(timezone.utc)`), and describe the three keys and the run boundary in the module and function docstrings. Commit: `feat(#559): run identity and timestamps in the ingestion status`.

## 2. Benchmark duration and source (D2, D3, D4)

- [ ] 2.1 In `tests/unit/test_benchmark_ingest_wait.py`, write the failing tests, watch them fail, then make them pass in `Benchmarker.wait_for_ingestion_completion` (`src/bin/service_benchmark.py`) in the same commit. Use the file's `FakeClock`, `_scripted`, and `_running` helpers, and a fixed `wall_now` seam. Tests:
      (a) observed progress, and a `completed` payload with `started_at` and `finished_at` 300 s apart → returns `300.0` and `bench.ingest_wall_source == "status_timestamps"`;
      (b) observed progress, no timestamps → the observed span and `"harness_observed"`;
      (c) observed progress, an unparsable `started_at` → `"harness_observed"`, no exception; the same for a naive (no offset) timestamp, and for `finished_at` earlier than `started_at`;
      (d) observed progress, then a `scheduled:git` or `upload` step, then `completed` with usable timestamps → the observed span that excludes the refresh, and `"harness_observed"` (D2 third rule);
      (e) no observed progress, the first answered poll is `pending` with `run_id` `None`, and the `completed` payload has `run_id` `"r2"` → `"missed"`; the return value is the timestamp duration when the timestamps are usable, else `None`;
      (f) the first answered poll is `completed` with `finished_at` after the `wall_now` time → `"missed"` and the timestamp duration;
      (g) the first answered poll is `completed` with `finished_at` before the `wall_now` time → returns `None` and the source is `None`;
      (h) every existing test in the file passes unchanged.
      Code: add the keyword seam `wall_now` (default `datetime.now(timezone.utc)`); set `self.ingest_wall_source = None` at the start of the method; record the first answered poll's `run_id` and whether a refresh or upload step was seen on any answered poll; apply D2, D3, and the D4 table at the `completed` poll, and set `self.ingest_wall_source` before each return. Put the timestamp parsing in one small module-level helper that returns `None` on `TypeError` or `ValueError` or a naive result. Replace the docstring sentence that says an exact figure needs a data-manager change (#428) with a description of the timestamp path and the source values. Commit: `feat(#559): derive the ingest duration from the status timestamps`.
- [ ] 2.2 In `tests/unit/test_benchmark_report_records_running_config.py`, next to `test_records_ingest_wall_seconds_on_the_arm`, write the failing tests, watch them fail, then make them pass in the same commit: `ResultHandler.handle_results(..., ingest_wall_source="status_timestamps")` writes `ingest_wall_source` with that value in the arm record; without the argument the key is present and `None`. Code: add the keyword argument `ingest_wall_source: Optional[str] = None` to `handle_results`, write it next to `"ingest_wall_seconds"`, and extend the comment above that record key to name the new sibling key and its four values. In `Benchmarker.run()`, pass `ingest_wall_source=getattr(self, "ingest_wall_source", None)` to `handle_results`, next to `ingest_wall_seconds=`. Commit: `feat(#559): record the ingest duration source on each arm`.

## 3. Publish

- [ ] 3.1 Run `bash scripts/gate.sh` on the tip and confirm it exits 0 with patch coverage ≥ 80 %. Confirm `git diff origin/dev --stat -- docs/` prints nothing. Tick this box, and commit the tick through the hook (`chore(#559): tick task 3.1`). Then push with `git push -u origin fix/issue-559-ingest-run-identity-v2`. Write the PR body to `/tmp/pr-559.md`: line 1 is `Closes #559` (in the body, never in the title); then a per-task summary, the D4 table from `design.md`, the gate result, and one sentence that this branch replaces `fix/issue-559-ingest-run-identity` on the operator's 2026-10-19 decision. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-559-ingest-run-identity-v2 --title "ingestion status: run_id and started_at/finished_at, and the benchmark's ingest duration source" --body-file /tmp/pr-559.md`.
      The task is complete when `gh pr view --repo fasrc/archi fix/issue-559-ingest-run-identity-v2` prints the PR.
