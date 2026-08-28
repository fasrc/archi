# Tasks — a stall budget for the benchmark's ingestion wait

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail for the stated reason, write the smallest fix, run
`bash scripts/gate.sh`, commit. Never end a task with the suite red, and never use
`--no-verify`.

Four standing notes for every task:

- **Scope.** The only production file to edit is `src/bin/service_benchmark.py`, and inside
  it only `wait_for_ingestion_completion` (`src/bin/service_benchmark.py:2092-2165`). Do
  not touch `deploy/**`, `.github/workflows/**`, `config/**`, `scripts/gate.sh`,
  `ralph.conf`, `PROMPT.md`, the `Makefile`, or the `Containerfile`.
- **The class is `Benchmarker`.** Issue #378 calls it `BenchmarkService`; no such class
  exists. Every file:line anchor in the issue is correct, only the class name is not.
- **Coverage.** `src/bin/service_benchmark.py` is inside `--cov=src`, so the changed lines
  do report to `diff-cover` and must be covered. The file is black-clean today — keep it
  that way so a reformat does not drag untouched lines into the diff.
- **Known flake.** `tests/unit/evaluation/qa/test_jobs_history.py::test_job_manager_terminates_running_evaluation_process`
  races under CPU load and is unrelated to this change. If the gate fails on only that
  test, re-run it.

All tests go in a new `tests/unit/test_benchmark_ingest_wait.py`.

## 1. Build the harness and stop killing healthy ingests

- [x] 1.1 `model: opus` — Create `tests/unit/test_benchmark_ingest_wait.py` with the shared fakes, then make
      the stall clock real.
      Harness: `import src.bin.service_benchmark as sb`; build the subject with
      `bench = object.__new__(sb.Benchmarker)` (the idiom at
      `tests/unit/test_benchmark_ragas_dialect.py:78`) and set
      `bench.config = {"services": {"data_manager": {"internal_port": 7871, "external_port": 7881}}}`
      — that is the only attribute the method reads. Fake the clock with
      `monkeypatch.setattr(sb, "time", fake_time)` where `fake_time.monotonic()` returns a
      mutable `now` and `fake_time.sleep(n)` advances it by `n`; fake the network with
      `monkeypatch.setattr(sb, "url_request", fake_request)` whose
      `urlopen(url, timeout=...)` returns a context manager whose `read()` yields JSON
      bytes. Set the budget with
      `monkeypatch.setenv("BENCH_INGEST_WAIT_TIMEOUT", "60")` and
      `monkeypatch.setenv("BENCH_INGEST_POLL_INTERVAL", "5")`.
      RED test: an opener that answers `{"state": "running", "step": "Updating vectorstore"}`
      on every call, and raises a module-level sentinel exception on its 100th call, must
      make `wait_for_ingestion_completion` raise **that sentinel** — 500 simulated seconds,
      more than eight budgets. Watch it fail with `TimeoutError` on `c60e6a69`; that
      failure is the whole bug.
      Implement: add `last_progress_time = start_time` beside `start_time`
      (`src/bin/service_benchmark.py:2112`); on the non-terminal success path, immediately
      before the existing `break`, set `last_progress_time = time.monotonic()`; change the
      abort test at `src/bin/service_benchmark.py:2155` to compare
      `now - last_progress_time` against `timeout_seconds`. Keep `start_time` — the message
      still reports total elapsed. Gate green; commit.
- [x] 1.2 `model: sonnet` — Guard the two fast-fail paths that must survive 1.1. **These pass once 1.1 lands —
      that is the point of them. Do not contrive a failure first.**
      Test one: an opener that raises `urllib.error.URLError("boom")` for every candidate
      URL raises `TimeoutError`, and the fake clock shows it was raised within about one
      budget of the start (assert the elapsed simulated time is under two budgets, not an
      exact float). Test two: an opener answering
      `{"state": "error", "step": "Embedding", "error": "cuda oom"}` raises `RuntimeError`
      whose message contains `Embedding`, on the first poll — assert the opener was called
      no more than four times, so a regression that swallows the error into the poll loop
      is caught. Test three: an opener answering `{"state": "completed"}` returns `None` and
      raises nothing. Gate green; commit.
