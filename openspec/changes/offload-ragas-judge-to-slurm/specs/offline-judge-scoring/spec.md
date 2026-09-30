## ADDED Requirements

### Requirement: A deferred run writes a judge bundle instead of calling the judge
When `ragas_settings.judge_mode` is `deferred`, the benchmark SHALL answer every question and write a judge bundle for each arm, and it SHALL NOT make any RAGAS judge call. The bundle has two files. `rows.jsonl` has one line per scorable row, with the row's key and the full `user_input`, `retrieved_contexts`, `response` and `reference` that the inline path gives to ragas. `manifest.json` records the enabled metrics, `timeout`, `max_workers`, the embedding model, the judge model id and revision, the run's `code_version`, the SHA-256 of the run environment's sorted `pip freeze`, the result basename, the arm index, the arm count of the result, and the SHA-256 of `rows.jsonl`. The run SHALL fix the result basename once, before its first arm, and SHALL use it for both the bundles and the result JSON. The bundle writer writes a `BUNDLE_COMPLETE` marker after every other bundle file.

#### Scenario: Bundle names the result before the result exists
- **WHEN** a deferred run with two arms writes the bundle of arm 0
- **THEN** the manifest names the result basename, and the result JSON that the run writes at the end has that basename When `judge_mode` is absent or `inline`, the run SHALL score inline exactly as before.

#### Scenario: Deferred run makes no judge call
- **WHEN** a run with `judge_mode: deferred` completes with 3 scorable rows
- **THEN** the bundle's `rows.jsonl` has 3 lines, and each line has the full contexts, not the 300-character `sources_trunc_content`
- **AND** the judge factory was not called

#### Scenario: Bundle rows equal the inline ragas input
- **WHEN** the same questions and answers go through the inline path and the deferred path
- **THEN** each bundle row's `user_input`, `retrieved_contexts`, `response` and `reference` equals the record that the inline path passes to ragas for that key

#### Scenario: Inline default is unchanged
- **WHEN** a config has no `judge_mode` key
- **THEN** the run scores RAGAS inline and writes no bundle

### Requirement: A deferred result records a pending judge status
A deferred run's result JSON SHALL record `judge_status: pending` for each deferred arm, and it SHALL NOT record `aggregate_<metric>` values for RAGAS metrics. A result with no `judge_status` key SHALL be read as `inline`.

#### Scenario: Pending arm carries no RAGAS aggregates
- **WHEN** a deferred arm's result is written
- **THEN** the arm records `judge_status: pending` and the bundle's SHA-256
- **AND** its `total_results` has no `aggregate_faithfulness` key

#### Scenario: Old artifact reads as inline
- **WHEN** a result JSON written before this change is loaded
- **THEN** its judge status is `inline`

### Requirement: Publishing a bundle writes the READY marker last
The publish step SHALL copy the pending result JSON to the queue only after its `RESULT_COMPLETE` marker exists and the file loads with an arm for each bundle and matching digests. The publish step SHALL copy each bundle that has `BUNDLE_COMPLETE` into a folder in the shared queue directory, check each file against the manifest's SHA-256, and create the `READY` marker only after every file is in place. A scheduled timer on the dev host SHALL run the publish step, so that publishing does not depend on the process that started the run. The publish step SHALL skip a queue folder that is already READY with the same digest, and it SHALL exit non-zero, and change nothing, for a queue folder with a different digest.

#### Scenario: Pending result still being written
- **WHEN** the pending result JSON exists but has no `RESULT_COMPLETE` marker, or does not load as JSON
- **THEN** the publish step does not copy it to the queue, and it copies it on a later run after the marker exists and the file loads

#### Scenario: Detached run is published
- **WHEN** a deferred run was started with `--no-follow`, and its bundle gets `BUNDLE_COMPLETE` after the wrapper script exited
- **THEN** the next run of the publish timer makes the queue folder READY

#### Scenario: Bundle still being written
- **WHEN** the publish step finds a bundle folder with no `BUNDLE_COMPLETE`
- **THEN** it does not copy it

