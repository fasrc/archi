## MODIFIED Requirements

### Requirement: A deferred run writes a judge bundle instead of calling the judge
`ragas_settings.judge_mode` SHALL take one of three values: `inline`, `deferred` and `inline+bundle`. Any other value SHALL stop the run before its first question, with a message that names the value and the three allowed values. When `judge_mode` is `deferred`, the benchmark SHALL answer every question and write a judge bundle for each arm, and it SHALL NOT make any RAGAS judge call. When `judge_mode` is `inline+bundle`, the benchmark SHALL write the arm's judge bundle before it builds the judge or the embeddings, and SHALL then score the arm inline exactly as `inline` does, so that a judge failure after the bundle is written leaves the bundle in place. The bundle has two files. `rows.jsonl` has one line per scorable row, with the row's key and the full `user_input`, `retrieved_contexts`, `response` and `reference` that the inline path gives to ragas. `manifest.json` records the judge mode that wrote it, the enabled metrics, the resolved ragas call settings (the output of `ragas_run_config_kwargs`, including `timeout` and `max_workers`, and the `batch_size` that the inline path passes to `evaluate`), the embedding model, the judge model id and revision (the revision is null when the config has none), the run's `code_version`, the SHA-256 of the run environment's sorted `pip freeze`, the result basename, the arm index, the arm count of the result, and the SHA-256 of `rows.jsonl`. The run SHALL fix the result basename once, before its first arm, and SHALL use it for both the bundles and the result JSON. The bundle writer writes a `BUNDLE_COMPLETE` marker after every other bundle file. When `judge_mode` is absent or `inline`, the run SHALL score inline exactly as before and write no bundle.

#### Scenario: Mixed judge modes in one sweep
- **WHEN** one config of a sweep has `judge_mode: deferred` and another config has no `judge_mode`
- **THEN** the run refuses to start before it answers any question, and names both configs

#### Scenario: Mixed inline+bundle and inline
- **WHEN** one config of a sweep has `judge_mode: inline+bundle` and another config has no `judge_mode`
- **THEN** the run refuses to start before it answers any question, and names both configs

#### Scenario: Invalid mode names all three values
- **WHEN** a config has `judge_mode: Inline+Bundle`
- **THEN** the run refuses to start, and the message names the value, `inline`, `deferred` and `inline+bundle`

#### Scenario: Scorer uses the run's batch size
- **WHEN** a deferred run's config sets `batch_size: 4`
- **THEN** the manifest records `batch_size: 4`, and the offline scorer passes 4 to `evaluate`

#### Scenario: Bundle names the result before the result exists
- **WHEN** a deferred run with two arms writes the bundle of arm 0
- **THEN** the manifest names the result basename, and the result JSON that the run writes at the end has that basename

#### Scenario: Deferred run makes no judge call
- **WHEN** a run with `judge_mode: deferred` completes with 3 scorable rows
- **THEN** the bundle's `rows.jsonl` has 3 lines, and each line has the full contexts, not the 300-character `sources_trunc_content`
- **AND** the judge factory was not called

#### Scenario: Inline+bundle run writes the bundle and scores inline
- **WHEN** a run with `judge_mode: inline+bundle` completes with 3 scorable rows
- **THEN** the bundle's `rows.jsonl` has 3 lines with the full contexts, its manifest records `judge_mode: inline+bundle`
- **AND** the judge factory was called, and the arm's `total_results` has the RAGAS aggregates of the inline path

#### Scenario: Inline+bundle keeps the bundle when the judge fails
- **WHEN** a run with `judge_mode: inline+bundle` builds a judge that raises on its first call
- **THEN** the arm's bundle folder exists with `BUNDLE_COMPLETE` before the judge is built, and the run handles the failure as the inline path does

#### Scenario: Bundle rows equal the inline ragas input
- **WHEN** the same questions and answers go through the inline path and the deferred path
- **THEN** each bundle row's `user_input`, `retrieved_contexts`, `response` and `reference` equals the record that the inline path passes to ragas for that key

#### Scenario: Inline default is unchanged
- **WHEN** a config has no `judge_mode` key
- **THEN** the run scores RAGAS inline and writes no bundle