- [ ] 1.3 `model: sonnet` — RED test: an opener that answers `{"state": "running", "step": "Embedding"}` for
      exactly 3 polls and then raises `URLError` on every later call must raise
      `TimeoutError`, and the fake clock at the raise must read later than one budget from
      the start — proving the successful polls pushed the deadline out rather than being
      ignored. Watch it fail on the pre-1.1 code if 1.1 were reverted; on the current tree
      it may already pass, in which case keep it as the regression guard for the reset and
      say so in the test docstring. Also assert an unrecognised payload counts as an
      answer: an opener returning `{"step": "warming up"}` with no `state` key, for one
      round longer than a budget, must not raise. Gate green; commit.

## 2. Stop blaming a URL that was not in use

- [ ] 2.1 `model: sonnet` — RED test: an opener that raises
      `urllib.error.URLError("[Errno 111] Connection refused")` for any URL containing
      `data-manager` and answers `{"state": "running", "step": "Updating vectorstore"}` for
      any other URL — the exact `--hostmode` shape from the incident — then stops answering
      entirely after 20 calls, must raise `TimeoutError` whose message contains **neither**
      `Connection refused` from that first candidate **nor** the literal `Last error` for
      it. Watch it fail: today the stale exception from the fallen-past candidate is
      rendered.
      Implement: set `last_error = None` on the non-terminal success path, immediately
      before the `break` (beside the `last_progress_time` assignment from 1.1). Keep the
      existing per-round reset at `src/bin/service_benchmark.py:2120`. Gate green; commit.
- [ ] 2.2 `model: sonnet` — Guard the other half: an opener that raises `URLError("gone")` for **every**
      candidate URL for one whole budget must raise `TimeoutError` whose message does
      contain `gone`. A fix that simply deleted the error from the message would pass 2.1
      and must fail here. Gate green; commit.

## 3. Say what the wait was watching

- [ ] 3.1 `model: sonnet` — RED test, one opener, three assertions: answer
      `{"state": "running", "step": "Updating vectorstore"}` for 5 polls, then raise
      `URLError` forever; the resulting `TimeoutError` message must contain `running`,
      contain `Updating vectorstore`, and report both the idle interval and the total
      elapsed wait as two different numbers. Watch it fail — today the message contains
      only the fixed budget.
      Implement: track `last_state` and `last_step` beside `last_progress_time`, and
      replace the two `raise TimeoutError` sites
      (`src/bin/service_benchmark.py:2156-2163`) with one that builds the message from
      what is known: the idle interval and total elapsed formatted `:.0f`; then either the
      last observed `state`/`step` or an explicit statement that no status response was
      received; then the last poll error appended only when `last_error` is set. Gate
      green; commit.
- [ ] 3.2 `model: sonnet` — RED-or-guard test: an opener that raises `URLError` from the very first call, for
      one whole budget, produces a `TimeoutError` whose message states that no status
      response was received and does **not** render an empty `state=` or `step=` value
      (assert `state=None` and `step=None` are absent). Gate green; commit.

## 4. Documentation and close-out

- [ ] 4.1 `model: sonnet` — Rewrite the `BENCH_INGEST_WAIT_TIMEOUT` row at `docs/docs/benchmarking.md:145`.
      **Do not add the variables — they are already there with their defaults**, which
      makes issue #378's plan item 4 stale. Change the description only: it must say the
      value is the time allowed with **no successful status response** (a stall budget),
      not the total time allowed for the ingest, and it must keep the default `7200`. Add
      one sentence recording that a slow ingest reporting `state=running` is no longer
      aborted. Leave the `BENCH_INGEST_POLL_INTERVAL` row alone; it is already correct.
      This is a docs-only turn — the gate reports "no lines with coverage information" for
      it, which is expected. Gate green; commit.
- [ ] 4.2 `model: haiku` — Close out. Run `bash scripts/gate.sh` once more on the finished change from the
      repository root and confirm it exits 0. Confirm `git status --porcelain` is empty
      after the last commit. Push with
      `git push -u origin fix/issue-378-bench-ingest-stall-timeout` — the branch tracks
      `origin/dev`, so `-u` is required or the push retargets the trunk. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, and put `closes #378` in the **body**
      (a closing keyword in the title does not link the issue). Then verify two things
      before declaring done: the URL `gh pr create` printed contains `fasrc/archi` and not
      a fork, and `gh pr view <n> --repo fasrc/archi --json closingIssuesReferences` lists
      #378. Stop there. Do not merge.
