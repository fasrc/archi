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
- [ ] 1.4 Add a test that the `huggingface` judge sends a configured API key in its requests.

## 2. PR 2: deferred mode, bundle writer, publish step

- [ ] 2.1 Add tests for `judge_mode` parsing: absent or `inline` gives inline, `deferred` gives deferred, and any other value is refused.
- [ ] 2.2 Render `judge_mode` and `judge_bundle_dir` in `src/cli/templates/base-config.yaml`, with a render test.
- [ ] 2.3 Add a test: the bundle writer makes `rows.jsonl` equal to the inline ragas records for each key, and a `manifest.json` that has every field in the spec and the correct SHA-256. Then write the writer (`src/utils/judge_bundle.py`).
- [ ] 2.4 Add a test: a deferred arm makes no judge-factory call, records `judge_status: pending` and the bundle digest, and has no RAGAS aggregates. Then add the call site in `_process_config`.
- [ ] 2.5 Add a test: a result JSON with no `judge_status` loads as `inline`.
- [ ] 2.6 Add tests for `scripts/benchmarking/judge/publish.py`: `READY` is written last, a digest mismatch exits non-zero with no `READY`, and a stop in the middle leaves no `READY`. Then write the script, and call it from `run_goldenset_eval.sh` when `judge_mode` is `deferred`.

## 3. PR 3: offline scorer and merge

- [ ] 3.1 Add tests for claiming: claim-at-start takes the READY bundles that match the judge, skips other judges, and exactly one of two racing claimants wins (`mkdir`).
- [ ] 3.1a Add tests for stale-claim recovery, with `squeue` and `sacct` replaced by stubs:
  - a claim whose job is `TIMEOUT` in `sacct` and absent from `squeue` is taken over, and only the unsaved pairs are scored;
  - a claim whose job is running is not taken;
  - of two jobs that race to take one stale claim, exactly one wins;
  - a requeued job with the same job id keeps its claim.
- [ ] 3.1b Add tests for the scorer identity: a code-version mismatch or a package-digest mismatch makes the bundle `FAILED` with both values in the reason, and no scores are written. No flag overrides this.
- [ ] 3.2 Add tests for the time budget: the first bundle is always claimed, and a later bundle that needs more time than is left stays READY.
- [ ] 3.3 Add tests for checkpoints: chunked scoring appends to `scores.partial.jsonl`, and a restart after 40 of 109 rows scores only the other 69, with one value for each `(key, metric)`.
- [ ] 3.4 Add a test: a bundle whose digest does not match becomes `FAILED` with a reason, and the other bundles become `SCORED`.
- [ ] 3.5 Add a test: offline aggregates equal inline aggregates for the same rows and the same stub judge.
- [ ] 3.6 Write the scorer (`src/evaluation/judge/` or `src/utils/judge_scorer.py`) and its CLI entry `scripts/benchmarking/judge/score.py`.
- [ ] 3.6a Add tests for `judge_identity` and `judge_execution` (design D10):
  - two jobs with the same settings give equal identities and different execution records;
  - a precision change (`bf16` against `fp8`) changes the identity;
  - the inline path records the same identity shape.
- [ ] 3.7 Add tests for the merge, then write `scripts/benchmarking/judge/merge.py`. The tests check that:
  - the judged file has the per-row scores, the aggregates, the leaderboard, A/B, `judge_status: scored`, `judge_identity` and `judge_execution`;
  - the pending file is unchanged, and a second merge gives identical bytes;
  - a digest mismatch exits non-zero;
  - a merge killed while it writes leaves no judged file and no `MERGED`, and the next merge cleans up and completes;
  - two concurrent merges leave one complete file;
  - an existing judged file with different bytes is never overwritten.

## 4. PR 4: Slurm scripts, gates, docs

- [ ] 4.1 Add tests for `scripts/benchmarking/slurm/judge_submit.sh`, with `squeue` and `sbatch` replaced by stubs on `PATH`. It submits nothing if a job exists or no bundle is READY, and exactly one job if a bundle is READY and no job exists. `--status` lists READY bundles older than 24 hours.
- [ ] 4.2 Write `scripts/benchmarking/slurm/archi_judge.sbatch`. It has `#SBATCH -J archi-judge --requeue --open-mode=append`, and the partition, GPU count and `-t` are settings at the top of the script. It starts vLLM in Apptainer on `127.0.0.1` with a random `--api-key`, waits for `/v1/models`, runs the scorer, and stops vLLM through a `trap`. It then submits the merge job with `--dependency=afterok:$SLURM_JOB_ID`. It must have a `--dry-run` that prints the resolved commands, with a test for it.
- [ ] 4.3 Write `scripts/benchmarking/slurm/judge_merge.sbatch` (CPU only), with a `--dry-run` test.
- [ ] 4.4 Add tests for G8 in `scripts/benchmarking/compare_runs.py`: a pending arm exits non-zero and is named. Then write the gate.
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
- [ ] 5.4 Install the `scrontab` entry, and switch the golden-set config in `fasrc/archi-config` to `judge_mode: deferred`.