### Requirement: A deferred result records a pending judge status
A deferred run's result JSON SHALL record `judge_status: pending` and `judge_bundle` (the bundle folder name and the SHA-256 of its `rows.jsonl`) for each deferred arm, and it SHALL NOT record `aggregate_<metric>` values for RAGAS metrics. An `inline+bundle` run's result JSON SHALL record `judge_bundle` for each arm and SHALL NOT record a `judge_status` key, so that the arm reads as `inline` and keeps its inline RAGAS aggregates. A result with no `judge_status` key SHALL be read as `inline`.

#### Scenario: Pending arm carries no RAGAS aggregates
- **WHEN** a deferred arm's result is written
- **THEN** the arm records `judge_status: pending` and the bundle's SHA-256
- **AND** its `total_results` has no `aggregate_faithfulness` key

#### Scenario: Inline+bundle arm is a scored inline arm with a bundle
- **WHEN** an `inline+bundle` arm's result is written
- **THEN** the arm records `judge_bundle` with the folder name and the SHA-256 of `rows.jsonl`, has no `judge_status` key, its judge status reads as `inline`, and its `total_results` has the RAGAS aggregates

#### Scenario: Old artifact reads as inline
- **WHEN** a result JSON written before this change is loaded
- **THEN** its judge status is `inline`

## ADDED Requirements

### Requirement: An inline+bundle run refuses to start when its bundles could never be matched
A run whose mode is `inline+bundle` SHALL refuse to start, before it answers any question, when its `code_version.digest` is unavailable, when the package digest of its environment is unavailable, when a config has an empty `evaluator_model`, when a config's `judge_bundle_dir` is not a relative path inside the output folder, or when its configs do not all resolve to the same `judge_bundle_dir`. It SHALL NOT require `evaluator_model_revision`. The run SHALL write no `RESULT_COMPLETE` marker.

#### Scenario: No revision is fine for inline+bundle
- **WHEN** an `inline+bundle` config has an `evaluator_model` and no `evaluator_model_revision`
- **THEN** the run starts, and the manifest's `judge_model.revision` is null

#### Scenario: Package digest unavailable
- **WHEN** an `inline+bundle` run cannot compute its package digest
- **THEN** it refuses to start, and the message quotes the unavailable source

#### Scenario: No result-complete marker
- **WHEN** an `inline+bundle` run writes its result JSON
- **THEN** no `<result basename>.RESULT_COMPLETE` file exists in the output folder

### Requirement: Publishing copies only deferred bundles
The publish step SHALL copy a complete bundle to the queue only when its manifest's `judge_mode` is `deferred`; a manifest with no `judge_mode` key SHALL be read as `deferred`. A bundle of another mode SHALL be left in place without a conflict.

#### Scenario: Inline+bundle bundle is not queued
- **WHEN** the output folder holds a complete bundle whose manifest records `judge_mode: inline+bundle`
- **THEN** the publish step copies nothing for it, exits 0, and the queue has no folder for it

#### Scenario: Old bundle without the key is queued
- **WHEN** the output folder holds a complete bundle whose manifest has no `judge_mode` key
- **THEN** the publish step copies it to the queue as before

### Requirement: The judge job serves pre-quantized weights without a quantization flag
`JUDGE_PRECISION` in `judge.env` SHALL accept `checkpoint` beside `bf16`, `fp16` and `fp8`. For `checkpoint`, the judge job SHALL pass `--dtype auto` and no `--quantization` flag to `vllm serve`, so that vLLM reads the quantization method from the checkpoint's own configuration, and the judge identity SHALL record the precision `checkpoint`.

#### Scenario: Checkpoint precision emits no quantization flag
- **WHEN** `judge.env` sets `JUDGE_PRECISION=checkpoint` and the job runs with `--dry-run`
- **THEN** the printed `vllm serve` command has `--dtype auto` and no `--quantization`

#### Scenario: Unknown precision is still refused
- **WHEN** `judge.env` sets `JUDGE_PRECISION=int3`
- **THEN** the job refuses before any command runs, and the message names `int3`
