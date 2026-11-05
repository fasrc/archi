## ADDED Requirements

### Requirement: The judge-job unit tests wait for the fake server's side effects
The fake `curl` in `tests/unit/test_judge_slurm.py` SHALL NOT answer a readiness probe until the fake `vllm serve` has finished all its side effects, so that the judge-job tests give the same result under any host load.
The fake `vllm serve` writes the marker file `vllm.ready` as its last side effect. The fake `curl` waits for that marker for at most 1000 steps of 0.01 s before it records the call and answers, and after that bound it continues as before. The wait adds no probe, so the recorded probe count does not change. `scripts/benchmarking/slurm/archi_judge.sbatch` does not change.

#### Scenario: A slow fake server still edits judge.env before the job ends
- **GIVEN** the fake `vllm serve` sleeps 3 s before it edits `judge.env`, and before it replaces the scorer image
- **WHEN** `tests/unit/test_judge_slurm.py` runs
- **THEN** `test_a_judge_env_change_during_the_job_does_not_reach_the_scorer` and `test_a_rebuild_during_the_job_keeps_the_pinned_pair` pass, and the file prints `56 passed`
- **AND** the same delay on the `origin/dev` test file gives `2 failed, 54 passed`

#### Scenario: A slow fake server start still records its socket folder and pid
- **GIVEN** the fake `vllm serve` sleeps 3 s before its first side effect
- **WHEN** `tests/unit/test_judge_slurm.py` runs
- **THEN** the file prints `56 passed`
- **AND** the same delay on the `origin/dev` test file gives `4 failed, 52 passed`, with a `FileNotFoundError` on `sockdir.mode` or `vllm.pid` in two of the failures

#### Scenario: Removing the marker fails a named test
- **WHEN** a throwaway copy removes the line that writes `vllm.ready` from the fake `vllm serve`
- **THEN** `test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm` fails on its `vllm.ready` assertion

#### Scenario: The probe count does not change
- **WHEN** `test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm` runs with `curl.ready_after` set to `2`
- **THEN** the fake `curl` records exactly 3 calls, as before the change
