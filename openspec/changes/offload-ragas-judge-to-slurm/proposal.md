## Why

Every golden-set RAGAS run sends its judge calls to Claude Sonnet 4.5 through the HUIT Bedrock gateway. That traffic bills the team's cost center. GPU time on the FASRC cluster costs the team almost nothing. The Bedrock judge also throttles: on 2026-09-20, judge timeouts lost 62 of 109 rows in one run. A judge with open weights, run in a Slurm GPU job, removes the bill and the throttle, and its weights can be pinned by revision.

The dev host (`holygpu7c0717`) cannot host the judge, because the two Qwen servers use all four of its V100 GPUs. A Slurm GPU job must also not sit idle while the run ingests and answers, which takes 1.5 to 3.5 hours. Queue wait is unknown. For these reasons, the scoring step must be a job that is submitted separately and has all its inputs.

## What Changes

- A benchmark run can **defer** its RAGAS judge. When `ragas_settings.judge_mode: deferred` is set, the run answers every question as it does today. Then it writes a **judge bundle**: the full per-row RAGAS inputs and a manifest. It does not call a judge. The default stays `inline`, and inline runs do not change.
- The result JSON of a deferred run records `judge_status: pending`, and it has no RAGAS aggregates.
- A publish step, run by a timer on the dev host and not by the process that started the run, moves each complete bundle into a queue folder on shared storage, and it writes a `READY` marker last.
- A **submitter**, run from `scrontab` or by a person, submits one `archi-judge` Slurm GPU job, but only if no such job is pending or running.
- The **judge job** starts a vLLM server with the judge model, on a Unix socket that only our user can open, with a per-job API key from the environment. When it starts, it **claims every READY bundle** that matches its judge model and its scorer version. A bundle that matches no available scorer waits, and it is never marked failed. It scores each bundle with the same RAGAS code that the inline path uses. It saves each row's scores as it goes, so a requeued job (`gpu_requeue`) continues and does not start again.
- A **merge job** (`--dependency=afterok`, CPU only) waits until every arm of a result is scored, and then folds the scores into one judged copy of that result JSON: the per-row metric fields, `total_results` aggregates, leaderboard, and A/B comparisons. The copy has `judge_status: scored` and the judge's identity.
- `compare_runs.py` gets two new gates. It refuses a run with `judge_status: pending`, and it refuses to compare arms that different judges scored.
- The RAGAS scoring in `Benchmarker.get_ragas_results` moves into a module function. The inline path and the offline path then run the same code.
- The judge bundle is also the per-row sidecar file that fasrc/archi#583 (Lynx faithfulness metric) needs. This change supplies that file, and #583 reuses it.

## Capabilities

### New Capabilities

- `offline-judge-scoring`: deferred RAGAS judging of a benchmark run. It covers the judge bundle format and publish step, the queue folder's state markers, the submitter's one-job rule, claim-at-start and time-budget batching in the judge job, per-row checkpoints and resume, the merge into a judged result JSON, and the comparison gates for `judge_status` and judge identity.

### Modified Capabilities

(none. The inline judge path keeps its current requirements, and the new comparison gates are added under `offline-judge-scoring`.)

## Impact

- **Code:**
  - `src/bin/service_benchmark.py`: a thin call site in `_process_config` for the deferred path, and the scoring extracted into a module function.
  - A new helper package for the bundle, queue, scorer and merge, with tests that need no GPU.
  - `scripts/benchmarking/compare_runs.py`: two new gates.
  - `src/cli/templates/base-config.yaml`: render `judge_mode`.
  - New Slurm scripts under `scripts/benchmarking/slurm/`.
- **Config:** new optional `ragas_settings` keys `judge_mode` (default `inline`) and `judge_bundle_dir`. Nothing changes for existing configs.
- **Artifacts:** additive only. Old result JSONs have no `judge_status` key, and they load as `inline`.
- **Cluster:** needs `scrontab` and GPU partition access (`gpu_h200` for a 70B judge, or `gpu_requeue`). It also needs one download of the judge weights to shared storage, and a vLLM Apptainer image built for the target GPUs. None of this runs on the dev host's GPUs.
- **Cost:** after the switch, the only Sonnet cost is an optional periodic spot check. fasrc/archi#582 (judge token usage) measures that cost.
- **Comparability:** scores from the new judge are not comparable with Sonnet scores. The judge-identity gate makes that visible. The first judged runs start a new baseline.
