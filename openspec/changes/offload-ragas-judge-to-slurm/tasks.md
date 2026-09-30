Rules for every task:
- Branch from `origin/dev`, and open each PR with `gh pr create --repo fasrc/archi --base dev`.
- Write a failing unit test first and watch it fail, then write the minimum code.
- Run `bash scripts/gate.sh` before each commit, and never use `--no-verify`.
- Add no `Co-Authored-By` lines.
- Keep new logic in the new modules, with only thin call sites in `src/bin/service_benchmark.py`.
- Unit tests use a stub judge and a temporary queue folder. No test needs a GPU, Slurm, or network access.

## 1. PR 1: extract the RAGAS scoring (mechanical, no change in behavior)

- [ ] 1.1 Run the black-seam check on `src/bin/service_benchmark.py`. Record whether an in-place edit reflows the file.
- [ ] 1.2 Add a test: a stub judge scores fixed rows through `Benchmarker.get_ragas_results`, and the test records the aggregates and scored counts.
- [ ] 1.3 Move the scoring (`get_ragas_results`, the judge factory and the `RunConfig` assembly) into `src/utils/ragas_scoring.py`. The module takes rows, keys, settings and a judge factory, and `Benchmarker` keeps thin wrappers. The 1.2 test must pass unchanged.
- [ ] 1.4 Add a test that the judge factory can build a client whose requests go through a Unix socket and carry a key read from the environment.

## 2. PR 2: deferred mode, bundle writer, publish step

- [ ] 2.1 Add tests for `judge_mode` parsing: absent or `inline` gives inline, `deferred` gives deferred, and any other value is refused. A sweep whose configs have different modes refuses to start before any question, and names the configs (design D8a).
- [ ] 2.2 Render `judge_mode` and `judge_bundle_dir` in `src/cli/templates/base-config.yaml`, with a render test.
- [ ] 2.2a Add a test: the run fixes its result basename once, before the first arm, and `dump_artifacts` writes the JSON and the `_report.md` with that basename. Then pass the basename from the run start to `dump_artifacts` and to the bundle writer.
- [ ] 2.2b Add a test: a deferred run writes `<basename>.RESULT_COMPLETE` only after `dump_artifacts` returns, and a deferred run whose `code_version.digest` is unavailable refuses to start.
- [ ] 2.3 Add a test: the bundle writer makes `rows.jsonl` equal to the inline ragas records for each key, a `manifest.json` that has every field in the spec (including the arm count of the result, and the resolved `ragas_run_config_kwargs` output and `batch_size`) and the correct SHA-256, and `BUNDLE_COMPLETE` after every other file. Then write the writer (`src/utils/judge_bundle.py`).
- [ ] 2.4 Add a test: a deferred arm makes no judge-factory call, records `judge_status: pending` and the bundle digest, and has no RAGAS aggregates. Then add the call site in `_process_config`.
- [ ] 2.5 Add a test: a result JSON with no `judge_status` loads as `inline`.
- [ ] 2.5a Add tests for the `link` primitive (design D3) in one shared helper: exactly one of two writers of the same name wins; the loser never replaces the winner's bytes; a writer whose `link` returns `EEXIST` while its temporary file has link count 2 reports success; the name never exists without its full content.
- [ ] 2.6 Add tests for `scripts/benchmarking/judge/publish.py`. The tests check that:
  - `READY` is written last, with the `link` primitive;
  - a digest mismatch exits non-zero with no `READY`, and a stop in the middle leaves no `READY`;
  - a bundle with no `BUNDLE_COMPLETE` is not copied;
  - a second run over a bundle that is READY with the same digest changes nothing and exits 0, and a different digest exits non-zero and changes nothing;
  - the pending result JSON is copied to `<queue>/results/` only after `<basename>.RESULT_COMPLETE` exists and the file loads with one arm for each bundle and matching digests; a half-written file is not copied;
  - a judged file with `MERGED` on all its bundles is copied back into the output folder.
  Then write the script. `run_goldenset_eval.sh` does not call it.
- [ ] 2.7 Write the `archi-judge-publish.service` and `archi-judge-publish.timer` user units (every 5 minutes) in `fasrc/archi-config` beside the other host timers, as a separate PR in that repository. `publish.py --status` lists complete bundles that are not yet in the queue.

## 3. PR 3: offline scorer and merge