#### Scenario: Publish runs twice
- **WHEN** the publish step runs again over a bundle that is already READY in the queue with the same digest
- **THEN** it changes nothing and exits 0

#### Scenario: Interrupted publish is never READY
- **WHEN** the publish step stops after it copies `rows.jsonl` but before it writes `READY`
- **THEN** the queue folder has no `READY` marker, and the submitter and the judge job ignore it

#### Scenario: Corrupt copy is refused
- **WHEN** the copied `rows.jsonl` does not match the manifest's SHA-256
- **THEN** the publish step exits non-zero and writes no `READY` marker

### Requirement: The submitter keeps at most one running judge job
The submitter SHALL submit an `archi-judge` job only when at least one bundle is available (READY, with no terminal marker, unclaimed or with a stale claim, and matching the job's judge and scorer), and no `archi-judge` job of the same user is pending or running. Every `archi-judge` job SHALL be submitted with `--dependency=singleton`, and a job SHALL count its available bundles before it starts the judge server, and exit without loading a model when it has none. The submitter SHALL also submit an `archi-judge-merge` job for each result whose arms are all `SCORED`, whose pending JSON is in the queue, and that has no `MERGED`, when no merge job of the same user exists.

#### Scenario: Two submitters at the same time
- **WHEN** a scheduled run and a manual run of the submitter both see one available bundle and no job, and both submit
- **THEN** Slurm runs the two jobs one after the other, and the second job exits without loading a model

#### Scenario: Judge job killed before it submitted the merge
- **WHEN** every arm of a result is `SCORED`, no `MERGED` exists, and no merge job exists
- **THEN** the next submitter run submits one merge job

A bundle SHALL match a job only when the manifest's judge model id and revision equal the job's judge configuration, and the manifest's code digest (`code_version.digest`) and package digest equal the job's scorer identity. The submitter and the judge job SHALL use one shared function for this rule.

#### Scenario: Only bundles for another judge
- **WHEN** the queue holds only READY bundles whose manifest names a judge model or revision that the configured job does not load
- **THEN** the submitter submits nothing on every tick
- **AND** `judge_submit.sh --status` lists those bundles and the judge each one waits for

#### Scenario: Job already queued
- **WHEN** two bundles are READY and an `archi-judge` job is pending
- **THEN** the submitter submits nothing

#### Scenario: Nothing to do
- **WHEN** no bundle is READY
- **THEN** the submitter submits nothing and exits 0

#### Scenario: Work and no job
- **WHEN** one bundle is READY and no `archi-judge` job exists
- **THEN** the submitter submits exactly one job

### Requirement: The judge job claims bundles when it starts
The judge job SHALL claim bundles when it starts, not when it is submitted. It SHALL claim each READY bundle that matches it, by an atomic, no-replace `link` of a claim file that already holds its job id. It SHALL leave a bundle that does not match unclaimed.

#### Scenario: Job killed right after its claim
- **WHEN** a job is cancelled at the first moment after its claim file name exists
- **THEN** the claim file holds that job's id, and stale-claim recovery can check that job

#### Scenario: Bundle added during the queue wait
- **WHEN** a bundle becomes READY after the job was submitted but before it started
- **THEN** the job claims and scores that bundle

#### Scenario: Two jobs race for one bundle
- **WHEN** two judge jobs try to claim the same bundle
- **THEN** exactly one claim succeeds, and the other job skips the bundle

#### Scenario: Different judge
- **WHEN** a READY bundle names a judge model that the job did not load
- **THEN** the job does not claim it, and the bundle stays READY

### Requirement: A claim whose job has ended is recovered
The judge job and the submitter SHALL treat a bundle's current claim (the `claim.<gen>` file with the highest `gen`) as stale when the bundle has no `SCORED` or `FAILED` marker, the job id in that claim is not in `squeue`, and `sacct` reports that job in a terminal state. A job SHALL take over a stale `claim.<gen>` only by a no-replace `link` of `claim.<gen+1>`, it SHALL NOT remove or rename any claim file, and it SHALL resume from the bundle's saved scores.

#### Scenario: Slow job after a takeover
- **WHEN** job A read `claim.1` as stale, and job B took it over with `claim.2` before job A acted
- **THEN** job A's `link` of `claim.2` fails, job A skips the bundle, and job B's claim is unchanged

#### Scenario: Job hit its time limit while it held a claim
- **WHEN** a bundle's current claim names a job that `sacct` reports as `TIMEOUT`, and the bundle has 50 saved `(key, metric)` scores and no terminal marker
- **THEN** the submitter counts the bundle as available
- **AND** the next job takes over the claim and scores only the pairs that are not saved

#### Scenario: Claimant is still running
- **WHEN** a bundle's current claim names a job that `squeue` lists as running
- **THEN** the claim is not stale, and no other job takes it

#### Scenario: Two jobs take over one stale claim
- **WHEN** two jobs find the same stale claim at the same time
- **THEN** exactly one `link` of the next claim file succeeds, and the other job skips the bundle

#### Scenario: Requeued job keeps its claim
- **WHEN** Slurm requeues a preempted job with the same job id, and no other bundle is available
- **THEN** the job still owns its claims, counts them as its work at start, loads the model, and resumes them
- **AND** the submitter does not count those bundles as available

### Requirement: The scorer never scores a bundle from different code or packages
The scorer SHALL NOT score a bundle when the manifest's code digest (`code_version.digest`) differs from the content digest of the scorer's installed `src`, computed by the same function, or the manifest's package digest differs from the SHA-256 of the scorer environment's sorted `pip freeze`. No setting SHALL override this refusal. The refusal SHALL NOT write a terminal marker: the bundle stays READY for a scorer that matches, and `judge_submit.sh --status` reports both values.

#### Scenario: Package drift
- **WHEN** the manifest records a package digest and the scorer environment has a different ragas version
- **THEN** no score is written, the bundle has no `SCORED` or `FAILED` marker, and `--status` names both digests

#### Scenario: Scorer sidecar out of date
- **WHEN** `scorer-identity.json` matches a bundle's manifest, but the job's live scorer environment differs from the sidecar
- **THEN** the job creates `SCORER_STALE.<sidecar digest>`, claims nothing, loads no model, and exits non-zero
- **AND** the submitter submits no job for that sidecar until the sidecar changes

#### Scenario: Deferred run with no code digest
- **WHEN** `judge_mode` is `deferred`, and the run's `code_version.digest` is unavailable
- **THEN** the run refuses to start and writes no bundle

#### Scenario: Matching scorer installed later
- **WHEN** a bundle waited for a scorer with its manifest's code digest, and a scorer image whose `src` has that digest is then configured
- **THEN** the submitter counts the bundle as available, and the next job claims and scores it

#### Scenario: Matching environment
- **WHEN** the code version and the package digest both match
- **THEN** the scorer scores the bundle

### Requirement: The judge job saves each row's score and resumes after a requeue
The judge job SHALL append each scored `(key, metric)` result, as one newline-terminated line and as soon as it is available, to the bundle's checkpoint file for its own `judge_identity` (`scores.<identity digest>.partial.jsonl`). It SHALL NOT read or use a checkpoint file of a different identity. After a restart, it SHALL truncate a torn last line (no final newline, or not valid JSON), mark the bundle `FAILED` for an invalid line that is not the last line, use the first record of a repeated pair, and skip the pairs that are already recorded.

#### Scenario: Kill during an append
- **WHEN** a job is killed while it writes a record, and its checkpoint file ends with half a JSON line
- **THEN** the requeued job truncates the half line, keeps every complete record, and scores the pair that the half line held

#### Scenario: Replacement job with a different precision
- **WHEN** a `bf16` job saved 40 scores for a bundle and timed out, and an `fp8` job of the same model revision takes over the stale claim
- **THEN** the `fp8` job scores every pair again in its own checkpoint file, and the final scores come only from the `fp8` file

#### Scenario: Requeue in the middle of a bundle
- **WHEN** a job is killed after it scored 40 of 109 rows for a metric, and Slurm requeues it
- **THEN** the requeued job scores only the 69 rows that remain for that metric
- **AND** the final scores have exactly one value for each `(key, metric)`

### Requirement: The judge job stops claiming when its time budget is low
Before it claims another bundle, the judge job SHALL compare the time left in its allocation with its time per row multiplied by the bundle's row count. Before it has scored a row, it SHALL use the configured bootstrap rate; after that, its measured rate. If the time left is less, it SHALL leave the bundle READY for a later job.

#### Scenario: First job with no measured rate
- **WHEN** a new job starts with three READY bundles of 109 rows, 60 minutes left, and a bootstrap rate of 2 rows per minute
- **THEN** it claims only the first bundle at start, and it decides on the others after that bundle, with its measured rate

#### Scenario: Not enough time left
- **WHEN** 10 minutes remain, and the next bundle needs about 30 minutes at the measured rate
- **THEN** the job does not claim it, and it exits after the bundles it already claimed

### Requirement: One bundle's failure does not stop the others
If the judge job cannot score a bundle, it SHALL mark that bundle `FAILED` with a reason, and it SHALL continue with the other claimed bundles. A row that the judge cannot score SHALL be recorded as NaN for that metric, with the error, and SHALL NOT fail the bundle.

#### Scenario: Unreadable bundle
- **WHEN** one of three claimed bundles has a `rows.jsonl` that does not match its manifest digest
- **THEN** that bundle is `FAILED` with the reason, and the other two are `SCORED`

### Requirement: The scorer uses the inline path's RAGAS code
The offline scorer and the inline benchmark path SHALL call one shared scoring function for per-metric eligibility, ragas invocation, and aggregation. Given the same rows and the same judge, the two paths SHALL produce the same `aggregate_<metric>` and `<metric>_scored` values.

#### Scenario: Same rows, stub judge
- **WHEN** a stub judge that returns fixed scores scores the same rows through both paths
- **THEN** both paths give equal aggregates and scored counts

### Requirement: The judge identity excludes per-job values
Each judged arm SHALL record a `judge_identity` with the model id and revision, the weight precision, the vLLM image digest, the output-relevant server settings, the decoding settings, the embedding (provider class, the model name that the built embedding object reports, and its revision where the provider gives one), and the scorer identity (the code digest of the scorer's `src`, scorer image digest, package digest, ragas version, metric settings). It SHALL record the job id, node, times, claimed bundles and requeue count in a separate `judge_execution` record.

#### Scenario: Embedding differs
- **WHEN** two arms have the same judge model and settings, but one was scored with the OpenAI embedding and the other with the HuggingFace embedding
- **THEN** their `judge_identity` records differ in the embedding fields

#### Scenario: Same settings, different jobs
- **WHEN** two arms were scored by two different jobs with identical model, precision, image, server, decoding and scorer settings
- **THEN** their `judge_identity` records are equal, and their `judge_execution` records differ

#### Scenario: Precision differs
- **WHEN** one arm was scored with `bf16` weights and the other with `fp8` weights of the same model revision
- **THEN** their `judge_identity` records differ in the precision field

### Requirement: The merge writes a judged result and never edits the pending one
The merge step SHALL write one new judged result JSON beside the pending one in the queue's results folder, from all arms of that result together, and only when every arm's bundle is `SCORED` and the pending result JSON is in that folder. The merge SHALL compute cross-arm RAGAS outputs (leaderboard and A/B metrics) only when every arm has the same `judge_identity`; otherwise it SHALL omit them and record `cross_arm_ragas: refused` with the fields that differ. The judged result SHALL have every arm's per-row metric scores and recomputed `total_results` RAGAS aggregates, the leaderboard and the A/B comparisons, `judge_status: scored`, and each arm's `judge_identity`, `judge_execution` and bundle SHA-256. The merge SHALL NOT change the pending file. Running the merge again on the same scores SHALL give an identical file. The merge SHALL write through a temporary file that it validates and then publishes with an atomic, no-replace `link`, and it SHALL write `MERGED` only after the judged file exists. It SHALL NOT replace an existing judged file, and it SHALL exit non-zero when an existing judged file has different bytes.

#### Scenario: One arm still pending
- **WHEN** a result has two arms, arm 0's bundle is `SCORED`, and arm 1's bundle is still READY
- **THEN** the merge writes no judged file for that result, names arm 1 as pending, and exits 0
- **AND** after arm 1 is `SCORED`, the next merge writes one judged file with both arms

#### Scenario: Pending result JSON not yet published
- **WHEN** every arm of a result is `SCORED`, and the pending result JSON is not yet in the queue's results folder
- **THEN** the merge writes nothing for that result and exits 0

#### Scenario: Arms scored by different judge identities
- **WHEN** arm 0 was scored with `bf16` weights and arm 1 with `fp8` weights, and both are `SCORED`
- **THEN** the judged file has each arm's scores and identity, no RAGAS metric in the leaderboard or the A/B comparisons, and `cross_arm_ragas: refused` naming the precision field

#### Scenario: One arm failed
- **WHEN** one arm's bundle of a result is `FAILED`
- **THEN** the merge writes no judged file for that result, names the failed arm, and exits non-zero

#### Scenario: Two merges compute different bytes at the same time
- **WHEN** two merges of the same result both find no judged file, and they computed different bytes
- **THEN** exactly one `link` succeeds, the other merge exits non-zero, and the judged file holds the first merge's bytes

#### Scenario: Merge is idempotent
- **WHEN** the merge runs twice on the same scores
- **THEN** the two judged files are byte-identical, and the pending file is unchanged

#### Scenario: Merge killed while it writes
- **WHEN** a merge stops after it wrote part of its temporary file
- **THEN** no `<name>.judged.json` and no `MERGED` marker exist
- **AND** the next merge ignores the temporary file, removes it once it is older than 1 hour, and writes the judged file

#### Scenario: Two merges at the same time
- **WHEN** two merges of the same result run at the same time
- **THEN** exactly one judged file exists afterwards, it is complete, and `MERGED` exists

#### Scenario: Conflicting judged file
- **WHEN** a judged file exists and a new merge computes different bytes
- **THEN** the merge exits non-zero and leaves the existing file unchanged

#### Scenario: Digest mismatch is refused
- **WHEN** the scores' bundle SHA-256 differs from the digest recorded in the pending result
- **THEN** the merge exits non-zero and writes no judged file

### Requirement: Comparison refuses pending runs and mixed judges
`compare_runs.py` and the campaign archiver (`scripts/benchmarking/feature_matrix/archive_run.sh`) SHALL refuse a run whose judge status is `pending`, through one shared check. `compare_runs.py` SHALL also refuse to compare RAGAS metrics across arms whose `judge_identity` differs in any field, naming the fields that differ. An artifact with no `judge_identity` SHALL get `(evaluator_provider, evaluator_model)` from its recorded settings, and a missing field SHALL never equal a recorded one.

#### Scenario: Campaign archiver and a pending run
- **WHEN** `archive_run.sh` selects an artifact with `judge_status: pending`
- **THEN** it exits non-zero, names the judged file to wait for, and appends nothing to the campaign ledger

#### Scenario: Pending run
- **WHEN** one arm has `judge_status: pending`
- **THEN** `compare_runs.py` exits non-zero and names the pending arm

#### Scenario: Sonnet arm against Llama arm
- **WHEN** one arm was scored by the inline Sonnet judge and the other by the offline Llama judge
- **THEN** `compare_runs.py` refuses the RAGAS comparison and names both judges

#### Scenario: Same judge across separate jobs
- **WHEN** two offline arms have equal `judge_identity` and different `judge_execution`
- **THEN** `compare_runs.py` compares them

#### Scenario: Two old inline artifacts
- **WHEN** two artifacts written before this change both record `huit_bedrock` and the same Sonnet model
- **THEN** `compare_runs.py` compares them as before
