# Time the benchmark's ingestion wait against stalls, not total runtime

## Why

`wait_for_ingestion_completion` (`src/bin/service_benchmark.py:2092`) blocks the whole
benchmark until the data-manager reports ingestion complete. Its one production caller is
`Benchmarker.run` (`src/bin/service_benchmark.py:1805`), the first statement in the run.
The method polls four candidate status URLs (`src/bin/service_benchmark.py:2107-2112`)
every `BENCH_INGEST_POLL_INTERVAL` seconds until `BENCH_INGEST_WAIT_TIMEOUT` seconds
elapse (default `7200`, `src/bin/service_benchmark.py:2093`).

Two defects meet in that loop, and together they cost a night of GPU time and then point
the operator at the wrong cause.

**The deadline is absolute and never resets on healthy progress.** `start_time` is taken
once before the loop (`src/bin/service_benchmark.py:2112`) and `elapsed` is measured
against it (`src/bin/service_benchmark.py:2155`). Nothing extends the deadline when the
endpoint answers successfully with `state=running` and a changing `step`. A legitimately
long ingest is killed at exactly two hours, and every file already embedded is discarded.

Measured on this host on 2026-08-27 (deployment `ragas-0827`, config
`config/benchmarking/fasrc_ragas_queries.yaml`): the benchmark container started
`19:42:58Z` and raised `TimeoutError` at `21:43:03Z` — exactly 7200s. Its own final log
line was a *successful* poll: `Ingestion status check #1433 via
http://localhost:7881/api/ingestion/status -> state=running step=Updating vectorstore`.
All 1433 checks succeeded and none reported `state=error`. The ingest finished two minutes
later at `21:45:29Z`. The ingest was slow because of load, not a fault: embedding ran at a
measured 6.0–6.7 s/file over 1079 files (~106 min) against ~3.2 s/file on an idle host,
because that config sets `processing.categorization.enabled: true` and so makes one LLM
call per document before embedding starts (~45 calls/min, ~24 min). Recovery needed no
re-ingest — relaunching only the `benchmark` compose service found ingestion complete on
its first status check.

**The reported error names a URL that was not in use.** `last_error` is reset once per
outer iteration (`src/bin/service_benchmark.py:2120`), assigned inside the per-URL
`except` (`src/bin/service_benchmark.py:2151`), and never cleared when a later URL
succeeds — the success path `break`s out of the `for` with the earlier failure still held.
Under `--hostmode` the first candidate `http://data-manager:<internal>/api/ingestion/status`
is unreachable and the second (`http://localhost:<internal>`) succeeds, so every iteration
leaves a stale exception behind. At timeout, `src/bin/service_benchmark.py:2158` renders
`Timed out after 7200s waiting for ingestion status endpoint. Last error: <urlopen error
[Errno 111] Connection refused>`. The refusal is real and irrelevant: it comes from a
candidate the code already fell past. An operator reading it goes hunting for a network
fault between benchmark and data-manager that does not exist.

## What Changes

- The budget becomes a **stall** timeout. The method tracks the time of the last
  successful, non-terminal status response and aborts only when that idle interval reaches
  the budget. An ingest reporting `state=running` can no longer be killed for being slow.
  The default value of `BENCH_INGEST_WAIT_TIMEOUT` is unchanged — a bigger constant only
  moves the wall.
- Fast failure is kept. An endpoint that no candidate URL can reach still raises
  `TimeoutError` one budget after the last successful poll (one budget from the start when
  none ever succeeded), and `state=error` still raises `RuntimeError` naming the step. The
  only case that stops aborting is the one that was never a failure.
- `last_error` is cleared on a successful poll, so the timeout message can quote an
  exception only when nothing answered in the final round.
- The timeout message names what the wait was watching: the last observed `state` and
  `step`, the idle interval, and the total elapsed wait. An operator can then tell "the
  ingest was still working" from "the endpoint was down" without reading 1400 log lines.
- `docs/docs/benchmarking.md:145` is corrected. **The issue's plan item 4 is stale**: both
  `BENCH_INGEST_WAIT_TIMEOUT` and `BENCH_INGEST_POLL_INTERVAL` are already documented with
  their defaults at `docs/docs/benchmarking.md:145-146`, so acceptance criterion 6 already
  holds on `c60e6a69`. What is wrong there is the description — "Seconds the benchmark
  container waits for the data-manager's ingestion to complete before giving up" states
  exactly the total-runtime reading this change removes. The row is rewritten to describe
  the stall budget, per `AGENTS.md:53-55`.
- New `tests/unit/test_benchmark_ingest_wait.py` covers all of the above with a fake clock
  and a fake opener. No live data-manager, no network, and no test that sleeps for a real
  budget.

Note for the reader of the issue: it names the class `BenchmarkService`. There is no such
class. The method lives on `Benchmarker` (`src/bin/service_benchmark.py:992`); every
file:line anchor in the issue is correct.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `retrieval-benchmarking`: three requirements are added to the existing capability
  (`openspec/specs/retrieval-benchmarking/spec.md`) covering when the benchmark abandons a
  wait on ingestion and what it reports when it does. None of the capability's four
  existing requirements mentions the ingestion wait, so this is an addition to it, not a
  modification of one.

## Impact

- `src/bin/service_benchmark.py` — `wait_for_ingestion_completion` only. No other method
  and no call site changes; `Benchmarker.run` still calls it with no arguments.
- `tests/unit/test_benchmark_ingest_wait.py` — new file.
- `docs/docs/benchmarking.md` — one table row rewritten.
- Coverage: `src/bin/service_benchmark.py` is already imported by 12 unit-test files and
  the file is black-clean, so an in-place edit reports normally to `diff-cover` and does
  not trip a reformat of untouched lines.
- Accepted risk, stated plainly: a data-manager that keeps answering `state=running`
  while making no real progress will now be waited on forever. That is the direct cost of
  acceptance criterion 1, which requires an indefinitely-`running` endpoint not to raise.
  Detecting a wedged-but-answering ingest needs a progress signal the status payload does
  not carry today, so it is out of scope here and belongs in a follow-up.
