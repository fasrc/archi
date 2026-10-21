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
<queue>/<result basename>-arm<N>/
  manifest.json        judge settings, the resolved ragas call settings (D10),
                       judge model id + revision, embedding setting,
                       code digest (`code_version.digest`), package digest of the
                       run's environment (D10),
                       result basename, arm index, arm count of the result,
                       rows sha256
  rows.jsonl           {key, user_input, retrieved_contexts, response, reference}
  READY                written last by publish
  claim.<gen>          a file that holds the Slurm job id; gen = 1, 2, 3 ...
                       (a requeued job keeps its id, so it still owns the claim)
  scores.<identity digest>.partial.jsonl
                       append-only {key, metric, value, error}; one file for each
                       judge_identity (D7)
  INCOMPATIBLE.<sidecar digest>
                       not terminal: a job with that scorer sidecar found a live
                       mismatch (D10)
  judge_scores.json    final: per-key scores, aggregates, NaN counts, judge_identity
  SCORED | FAILED      terminal marker (FAILED holds the reason)
  MERGED               written by the merge job
<queue>/results/
  <result basename>.json         the pending result JSON, copied by publish
  <result basename>.judged.json  written by the merge (D8)
<queue>/SCORER_STALE.<sidecar digest>
                       not terminal: a job found that its scorer sidecar does not
                       describe its live environment (D10)
