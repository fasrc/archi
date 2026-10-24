## ADDED Requirements

### Requirement: A benchmark run uses one judge mode for all its configs
The benchmark SHALL read `ragas_settings.judge_mode` from every config of a run before it answers any question, SHALL treat an absent key and `inline` as `inline`, SHALL refuse to start on any value other than `inline` or `deferred`, and SHALL refuse to start when the configs do not all have the same mode, with a message that names each config and its mode.

`base-config.yaml` renders `judge_mode`, `judge_bundle_dir` and `evaluator_model_revision` only when each one is set, so an inline config renders as before.

#### Scenario: Absent and inline are the same mode
- **WHEN** one config of a sweep has no `judge_mode` and another has `judge_mode: inline`
- **THEN** the run starts, inline, and computes no new digest

#### Scenario: Mixed modes
- **WHEN** one config of a sweep has `judge_mode: deferred` and another has no `judge_mode`
- **THEN** the run refuses to start before it answers any question, and the message names both configs

#### Scenario: Invalid mode
- **WHEN** a config has `judge_mode: Deferred`
- **THEN** the run refuses to start, and the message names the value, the config, and the two allowed values

#### Scenario: Unset keys do not render
- **WHEN** a config sets none of `judge_mode`, `judge_bundle_dir` or `evaluator_model_revision`
- **THEN** the rendered `ragas_settings` has none of the three keys

### Requirement: A run fixes its result basename before its first arm
The benchmark SHALL choose its result basename `<benchmark_name>-<UTC timestamp>` once, before its first arm, and SHALL write the result JSON, the `_report.md` and every category-map TSV with that basename.

#### Scenario: One basename for every artifact
- **WHEN** a run with two arms finishes
- **THEN** the result JSON, the `_report.md` and the two `_category_map_<N>.tsv` files all start with the basename that the run chose before its first arm

### Requirement: A deferred run refuses to start when its bundles could never be scored
A run whose mode is `deferred` SHALL refuse to start, before it answers any question, when its `code_version.digest` is unavailable, when the package digest of its environment is unavailable, when a config has an empty `evaluator_model` or `evaluator_model_revision`, when a config's `judge_bundle_dir` is not a relative path inside the output folder, or when its configs do not all resolve to the same `judge_bundle_dir` (an absent key is `judge_bundles`).

#### Scenario: Code digest unavailable
- **WHEN** a deferred run cannot compute `code_version.digest`
- **THEN** it refuses to start, and the message quotes the unavailable source

#### Scenario: No judge revision
- **WHEN** a deferred config has an `evaluator_model` and no `evaluator_model_revision`
- **THEN** the run refuses to start, and the message names the config

#### Scenario: Two bundle folders in one run
- **WHEN** one deferred config has no `judge_bundle_dir` and another has `judge_bundle_dir: x`
- **THEN** the run refuses to start, and the message names both configs and both resolved folders

### Requirement: A deferred arm writes a judge bundle and records a pending judge status
In a deferred run, each arm SHALL write the bundle folder `<judge_bundle_dir>/<basename>-arm<N>` (with `N` the 0-based arm index) under the output folder, with `rows.jsonl`, then `manifest.json`, then `BUNDLE_COMPLETE` last, and it SHALL NOT build or call a judge or an embedding model.

Each `rows.jsonl` line is `{key, user_input, retrieved_contexts, response, reference}`, and for each key it equals the record that the inline path gives to ragas, with the full contexts. The manifest records the result basename, the arm index, the arm count, the row count, the SHA-256 of `rows.jsonl`, the enabled metrics, the output of `ragas_run_config_kwargs`, the `batch_size`, the embedding model, the judge model id and revision, the run's `code_version`, and the package digest. The arm's result records `judge_status: pending` and `judge_bundle` with the folder name and the SHA-256 of `rows.jsonl`. It keeps `ragas_effective_settings` and `ragas_judge_identity_effective` as `null`, and its `total_results` has no RAGAS aggregate and no `<metric>_scored` key. After the result JSON is written, the run creates `<basename>.RESULT_COMPLETE`. An inline arm writes no bundle, no marker, and no new key. A result arm with no `judge_status` reads as `inline`.

#### Scenario: Deferred arm makes no judge call
- **WHEN** a deferred arm completes with 3 scorable rows
- **THEN** its `rows.jsonl` has 3 lines with full contexts, and the judge factory and the embeddings factory were never called

#### Scenario: Bundle rows equal the inline input
- **WHEN** the same answers go through the inline path and the deferred path
- **THEN** each `rows.jsonl` line, without its `key`, equals the record that the inline path passes to ragas for that key

#### Scenario: Arm with no scorable row
- **WHEN** every answer of a deferred arm failed
- **THEN** the arm still writes a bundle, with an empty `rows.jsonl` and `row_count: 0`

#### Scenario: Marker after the result
- **WHEN** a deferred run writes its result JSON
- **THEN** `<basename>.RESULT_COMPLETE` does not exist while `dump_artifacts` runs, and exists after it returns

#### Scenario: Old result reads as inline
- **WHEN** a result JSON written before this change is read
- **THEN** each arm's judge status is `inline`

### Requirement: Exclusive creation never replaces an existing file
The shared helper `link_exclusive(path, data)` SHALL create `path` with the full bytes `data` through a temporary file, `fsync` and a no-replace `link`, SHALL return `True` only to the writer whose bytes own `path`, SHALL NOT replace an existing `path`, and SHALL treat an `EEXIST` from `link` as its own success when its temporary file has a link count of 2.

#### Scenario: Two writers of one name
- **WHEN** two writers call `link_exclusive` on the same name with different bytes
- **THEN** exactly one gets `True`, and the file holds that writer's bytes

#### Scenario: A resent link
- **WHEN** `link` returns `EEXIST` while the writer's temporary file has a link count of 2
- **THEN** the writer gets `True`

### Requirement: The publish script moves complete bundles to a queue folder
`scripts/benchmarking/judge/publish.py` SHALL require a queue folder argument, SHALL copy only bundles that have `BUNDLE_COMPLETE`, SHALL check the copied `rows.jsonl` against the manifest's SHA-256 before it creates `READY` with `link_exclusive`, and SHALL exit non-zero and change nothing for a queue folder that is `READY` with a different digest.

It copies the pending result JSON to `<queue>/results/` only after `<basename>.RESULT_COMPLETE` exists, a complete bundle exists for every arm index of the result, and the file loads with an arm entry for each bundle, whose `judge_bundle.digest` equals the bundle's SHA-256. A missing arm after `RESULT_COMPLETE` is a conflict that names the arm. It copies a judged file back into the output folder only when every bundle of that result has `MERGED`. A second run over the same state changes nothing and exits 0.

#### Scenario: Digest mismatch
- **WHEN** the copied `rows.jsonl` does not match the manifest's SHA-256
- **THEN** the script exits non-zero and the queue folder has no `READY`

#### Scenario: Interrupted publish
- **WHEN** the script stops after it copies `rows.jsonl` and before `READY`
- **THEN** the queue folder has no `READY`, and the next run completes it

#### Scenario: Pending result still being written
- **WHEN** the pending result JSON has no `RESULT_COMPLETE` marker, or does not load
- **THEN** the script does not copy it, and copies it on a later run after both are true
