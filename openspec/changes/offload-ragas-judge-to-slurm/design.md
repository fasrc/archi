## Context

Anchors are at origin/dev `e58a7ada`.

- `archi evaluate` starts postgres, data-manager and benchmark on the dev host (`src/cli/cli_main.py:776`, `:855`). The benchmark imports the archi pipeline in-process.
- `Benchmarker._process_config` (`src/bin/service_benchmark.py:2022`) has two steps:
  1. It answers each question, and collects one ragas record per scorable row (`:2213-2221`): `{user_input, retrieved_contexts, response, reference}`, with full contexts.
  2. It calls `get_ragas_results(rows, keys, results_by_key)` (`:1927`, called at `:2089`). That call writes per-row scores onto the question entries and returns the `aggregate_<metric>` and `<metric>_scored` values.
- The leaderboard and the A/B comparisons are built from each arm's `total_results` when the result JSON is written (`build_leaderboard` `:998`, reads at `:1063`; `dump` `:756`).
- The result JSON keeps only `page_content[:300]` for each source (`:2200`).
- The judge comes from `get_ragas_llm_evaluator` (`:1612`). Its `huggingface` case (`:1688`) builds an OpenAI-compatible client for any `/v1` URL. `LocalProvider` sends a configured API key, or `"not-needed"` (`src/archi/providers/local_provider.py:174`, key loading in `src/archi/providers/base.py:92-97`).
- The judge today is `huit_bedrock`, `us.anthropic.claude-sonnet-4-5-20250929-v1:0`, with 5 metrics, `timeout: 600` and `max_workers: 6`. It is set in `fasrc/archi-config` `benchmarking/fasrc_ragas_queries.yaml`.
- Dev host: `holygpu7c0717`, 4 × V100 32 GB. All four GPUs serve the Qwen models under test on ports 8001 and 8002 (`docs/docs/fasrc_archi.md:8-16`). The host is on the cluster network and is not a Slurm node.
- The operator confirmed these facts on 2026-09-29:
  - `$HOME` and FASRC scratch are shared between login and compute nodes.
  - Compute nodes can reach the internet.
  - A person or a scheduled job on the cluster submits jobs.
  - Only runs from now on are scored offline.