- [ ] 3.0 Add tests for the match rule in `scripts/benchmarking/judge/queue.py` (standard library only), then write it. A bundle matches only when its judge model id and revision equal `judge.env`, and its code digest (`code_version.digest`) and package digest equal `scorer-identity.json`. The scorer image build computes the code digest with the same function as the benchmark (`src/utils/benchmark_provenance.py`), and a test checks that the two agree for the same `src`. A bundle that does not match gets a reason that names the judge it waits for, or both scorer values.
- [ ] 3.1 Add tests for claiming: claim-at-start takes the READY bundles that match (3.0) and skips the others; exactly one of two racing claimants wins (`link` of `claim.1`); a claim file holds the job id at the moment its name exists.
- [ ] 3.1a Add tests for stale-claim recovery, with `squeue` and `sacct` replaced by stubs:
  - a claim whose job is `TIMEOUT` in `sacct` and absent from `squeue` is taken over, and only the unsaved pairs are scored;
  - a claim whose job is running is not taken;
  - of two jobs that race to take one stale claim, exactly one `link` of `claim.<gen+1>` wins;
  - a slow job that read `claim.1` as stale after another job made `claim.2` fails its `link` and leaves `claim.2` unchanged;
  - no takeover removes or renames a claim file;
  - a requeued job with the same job id keeps its claim, counts it as work at start (so it does not exit before the model load), and resumes it; the submitter does not count it as available.
- [ ] 3.1b Add tests for the scorer identity:
  - a code-version mismatch or a package-digest mismatch writes no score and no terminal marker, the bundle stays READY, and `--status` names both values. No flag overrides this;
  - after a matching `scorer-identity.json` is configured, the same bundle is available and is claimed;
  - a live environment that differs from the sidecar at job start creates `SCORER_STALE.<sidecar digest>`, claims nothing, and loads no model; the match rule then excludes every bundle for that sidecar, and a new sidecar digest makes them available again;
  - a live-environment mismatch found after a claim creates the bundle's `INCOMPATIBLE.<sidecar digest>`, writes no score, and the job continues with its other bundles; the match rule then excludes that bundle for that sidecar.
- [ ] 3.2 Add tests for the time budget: the first bundle is always claimed; before any row is scored, the bootstrap rate from `judge.env` decides; a later bundle that needs more time than is left stays READY.
- [ ] 3.3 Add tests for checkpoints. The tests check that:
  - chunked scoring appends newline-terminated lines to `scores.<identity digest>.partial.jsonl`, and a restart after 40 of 109 rows scores only the other 69, with one value for each `(key, metric)`;
  - a file that ends with half a JSON line (a kill inside the write) is truncated to the last complete line, and the pair in the half line is scored again;
  - an invalid line that is not the last line makes the bundle `FAILED` with the line number;
  - a repeated `(key, metric)` pair uses its first record;
  - a job with a different `judge_identity` (for example `fp8` after `bf16`) does not read the other identity's file, and its final scores come only from its own file.
- [ ] 3.4 Add a test: a bundle whose digest does not match becomes `FAILED` with a reason, and the other bundles become `SCORED`.
- [ ] 3.5 Add a test: offline aggregates equal inline aggregates for the same rows and the same stub judge.
- [ ] 3.6 Write the scorer (`src/evaluation/judge/` or `src/utils/judge_scorer.py`) and its CLI entry `scripts/benchmarking/judge/score.py`.
- [ ] 3.6a Add tests for `judge_identity` and `judge_execution` (design D10):
  - two jobs with the same settings give equal identities and different execution records;
  - a precision change (`bf16` against `fp8`) changes the identity;
  - an embedding change (OpenAI against HuggingFace, or a different model name reported by the built object) changes the identity;
  - a change of the effective `timeout`, `max_workers`, `batch_size` or chunk size changes the identity, and an explicit default equals an unset value;
  - the offline scorer passes the manifest's `batch_size` and run-config values to ragas, not its own defaults;
  - the inline path records the same identity shape.
