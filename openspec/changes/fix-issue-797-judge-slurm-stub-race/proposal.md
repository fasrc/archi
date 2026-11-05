## Why

`tests/unit/test_judge_slurm.py` (added in `91aba782`, #758) has a timing race. The judge job
`scripts/benchmarking/slurm/archi_judge.sbatch` starts the server in the background
(`:342-343`, `"${SERVE[@]}" &`) and then polls readiness with `curl` (`:345-355`). In the tests,
the `apptainer` stub branch `*"vllm serve"*` (`test_judge_slurm.py:101-117` on `origin/dev`
`5f3d80e9`) does its side effects and then runs `exec sleep 300`. The `curl` stub (`_CURL`,
`:127-132`) answers `200` on the first call. Nothing makes the `curl` stub wait for the
`vllm serve` stub. Under load, the job can probe, score, merge and stop the server before the
stub does its side effects.

The race fails real gate runs. On 2026-11-02 and 2026-11-03 the host hook gate failed on
`test_a_judge_env_change_during_the_job_does_not_reach_the_scorer` and
`test_a_rebuild_during_the_job_keeps_the_pinned_pair` up to 3 times in a row, and the
`rebuild` test also failed inside the loop container (#699, turns 753-754).

Loop 1 measured the race on `origin/dev` `5f3d80e9` in a copy under `/tmp`:

- With a temporary `sleep 3` before the `sed` (`:108`) and before the `printf` (`:112`):
  `2 failed, 54 passed` (the `edit-env` and `rebuild` tests).
- With a temporary `sleep 3` at the start of the `vllm serve` branch (before `prev=""`,
  `:102`): `4 failed, 52 passed`. The 2 more failures are the `sockdir.mode` and `vllm.pid`
  reads in `test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm` and
  `test_a_server_that_never_gets_ready_fails_the_job_and_is_stopped` (`FileNotFoundError`).

## What Changes

- The `vllm serve` stub writes a marker, `$STUB/vllm.ready`, as its last step before
  `exec sleep 300`.
- The `curl` stub waits for that marker before it does anything else, at most 1000 steps of
  `sleep 0.01`. If the marker is still absent after the bound, it prints `000` and exits 7
  without a record, as `curl` does with nothing on the socket, so the job probes again and
  never sees `200` before the marker (review round 1, 2026-11-05; design D3). A test whose
  server never writes the marker does not hang: it fails on the 60 s `_run` timeout. The wait
  comes before the `curl.count` update, so the count does not change.
- `test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm` gets one more
  assertion: `(stub / "vllm.ready").exists()`.
- A new test, `test_a_server_slower_than_the_probe_wait_is_not_ready_before_its_edits`, makes
  the server stub slower than the probe wait (`curl.wait_ticks`, `serve.delay`) and checks
  that the job still sees the server's `judge.env` edit.

All existing assertions stay, including `int((stub / "curl.count").read_text()) == 3`.
`scripts/benchmarking/slurm/archi_judge.sbatch` does not change: the job is correct, the test
stubs have the race.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `offline-judge-scoring`: adds a requirement that the judge-job unit tests wait for the
  fake server's side effects before the fake readiness probe answers.

## Impact

- Tests only: `tests/unit/test_judge_slurm.py`. A tests-only diff scores no patch-coverage
  lines.
- The file's run time does not change measurably (`56 passed`, about 4.5 s, 20 of 20 runs
  on the host with swap full; `57 passed` in about 5 s after review round 1 adds a test).
- The host gate stops failing on the two flaky tests. Until this merges, every commit pays
  up to about 16 min of gate retries.
- Closes #797.
