## ADDED Requirements

### Requirement: The judge-job unit tests wait for the fake server's side effects
The fake `curl` in `tests/unit/test_judge_slurm.py` SHALL NOT answer a readiness probe until the fake `vllm serve` has finished all its side effects, so that the judge-job tests give the same result under any host load.
The fake `vllm serve` writes the marker file `vllm.ready` as its last side effect. The fake `curl` waits for that marker before it records the call and answers, for at most (`_RUN_TIMEOUT` + 1) × 100 steps of 0.01 s: longer than the 60 s that `_run` gives a job, so no probe answers before the marker while a test runs. If the marker is still absent after that bound, the fake `curl` fails as `curl` does with nothing on the socket: it prints `000`, exits 7 and records no call, so the job probes again and never sees `200` before the marker. The wait adds no probe, so the recorded probe count does not change. `scripts/benchmarking/slurm/archi_judge.sbatch` does not change.

#### Scenario: A slow fake server still edits judge.env before the job ends
- **GIVEN** the fake `vllm serve` sleeps 3 s before it edits `judge.env`, and before it replaces the scorer image
- **WHEN** `tests/unit/test_judge_slurm.py` runs
- **THEN** `test_a_judge_env_change_during_the_job_does_not_reach_the_scorer` and `test_a_rebuild_during_the_job_keeps_the_pinned_pair` pass, and the file prints `58 passed`
- **AND** the same delay on the `origin/dev` test file gives `2 failed, 54 passed`

#### Scenario: A slow fake server start still records its socket folder and pid
- **GIVEN** the fake `vllm serve` sleeps 3 s before its first side effect
- **WHEN** `tests/unit/test_judge_slurm.py` runs
- **THEN** the file prints `58 passed`
- **AND** the same delay on the `origin/dev` test file gives `4 failed, 52 passed`, with a `FileNotFoundError` on `sockdir.mode` or `vllm.pid` in two of the failures

#### Scenario: A fake server slower than the probe wait is not reported ready
- **GIVEN** `curl.wait_ticks` is `1`, and the fake `vllm serve` sleeps 0.5 s (`serve.delay`) before it edits `judge.env`
- **WHEN** `test_a_server_slower_than_the_probe_wait_is_not_ready_before_its_edits` runs
- **THEN** the job exits 0, `judge.env` holds `JUDGE_PRECISION=fp8`, and the scorer's copy holds `JUDGE_PRECISION=bf16`
- **AND** with the line that prints `000` removed, the test fails on its `JUDGE_PRECISION=fp8` assertion

#### Scenario: A server stub that starts late is stopped after it records its pid
- **GIVEN** the fake `vllm serve` sleeps 11 s before its first side effect, longer than a 1000-step probe wait
- **WHEN** `test_a_server_that_never_gets_ready_fails_the_job_and_is_stopped` runs with `JUDGE_READY_TIMEOUT=0`
- **THEN** it passes: the first probe waits for the marker, so the job stops the server only after the stub wrote `vllm.pid`
- **AND** with a 1000-step wait it fails with `FileNotFoundError` on `vllm.pid`, and `test_the_probe_wait_outlasts_the_run_timeout` fails with `assert (1000 * 0.01) > 60`

#### Scenario: Removing the marker fails a named test
- **WHEN** a throwaway copy removes the line that writes `vllm.ready` from the fake `vllm serve`
- **THEN** `test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm` fails: no probe answers `200`, and `_run` stops the job after 60 s with `subprocess.TimeoutExpired`

#### Scenario: The probe count does not change
- **WHEN** `test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm` runs with `curl.ready_after` set to `2`
- **THEN** the fake `curl` records exactly 3 calls, as before the change