- [ ] 3.7 Add tests for the merge, then write `scripts/benchmarking/judge/merge.py`. The tests check that:
  - the merge groups bundles by result file, and writes nothing while any arm is not `SCORED` (it names the pending arms and exits 0); after the last arm is `SCORED`, one judged file holds all arms;
  - a `FAILED` arm makes the merge write nothing for that result and exit non-zero;
  - a result whose pending JSON is not yet in `<queue>/results/` gets nothing, and the merge exits 0;
  - arms with different `judge_identity` values keep their per-arm scores, and the leaderboard and A/B comparisons have no RAGAS metric, with `cross_arm_ragas: refused` naming the fields;
  - the judged file has every arm's per-row scores and aggregates, the leaderboard, A/B, `judge_status: scored`, and each arm's `judge_identity` and `judge_execution`;
  - the pending file is unchanged, and a second merge gives identical bytes;
  - a digest mismatch exits non-zero;
  - a merge killed while it writes leaves no judged file and no `MERGED`, and the next merge completes, and removes the temporary file only when it is older than 1 hour;
  - two concurrent merges leave one complete file;
  - two concurrent merges with different bytes: exactly one `link` wins, and the other exits non-zero;
  - an existing judged file with different bytes is never overwritten.

## 4. PR 4: Slurm scripts, gates, docs

- [ ] 4.1 Add tests for `scripts/benchmarking/slurm/judge_submit.sh`, with `squeue` and `sbatch` replaced by stubs on `PATH`. It submits nothing if a job exists or no bundle is available, nothing if the only READY bundles do not match the configured judge or scorer (3.0), and exactly one job if a matching bundle is READY and no job exists. Every `sbatch` of `archi-judge` has `--dependency=singleton`. For a result whose arms are all `SCORED` with no `MERGED` and no merge job, it submits one `archi-judge-merge` job. `--status` lists READY bundles older than 24 hours, bundles that do not match with the reason, `SCORER_STALE` and `INCOMPATIBLE` records, and stale claims.
- [ ] 4.2 Write `scripts/benchmarking/slurm/archi_judge.sbatch`. It has `#SBATCH -J archi-judge --requeue --open-mode=append`, and the partition, GPU count and `-t` are settings at the top of the script. It checks the live scorer identity against the sidecar and counts its available bundles first, and exits without starting vLLM when the sidecar is stale or no bundle is available. It starts vLLM in Apptainer on a Unix socket (`--uds`) in a `0700` folder under the job's private temporary folder, with no TCP listener, and with a random key in `VLLM_API_KEY` (never on the command line). It waits for `/v1/models` through the socket, runs the scorer, and stops vLLM through a `trap`. It then submits the merge job with `--dependency=afterok:$SLURM_JOB_ID`. It must have a `--dry-run` that prints the resolved commands, with a test for it.
- [ ] 4.3 Write `scripts/benchmarking/slurm/judge_merge.sbatch` (CPU only), with a `--dry-run` test.
- [ ] 4.4 Add tests for G8 as one shared check, used by `scripts/benchmarking/compare_runs.py` and `scripts/benchmarking/feature_matrix/archive_run.sh`: a pending arm exits non-zero and is named; the archiver appends nothing to the ledger for a pending artifact and names the judged file to wait for; it archives a `judge_status: scored` judged file, with each arm's `judge_identity` in the ledger row; it refuses a scored artifact whose identity differs from the campaign's earlier RAGAS rows, and names the fields. Then write the gate.
- [ ] 4.5 Add tests for G9, then write the gate. The tests check that:
  - arms whose `judge_identity` differs are refused, and the differing fields are named (inline Sonnet against offline Llama; `bf16` against `fp8`);
  - two offline arms with equal identity from separate jobs compare;
  - two older inline artifacts with the same judge still compare;
  - an old artifact never matches a new one that records a digest.
- [ ] 4.6 Document the operator steps in `docs/docs/benchmarking.md`: image and weights download, the `scrontab` entry, `judge_mode: deferred`, how to read the queue, and how to copy judged files into `bench_out/`.

## 5. Operator (after PR 4 merges, not automated)

- [ ] 5.1 Pull the vLLM image to a SIF, and the judge weights to lab or netscratch storage. Record the image tag and the model revision.
- [ ] 5.2 Make one deferred fm-00 run and publish it. Run the judge job by hand on the chosen partition, and record the queue wait, the load time and the rows per minute.
- [ ] 5.3 Compare judges: score the same bundle with each candidate, and compare with an inline Sonnet run of the same answers (row-level rank agreement and NaN counts for each metric). Record the choice on the tracking issue.
- [ ] 5.4 Install the `scrontab` entry and the `archi-judge-publish.timer` units, and switch the golden-set config in `fasrc/archi-config` to `judge_mode: deferred`.