- FASRC gives `scrontab` (cron entries stored in Slurm), `gpu_h200` (4 × H200 per node, 3 days), `gpu_requeue` (GPU jobs of less than 1 day that can be killed and requeued), and `--dependency=afterok` (https://docs.rc.fas.harvard.edu/kb/running-jobs/).

## Goals / Non-Goals

**Goals:**
- Remove the Bedrock judge bill from golden-set runs, and use a pinned model with open weights on the cluster.
- No GPU sits idle while a run ingests or answers.
- The design tolerates an unknown queue wait. Bundles that arrive during the wait join the same job.
- Offline scores come from the same RAGAS code as inline scores.
- A judge change is always visible in comparisons, and it never mixes silently with old scores.

**Non-Goals:**
- Scoring old runs again. Their result JSON has no full contexts, and the operator accepted this.
- Choosing the judge model for good. Llama-3.3-70B-Instruct is the first candidate, and a comparison against Sonnet decides (see Open Questions).
- The Lynx metric (fasrc/archi#583). It reuses the bundle, but it is not built here.
- Removing the inline Bedrock path. It stays as the default and as a spot check.
- Moving the model under test, ingest, or the archi stack onto Slurm.

## Decisions

### D1. Split the run at the existing scoring call, not at the result JSON

The bundle is the `rows` and `keys` that `_process_config` already passes to `get_ragas_results`. The deferred path writes them to disk in place of the call.

- Alternative: score from the result JSON. Refused, because the JSON cuts contexts to 300 characters, and it would couple the scorer to the report format.
- Alternative: a live judge server that the run calls over HTTP. Refused, because the GPU would be allocated through the ingest and 1.25 hours of answers, and the queue wait would block the run.

### D2. Extract the scoring into one module function

`get_ragas_results`, `get_ragas_llm_evaluator` and the `RunConfig` assembly move into a module, for example `src/utils/ragas_scoring.py`. The module takes rows, keys, settings and a judge factory. `Benchmarker` keeps thin wrappers. The offline scorer imports the same function. This is how the requirement "same rows and same judge give the same aggregates" is tested, with a stub judge.

The extraction is a separate, mechanical PR with no change in behavior. `service_benchmark.py` is large, so run the black-seam check before the edit.

### D3. Bundle layout and state markers on shared storage

```
<queue>/<run_id>-arm<N>/
  manifest.json        judge settings, judge model id + revision, code_version,
                       result file name, arm index, rows sha256
  rows.jsonl           {key, user_input, retrieved_contexts, response, reference}
  READY                written last by publish
  claim.d/             mkdir = atomic claim; claim.d/job holds the job id
  scores.partial.jsonl append-only {key, metric, value, error}
  judge_scores.json    final: per-key scores, aggregates, NaN counts, judge_identity
  SCORED | FAILED      terminal marker (FAILED holds the reason)
  MERGED               written by the merge job
```

- Markers are separate empty files, not one state field that is rewritten, so that each change of state is one atomic create.
- `mkdir` is atomic on network storage. `flock` is not reliable there.
- The benchmark container writes the bundle under the run's `out_dir`, which is already bind-mounted. A host-side publish step (`scripts/benchmarking/judge/publish.py`, called by `run_goldenset_eval.sh` after the container exits) copies it to `<queue>` and checks the digests before it writes `READY`. This way the compose templates get no new mount.

### D4. Submitter: one job, via scrontab

`scripts/benchmarking/slurm/judge_submit.sh` does three things:
1. It counts READY folders that have no `claim.d`.
2. It runs `squeue -h -u "$USER" -n archi-judge`.
3. It runs `sbatch` only if the count is 1 or more and no job exists.

`scrontab` runs it every 15 minutes, and a person can also run it by hand. Slurm stores `scrontab` entries, so they do not depend on one login node, while a normal crontab lives on one login node of the pool.

### D5. Claim at start, one judge per job, time budget

- At start, the job loads the model that its sbatch script names. That is one model and one revision for each job.
- It lists the READY folders in `<queue>`, oldest first. It claims each bundle whose manifest matches the loaded judge.
- Before each claim, it checks the budget: time left (from `squeue -h -j $SLURM_JOB_ID -o %L`) against the rows-per-minute rate it has measured so far. The first bundle is always claimed.
- Bundles that are not claimed stay READY for the next job. The submitter's next tick submits that job.
- All arms of a campaign that one job scores therefore share the same weights, precision and server settings.

### D6. Judge server on the node's loopback interface, with a per-job API key

The job starts vLLM from an Apptainer image of the upstream `vllm/vllm-openai` image at a pinned tag (not the V100 build `vllm_volta.sif`). The server listens on `127.0.0.1` at a free port, with `--api-key` set to a random token for the job. Other users on a shared node can reach `127.0.0.1` too, so the key is necessary.

The scorer builds the judge with `evaluator_provider: huggingface`, `evaluator_ollama_url: http://127.0.0.1:<port>/v1` and the token. A unit test confirms that the token reaches the request. The job waits until `GET /v1/models` returns HTTP 200 before it claims anything, and it stops the server on exit, through a `trap`.

### D7. Save each row's score and resume

`get_ragas_results` scores each metric over its eligible rows in one ragas call. The offline scorer calls the shared function over **chunks** of rows (the default is 8), and it appends each chunk's results to `scores.partial.jsonl` with `fsync`. When it starts, it reads that file and skips the `(key, metric)` pairs that are already recorded. The final aggregates are computed from the complete per-row set with the same aggregation code (D2). The sbatch script uses `--requeue --open-mode=append`, so this also works on `gpu_requeue`.

### D8. The merge writes a new judged file

`scripts/benchmarking/judge/merge.py` runs as a CPU job with `--dependency=afterok:<judge job>`. It also runs by hand. For each SCORED bundle, it:
1. reads the pending result JSON (the file name is in the manifest);
2. checks the bundle digest against the digest recorded in the pending arm;
3. writes the per-row scores and the `total_results` aggregates;
4. rebuilds the leaderboard and the A/B comparisons with the existing `ResultHandler` code;
5. writes `<name>.judged.json`, with `judge_status: scored` and `judge_identity`.

It does not change the pending file. Output is canonical JSON (sorted keys, `allow_nan=False`, the same writer as `dump`), so a second run gives identical bytes. A person, or a later dev-host timer, copies the judged files into `bench_out/` and commits them.

### D9. Two new gates in compare_runs.py

- **G8, judged.** Refuse any arm with `judge_status: pending`.
- **G9, one judge.** Refuse a RAGAS comparison when the arms' judge identities differ. For an inline arm, the identity is `(evaluator_provider, evaluator_model)`. For an offline arm, it is `(model id, revision)`.

There is no override flag, because the result of a mixed comparison has no meaning.

## Risks / Trade-offs

- [The new judge disagrees with Sonnet, or gives more NaN rows] → Before the switch, score one fm-00 bundle with each candidate and with inline Sonnet. Compare row-level rank agreement and NaN counts for each metric. Choose with those numbers. G9 prevents a silent mix.
- [vLLM output at temperature 0 changes a little with the other requests in the same batch] → Record the job id and the list of claimed bundles in each `judge_identity`. The effect is expected to be far below run-to-run noise, and the record makes it possible to check later.
- [Queue wait on `gpu_h200` is long] → Claim-at-start makes a long wait add more bundles to the job. The operator can switch the sbatch partition to `gpu_requeue` (D7 makes that safe) or to 1 × H200 with FP8. Precision is part of `judge_identity`.
- [A lost scrontab entry or a crashed submitter leaves bundles READY with no job] → The submitter logs each tick. A READY bundle older than 24 hours is reported by `judge_submit.sh --status`.
- [Many parallel ragas requests over a small chunk give less throughput] → `max_workers` and the chunk size are manifest settings. Measure rows per minute in the first job and adjust.
- [The scorer's Python environment differs from the benchmark image, so the ragas or langchain versions drift] → Run the scorer in an Apptainer image built from the benchmark image, and record its digest in `judge_identity`. The manifest records `code_version`, and the scorer refuses to run when its code version differs (an operator override is logged).
- [`service_benchmark.py` is large and black-sensitive] → D2 extraction goes first, as its own mechanical PR, and new logic lives in the new module.

## Migration Plan

1. PR 1: extract the scoring (D2), with no change in behavior. The existing tests pass unchanged.
2. PR 2: `judge_mode: deferred`, the bundle writer, the publish step, and `judge_status`. Default `inline`. A deferred run can be made and published, but nothing scores it yet.
3. PR 3: the offline scorer (claim, budget, checkpoints, stub-judge tests) and the merge.
4. PR 4: the Slurm scripts (submitter, judge job, merge job) with `--dry-run`, the G8 and G9 gates, and docs in `docs/docs/benchmarking.md`.
5. Operator: pull the vLLM image and the judge weights to shared storage, install the `scrontab` entry, and run the judge comparison.
6. Switch the golden-set config in `fasrc/archi-config` to `judge_mode: deferred`.

Rollback: set `judge_mode: inline` (or remove the key). Pending bundles can stay in the queue, and nothing else depends on them.

## Open Questions

- **Judge model.** Llama-3.3-70B-Instruct first, and DeepSeek-R1-Distill-Llama-70B as a second candidate. Qwen models are excluded because Qwen is the agent model, and DeepSeek-R1-Distill-Qwen-32B is built on Qwen2.5. The comparison in Risks decides.
- **Partition and precision.** `gpu_h200` with 2 × H200 in bf16, or 1 × H200 in FP8, or `gpu_requeue`. This depends on the lab's access and on the measured queue waits.
- **Can the dev host run `sbatch`?** If it cannot, the chain is: the publish step writes to shared home, and `scrontab` submits. Nothing needs the dev host to talk to Slurm.
- **Queue location.** `$HOME/archi-judge/queue` or a lab scratch folder. `$HOME` has a quota. Bundles are small (the text of about 109 rows), but the vLLM image and the weights (about 140 GB) must be on lab or netscratch storage.