```

- **The result basename is fixed when the run starts, not when it ends.** Today `ResultHandler.dump_artifacts` picks the timestamp only when it writes the result (`src/bin/service_benchmark.py:692`), and `dump` builds the file name from it (`:756-758`), after every arm has finished. A bundle that is written at the scoring call cannot know that name. The change therefore picks `<benchmark_name>-<UTC timestamp>` once, before the first arm, and passes it to both the bundle writer and `dump_artifacts`. The JSON and its `_report.md` still share one timestamp, so the backfill script's invariant holds.

- Markers are separate empty files, not one state field that is rewritten, so that each change of state is one atomic create.
- **One primitive for every exclusive create: `link(2)`.** The writer writes a unique temporary file (`.tmp.<host>.<pid>.<random>`), calls `fsync`, and then calls `link(tmp, name)`. `link` is atomic on NFS, and it fails with `EEXIST` if `name` exists, so exactly one writer wins and a loser never replaces the winner. The file has its full content at the moment its name appears. The writer then removes its temporary file. NFS can report `EEXIST` for a `link` that succeeded, when it resends the request. A writer that gets `EEXIST` therefore checks the link count of its temporary file: a count of 2 means that its own `link` succeeded. Claims (D5, D5a), `READY` (publish), and the judged file (D8) all use this primitive. `rename` is not used for these, because it replaces an existing name. `mkdir` is not used for claims, because a claim folder exists for a time with no owner in it. `flock` is not reliable on network storage.
- The benchmark container writes the bundle under the run's `out_dir`, which is already bind-mounted, and it writes a local `BUNDLE_COMPLETE` marker last. The compose templates therefore get no new mount.
- **Publishing does not depend on the process that started the run.** `run_goldenset_eval.sh` returns at once with `--no-follow`, and Ctrl+C stops it while the container continues (`scripts/benchmarking/run_goldenset_eval.sh:72-76`). The wrapper therefore does not publish. A systemd user timer on the dev host, `archi-judge-publish.timer` (every 5 minutes, with its units in `fasrc/archi-config` beside the other host timers), runs `scripts/benchmarking/judge/publish.py` over the benchmark output folder. Each run of the publish step does three things, and each one is idempotent:
1. For each bundle with `BUNDLE_COMPLETE`, it copies the bundle to `<queue>`, checks the digests, and then creates `READY` with `link`. A queue folder that already has `READY` and the same digest is skipped. A folder with a different digest makes it exit non-zero and report the conflict.
2. When the pending result JSON of a published bundle is **complete**, it copies that JSON to `<queue>/results/` with `link`. The merge reads it only from there (D8). The existence of the file is not enough: `ResultHandler.dump` opens the final path and writes into it (`src/bin/service_benchmark.py:772-781`), so a timer tick can see a half-written file, and the no-replace `link` would then keep the truncated copy for good. Two signals are required. First, a deferred run writes `<basename>.RESULT_COMPLETE` in the output folder after `dump_artifacts` returns. Second, the publish step loads the JSON, and checks that it has an arm for each bundle of the result, with the same bundle digests. Without both, it copies nothing and tries again on the next tick.
3. When a judged file exists in `<queue>/results/` and the bundles of its result have `MERGED`, it copies the judged file back into the output folder, beside the pending file. The campaign tools then find it where they find other results.

### D4. Submitter: one job, via scrontab

`scripts/benchmarking/slurm/judge_submit.sh` does these things:
1. It counts **available** bundles: READY folders with no terminal marker, with no claim or a stale claim (D5a), and that **match** the judge job it will submit (D4a).
2. It runs `squeue -h -u "$USER" -n archi-judge`.
3. It runs `sbatch` only if the count is 1 or more and no job exists.
4. It **reconciles merges**: for each result whose arms are all `SCORED`, with its pending JSON in `<queue>/results/` and no `MERGED`, it submits `archi-judge-merge` if no job of that name exists. The merge is idempotent (D8), so a duplicate costs one short CPU job.

`scrontab` runs it every 15 minutes, and a person can also run it by hand. Slurm stores `scrontab` entries, so they do not depend on one login node, while a normal crontab lives on one login node of the pool.

**Two submitters at the same time.** A scheduled tick and a manual run can both see work and no job, and both call `sbatch`. The design does not try to prevent this with a lock file. Slurm serializes the jobs itself:
- Every `archi-judge` job is submitted with `--dependency=singleton`, so Slurm starts at most one job of that name and user at a time. A second job stays pending until the first ends.
- The job counts its available bundles (D4a, D5a) **before** it starts vLLM. With none, it exits in seconds and loads no model. A duplicate job therefore finds the work done and costs only a short allocation.

The merge is also submitted from the judge job (`--dependency=afterok`) as the fast path. Step 4 is the durable path: a judge job that is killed after its last `SCORED` marker and before its `sbatch` of the merge leaves no gap, because the next tick submits the merge.

### D4a. One match rule for the submitter and the job

A bundle **matches** a judge job when both of these are true:
- the manifest's judge model id and revision equal the model and revision in the job's judge configuration (`judge.env`, which the sbatch script also reads);
- the manifest's code digest and package digest equal the scorer identity (D10) of the scorer image that the job will run. The scorer image build writes that identity to `scorer-identity.json` beside the image.

The match rule also excludes a bundle while one of these records names the digest of the configured `scorer-identity.json`: `<queue>/SCORER_STALE.<digest>` or the bundle's `INCOMPATIBLE.<digest>` (D10). A new sidecar has a new digest, so a rebuilt scorer makes the bundle available again with no manual cleanup.

The submitter and the judge job call one function for this rule (`scripts/benchmarking/judge/queue.py`, standard library only, so that it runs on a login node). A bundle that does not match is **not** a failure. It stays READY, with no terminal marker, until a job with a matching judge and scorer exists. The submitter does not count it, so a queue that holds only such bundles submits no job. `judge_submit.sh --status` lists each one with the reason: the judge it waits for, or both scorer values that differ.

### D5. Claim at start, one judge per job, time budget

- At start, the job first checks its live scorer identity against its sidecar (D10), and then counts its work: the available bundles (D4a, D5a), **and** the bundles whose current claim holds its own `$SLURM_JOB_ID` and that have no terminal marker. The second group is the work of a requeued job (D5a), and the job resumes it first. With a stale sidecar and no work, it exits before it loads a model (D4). The submitter does not count the second group, because a claim of a job that is in `squeue` is not available to a new job.
- It then loads the model that its sbatch script names. That is one model and one revision for each job.
- It lists the READY folders in `<queue>`, oldest first. It claims each bundle that matches the job (D4a), with no claim or a stale claim, by a `link` of `claim.<gen>` (D3). It checks the match before the claim, so it never claims a bundle that it cannot score.
- Before each claim, it checks the budget: time left (from `squeue -h -j $SLURM_JOB_ID -o %L`) against the bundle's row count divided by the rate. The rate is the rows-per-minute rate that the job has measured so far. Before it has scored a row, it uses `JUDGE_BOOTSTRAP_ROWS_PER_MINUTE` from `judge.env`, a low value that the operator sets from the first measured job (task 5.2). The first bundle is always claimed. The job claims at start only while the bootstrap rate says a bundle fits. Later bundles stay READY, and the job looks again after each bundle, with its measured rate.
- Bundles that are not claimed stay READY for the next job. The submitter's next tick submits that job.
- All arms of a campaign that one job scores therefore share the same weights, precision and server settings.

### D5a. Stale-claim recovery

`--requeue` keeps the job ID, so a preempted job that Slurm requeues still owns its claims. Some jobs end without a terminal marker and without a requeue: a job that reaches its time limit, a cancelled job, or a node failure. Those jobs leave their claim behind with no live owner.

The **current claim** of a bundle is the `claim.<gen>` file with the highest `gen`. Each claim file holds its job id from the moment its name appears (D3), so a claim with no owner record cannot exist.

The current claim is **stale** when all three conditions are true:
1. the bundle has no `SCORED` or `FAILED` marker;
2. the job ID in the claim is not in `squeue -h -j <id>`, which means it is not pending, running, or requeued;
3. `sacct` reports that job ID in a terminal state.

Condition 3 prevents a false stale report during a short `squeue` gap. A claim file that is empty or that does not hold a job id can come only from a manual edit. The job treats it as stale only after the same checks fail for it, and `--status` reports it.

A new job takes over a stale `claim.<gen>` with one `link` of `claim.<gen+1>`. The old claim file stays in place as a record. If two jobs find the same stale claim, both try `claim.<gen+1>`, and exactly one `link` succeeds. A slow job that read `claim.<gen>` as stale after another job already took it over also tries `claim.<gen+1>`, and it fails, because that name exists. The takeover therefore never removes or replaces a live claim. (A rename-based takeover does not have this property: a slow job can rename away the new, live claim of the job that won.)

The original job is known to be finished, so the takeover cannot race with it. Scoring resumes from the checkpoint of the new job's own `judge_identity` (D7). The work of a dead job with the same identity is kept. The work of a dead job with a different identity is not used. `judge_submit.sh --status` lists stale claims.

### D6. Judge server on a private Unix socket, with a per-job API key

The job starts vLLM from an Apptainer image of the upstream `vllm/vllm-openai` image at a pinned tag (not the V100 build `vllm_volta.sif`).

**The server has no TCP listener.** Other users on a shared node can reach `127.0.0.1`, and the vLLM API key does not protect every endpoint: it covers only the `/v1`, `/v2`, `/inference` and `/cohere` prefixes, and `/invocations` runs inference with no key (vLLM docs, "API Key Authentication Limitations"). A key on a loopback port therefore does not keep a co-tenant off the judge. The server listens on a Unix socket (`vllm serve --uds <path>`) in a folder under the job's private temporary folder, with mode `0700`. Only processes of our user can connect to it.

The job also sets a random per-job key, through the `VLLM_API_KEY` environment variable and never as a command-line argument, because other users can read a process's arguments through `ps` or `/proc/<pid>/cmdline`. The key is a second guard, not the main one.

The scorer builds the judge through the judge factory (D2), with an OpenAI-compatible client whose HTTP transport connects to the Unix socket, and with the key from the environment. A unit test confirms that the client sends its requests through the socket, and that the key reaches the request. The job waits until `GET /v1/models` returns HTTP 200 before it claims anything, and it stops the server on exit, through a `trap`.

### D7. Save each row's score and resume

`get_ragas_results` scores each metric over its eligible rows in one ragas call. The offline scorer calls the shared function over **chunks** of rows (the default is 8), and it appends each chunk's results to a checkpoint file with `fsync`. Each record is one line that ends with a newline.

**A checkpoint belongs to one judge identity.** The file is `scores.<identity digest>.partial.jsonl`, where the digest is the SHA-256 of the canonical `judge_identity` (D10). The match rule (D4a) does not compare precision, the vLLM image, the server settings, the decoding settings or the embedding, so a replacement job can differ from a dead job in those fields. A job reads and appends only the file of its own identity. Scores from a different identity are therefore never combined with its own, and they are never labelled with its identity. `judge_scores.json` records the identity, and its scores come from one file only.

An append is not atomic, so a kill during the write can leave a torn last line. When the scorer starts, it reads the file with these rules:
- A last line with no final newline, or a last line that is not valid JSON, is a torn write. The scorer truncates the file to the end of the last complete line before it appends again.
- A line that is not valid JSON and is not the last line is corruption, not a torn write. The scorer marks the bundle `FAILED` with the line number.
- If a `(key, metric)` pair occurs twice, the first record is used.

It then skips the `(key, metric)` pairs that are already recorded. The final aggregates are computed from the complete per-row set with the same aggregation code (D2). The sbatch script uses `--requeue --open-mode=append`, so this also works on `gpu_requeue`.

### D8. The merge writes a new judged file

`scripts/benchmarking/judge/merge.py` runs as a CPU job with `--dependency=afterok:<judge job>`. It also runs by hand.

**The unit of a merge is one result JSON, not one bundle.** One result JSON holds every arm of a run (`ResultHandler.dump`, `src/bin/service_benchmark.py:756`), and the leaderboard and the A/B comparisons need all arms. Each bundle's manifest records the result basename (D3), its arm index and the arm count of the result. The merge groups bundles by result basename, and for each result it:
1. waits: if any arm of the result has no bundle, or a bundle that is not `SCORED`, or if `<queue>/results/<basename>.json` does not exist yet, it writes nothing for that result, reports what it waits for, and exits 0. A later merge completes the result. If any arm is `FAILED`, it writes nothing, names the arm, and exits non-zero;
2. reads the pending result JSON from `<queue>/results/`;
3. checks each bundle digest against the digest recorded in its pending arm;
4. writes every arm's per-row scores and `total_results` aggregates;
5. **checks that every arm has the same `judge_identity`** (the G9 rule, D9) before it computes anything across arms. If the identities are equal, it rebuilds the leaderboard and the A/B comparisons with the existing `ResultHandler` code. If they differ, it leaves out every RAGAS metric from the leaderboard and the A/B comparisons, and it records `cross_arm_ragas: refused` with the fields that differ. The per-arm scores stay, each with its own identity. A judged file therefore never holds a cross-arm RAGAS comparison that G9 would refuse;
6. writes `<basename>.judged.json` in `<queue>/results/`, with `judge_status: scored` and, for each arm, `judge_identity` and `judge_execution` (D10).

A judged file is therefore written once, from the complete set of arms, and a later merge never needs to change it.

It does not change the pending file. Output is canonical JSON (sorted keys, `allow_nan=False`, the same writer as `dump`), so a second run gives identical bytes.

The merge is safe when two merges run at the same time (the afterok job and a manual run) and when a merge is killed while it writes:
- It writes a unique temporary file in the same folder, calls `fsync`, validates the file by loading it again, and publishes it with the `link` primitive (D3). `link` fails if the judged file exists, so a second merge can never replace the first.
- If the judged file exists (before the write, or because `link` failed): identical bytes are a no-op success. Different bytes make the merge exit non-zero. It never overwrites.
- It writes `MERGED` in each bundle of the result only after the judged file exists with its bytes.
- A reader must trust a judged file only when `MERGED` exists. A killed merge therefore leaves only a temporary file. A later merge removes temporary files that are older than 1 hour, so it never removes the file of a merge that is still running.

The publish timer copies each judged file back into the output folder (D3). A person copies judged files into `bench_out/` and commits them.

### D8a. One judge mode for each run

Each sweep config loads its own benchmarking settings (`load_new_configuration`, `src/bin/service_benchmark.py:1422-1434`), so one run can have an inline arm and a deferred arm. The merge unit is the whole result (D8), and an inline arm has no bundle, so such a result would never be merged. The run therefore reads `judge_mode` from every config before it answers any question, and it refuses to start if the configs do not all have the same mode. A person who wants an inline spot check next to a deferred run makes two runs.

### D10. Judge identity, scorer identity, and execution record

The design keeps three records separate:

- **`judge_identity`** decides comparability, and G9 compares it field by field:
  - the model id and HF revision SHA;
  - the weight precision (`bf16`, `fp8`, and so on);
  - the vLLM image digest, and the server settings that change output (`--max-model-len`, `--dtype`, the quantization, the chat template);
  - the decoding settings that the scorer sends (temperature, top_p, max tokens);
  - the **resolved ragas call settings**: every value that the shared scoring function passes to `RunConfig` or to `ragas.evaluate`, after defaults. That is the output of `ragas_run_config_kwargs` (so `timeout` and `max_workers`, as `ragas_effective_settings` reports them, `src/utils/benchmark_schema.py:695-709`), the `batch_size` that goes to `evaluate` (`src/bin/service_benchmark.py:1979`, `:2000`), and the scorer's chunk size (D7). The existing runtime already treats judge pressure as a comparability field (`src/bin/service_benchmark.py:1181-1184`), because it changes the missing-score rate and so the denominator of every aggregate. The run records these values in the manifest, and the scorer uses the manifest's values, not its own defaults;
  - the **embedding** that ragas uses (`get_ragas_results` passes it into the evaluation, `src/bin/service_benchmark.py:1986`, `:1998`): the provider class, the model name that the built object reports (not the config string), and the model revision where the provider gives one. The object is the source because `get_ragas_embedding_model` (`:1713`) gives an unknown config value the OpenAI default, and both providers use their library's default model;
  - the **scorer identity** below.

  It excludes every per-job value, so arms that separate jobs scored with the same settings stay comparable.
- **Scorer identity**:
  - the **code digest** of the scorer's installed `src` package, computed by the same function that makes the benchmark's `code_version.digest` (`src/utils/benchmark_provenance.py:673-699`). A repository commit cannot be used: nothing stamps a commit into the image, and the benchmark's `deploy_git_commit` is frozen at deploy and does not name the code that ran (`docs/docs/interpreting_benchmark_results.md:963-973`). Both sides therefore compare the same content digest;
  - the scorer Apptainer image digest;
  - a SHA-256 of the sorted `pip freeze` output of the scorer's environment (this pins ragas, langchain and every other dependency);
  - the ragas version as a readable copy of the same fact;
  - the metric list and the per-metric settings from the manifest.

  ragas holds its prompt templates inside the pinned package, so the package digest covers them.
- **`judge_execution`** is the record of the job, and nothing compares it: the job id, node name, start and end times, the list of claimed bundles, and the requeue count.

**The scorer fails closed.** It never scores a bundle whose manifest code digest or package digest differs from its own scorer identity. A deferred run whose `code_version.digest` is unavailable (the `<unavailable>` case in `code_version`) refuses to start, because no scorer could ever match its bundles. There is no override. The refusal is not a terminal state: through the match rule (D4a), the job does not claim such a bundle, and the bundle stays READY with no marker. To score it, build a scorer image whose `src` has the manifest's code digest: build it from the benchmark image that made the run, or from a checkout whose `src` gives the same digest. The manifest's `deploy_git_commit` is only a hint for where to look. The next job that runs that image matches the bundle and claims it. `scorer-identity.json` can be out of date, and then the submitter and the job both match a bundle that the live scorer must refuse. Without a record, each tick would submit a new GPU job that claims the bundle, refuses it and exits, with no end. Two checks stop this:
- **At job start, before vLLM and before any claim,** the job computes its live scorer identity and compares it with the sidecar. On a difference, it creates `<queue>/SCORER_STALE.<sidecar digest>` with both values, claims nothing, and exits non-zero. The match rule then excludes every bundle for that sidecar (D4a), so the submitter stops.
- **After a claim,** the job compares its live identity with the manifest again. On a difference, it creates the bundle's `INCOMPATIBLE.<sidecar digest>` with both values, writes no score, and continues with its other bundles. The match rule excludes that bundle for that sidecar.

Neither record is terminal. A rebuilt scorer has a new sidecar digest, and the bundles become available again. `--status` reports both records.

The inline path records the same `judge_identity` shape: provider `huit_bedrock`, the Bedrock model id, and the benchmark image's package digest. Inline and offline arms can then be compared by the same rule, and they differ on the model, as they must.

### D9. Two new gates in compare_runs.py

- **G8, judged.** Refuse any arm with `judge_status: pending`.
- **G9, one judge.** Refuse a RAGAS comparison when the arms' `judge_identity` (D10) differ in any field, and name the fields that differ. An old inline artifact with no `judge_identity` gets `(evaluator_provider, evaluator_model)` from its recorded settings. It is then comparable only with other old inline artifacts that have the same pair, because a missing digest never equals a recorded one.

There is no override flag, because the result of a mixed comparison has no meaning.

**The campaign archiver obeys G8 and G9 too.** `scripts/benchmarking/feature_matrix/archive_run.sh` takes the newest `benchmarking-<stack>-*.json` (`:91`), never runs `compare_runs.py`, and writes the arm's aggregates into the campaign ledger (`:212-239`). A pending artifact would therefore enter the ledger with no RAGAS metrics. The archiver refuses an artifact with `judge_status: pending`, and names the judged file to wait for. It accepts the judged file (`judge_status: scored`) when the publish timer has copied it into the output folder. The G8 check is one shared function that `compare_runs.py` and the archiver both call.

The archiver also applies G9 across runs, because separately archived runs are compared through the ledger, not through `compare_runs.py`. It writes each arm's `judge_identity` into its ledger row (the row has none today, `archive_run.sh:221-235`). It refuses an archive whose `judge_identity` differs from the identity of the campaign's earlier RAGAS rows, and it names the fields that differ. An earlier row with no identity never equals a recorded one (the D9 rule), so the first judged run of a campaign that has only older rows is refused, and the operator starts a new campaign for the new judge.

## Risks / Trade-offs

- [The new judge disagrees with Sonnet, or gives more NaN rows] → Before the switch, score one fm-00 bundle with each candidate and with inline Sonnet. Compare row-level rank agreement and NaN counts for each metric. Choose with those numbers. G9 prevents a silent mix.
- [vLLM output at temperature 0 changes a little with the other requests in the same batch] → Record the job id and the list of claimed bundles in each `judge_execution` (not in `judge_identity`, so it does not block comparison). The effect is expected to be far below run-to-run noise, and the record makes it possible to check later.
- [Queue wait on `gpu_h200` is long] → Claim-at-start makes a long wait add more bundles to the job. The operator can switch the sbatch partition to `gpu_requeue` (D7 makes that safe) or to 1 × H200 with FP8. Precision is part of `judge_identity`.
- [A lost scrontab entry or a crashed submitter leaves bundles READY with no job] → The submitter logs each tick. A READY bundle older than 24 hours is reported by `judge_submit.sh --status`.
- [Many parallel ragas requests over a small chunk give less throughput] → `max_workers`, `batch_size` and the chunk size are manifest settings, and they are part of `judge_identity`. Measure rows per minute in the first job and choose them once. A later change starts a new baseline, and G9 shows it.
- [The scorer's Python environment differs from the benchmark image, so the ragas or langchain versions drift] → Run the scorer in an Apptainer image built from the benchmark image. The scorer identity (D10) pins the code, the image digest and the package digest, and any mismatch fails closed, with no override.
- [A job ends without a requeue and leaves its claim] → Stale-claim recovery (D5a).
- [A time-limited job scores some arms of a result and leaves the others] → The merge waits for all arms of the result (D8), and the next job claims the rest.
- [Each change to archi's `src` needs a matching scorer image before its bundles can be scored] → This is the cost of the fail-closed rule. `--status` lists bundles that wait for a scorer, with the code digest and the `deploy_git_commit` hint they need.
- [The dev-host publish timer stops] → Bundles stay under `out_dir` with `BUNDLE_COMPLETE` and no queue copy. `publish.py --status` lists them, and a manual run publishes them.
- [`service_benchmark.py` is large and black-sensitive] → D2 extraction goes first, as its own mechanical PR, and new logic lives in the new module.

## Migration Plan

1. PR 1: extract the scoring (D2), with no change in behavior. The existing tests pass unchanged.
2. PR 2: `judge_mode: deferred`, the bundle writer, the publish step, and `judge_status`. Default `inline`. A deferred run can be made and published, but nothing scores it yet.
3. PR 3: the offline scorer (claim, budget, checkpoints, stub-judge tests) and the merge.
4. PR 4: the Slurm scripts (submitter, judge job, merge job) with `--dry-run`, the G8 and G9 gates, and docs in `docs/docs/benchmarking.md`.
5. Operator: pull the vLLM image and the judge weights to shared storage, build the scorer image, install the `scrontab` entry and the `archi-judge-publish.timer` units (in `fasrc/archi-config`), and run the judge comparison.
6. Switch the golden-set config in `fasrc/archi-config` to `judge_mode: deferred`.

Rollback: set `judge_mode: inline` (or remove the key). Pending bundles can stay in the queue, and nothing else depends on them.

## Open Questions

- **Judge model.** Llama-3.3-70B-Instruct first, and DeepSeek-R1-Distill-Llama-70B as a second candidate. Qwen models are excluded because Qwen is the agent model, and DeepSeek-R1-Distill-Qwen-32B is built on Qwen2.5. The comparison in Risks decides.
- **Partition and precision.** `gpu_h200` with 2 × H200 in bf16, or 1 × H200 in FP8, or `gpu_requeue`. This depends on the lab's access and on the measured queue waits.
- **Can the dev host run `sbatch`?** If it cannot, the chain is: the publish step writes to shared home, and `scrontab` submits. Nothing needs the dev host to talk to Slurm.
- **Queue location.** `$HOME/archi-judge/queue` or a lab scratch folder. `$HOME` has a quota. Bundles are small (the text of about 109 rows), but the vLLM image and the weights (about 140 GB) must be on lab or netscratch storage.
