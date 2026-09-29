## ADDED Requirements

### Requirement: A deferred run writes a judge bundle instead of calling the judge
When `ragas_settings.judge_mode` is `deferred`, the benchmark SHALL answer every question and write a judge bundle for each arm, and it SHALL NOT make any RAGAS judge call. The bundle has two files. `rows.jsonl` has one line per scorable row, with the row's key and the full `user_input`, `retrieved_contexts`, `response` and `reference` that the inline path gives to ragas. `manifest.json` records the enabled metrics, `timeout`, `max_workers`, the embedding model, the judge model id and revision, the run's `code_version`, the result file name, the arm index, and the SHA-256 of `rows.jsonl`. When `judge_mode` is absent or `inline`, the run SHALL score inline exactly as before.

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
The publish step SHALL copy the bundle into a folder in the shared queue directory, check each file against the manifest's SHA-256, and create the `READY` marker only after every file is in place.

#### Scenario: Interrupted publish is never READY
- **WHEN** the publish step stops after it copies `rows.jsonl` but before it writes `READY`
- **THEN** the queue folder has no `READY` marker, and the submitter and the judge job ignore it

#### Scenario: Corrupt copy is refused
- **WHEN** the copied `rows.jsonl` does not match the manifest's SHA-256
- **THEN** the publish step exits non-zero and writes no `READY` marker

### Requirement: The submitter keeps at most one judge job
The submitter SHALL submit an `archi-judge` job only when at least one bundle is READY and unclaimed, and no `archi-judge` job of the same user is pending or running.

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
The judge job SHALL claim bundles when it starts, not when it is submitted. It SHALL claim each READY bundle whose manifest names the loaded judge model and revision, through an atomic `mkdir` of the bundle's claim folder. It SHALL leave a bundle for another judge unclaimed.

#### Scenario: Bundle added during the queue wait
- **WHEN** a bundle becomes READY after the job was submitted but before it started
- **THEN** the job claims and scores that bundle

#### Scenario: Two jobs race for one bundle
- **WHEN** two judge jobs try to claim the same bundle
- **THEN** exactly one claim succeeds, and the other job skips the bundle

#### Scenario: Different judge
- **WHEN** a READY bundle names a judge model that the job did not load
- **THEN** the job does not claim it, and the bundle stays READY

### Requirement: The judge job saves each row's score and resumes after a requeue
The judge job SHALL append each scored `(key, metric)` result to the bundle's `scores.partial.jsonl` as soon as it is available. After a restart, it SHALL skip the pairs that are already recorded.

#### Scenario: Requeue in the middle of a bundle
- **WHEN** a job is killed after it scored 40 of 109 rows for a metric, and Slurm requeues it
- **THEN** the requeued job scores only the 69 rows that remain for that metric
- **AND** the final scores have exactly one value for each `(key, metric)`

### Requirement: The judge job stops claiming when its time budget is low
Before it claims another bundle, the judge job SHALL compare the time left in its allocation with its measured time per row multiplied by the bundle's row count. If the time left is less, it SHALL leave the bundle READY for a later job.

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

### Requirement: The merge writes a judged result and never edits the pending one
The merge step SHALL write a new judged result JSON next to the pending one. The judged result SHALL have the per-row metric scores, the recomputed `total_results` RAGAS aggregates, the leaderboard and the A/B comparisons, `judge_status: scored`, and a `judge_identity` (model id, revision, vLLM image, job id, bundle SHA-256). The merge SHALL NOT change the pending file. Running the merge again on the same scores SHALL give an identical file.

#### Scenario: Merge is idempotent
- **WHEN** the merge runs twice on the same scores
- **THEN** the two judged files are byte-identical, and the pending file is unchanged

#### Scenario: Digest mismatch is refused
- **WHEN** the scores' bundle SHA-256 differs from the digest recorded in the pending result
- **THEN** the merge exits non-zero and writes no judged file

### Requirement: Comparison refuses pending runs and mixed judges
`compare_runs.py` SHALL refuse a run whose judge status is `pending`, and it SHALL refuse to compare RAGAS metrics across arms whose judge identity differs. An inline run's judge identity is its recorded `evaluator_provider` and `evaluator_model`.

#### Scenario: Pending run
- **WHEN** one arm has `judge_status: pending`
- **THEN** `compare_runs.py` exits non-zero and names the pending arm

#### Scenario: Sonnet arm against Llama arm
- **WHEN** one arm was scored by the inline Sonnet judge and the other by the offline Llama judge
- **THEN** `compare_runs.py` refuses the RAGAS comparison and names both judges
