## ADDED Requirements

### Requirement: Sweep config generation from a manifest

A sweep-config generator SHALL take a manifest naming one base benchmarking config and a list of prompt files, and write one rendered config per prompt into a sweep directory. Each generated config MUST be identical to the base except for `services.benchmarking.agent_md_file` (set to that prompt's path) and `services.benchmarking.name` (set to the prompt's filename stem). The generator MUST NOT alter any other field, so that model, provider, queries, retriever, and RAGAS judge are provably identical across all variants.

#### Scenario: One config per prompt

- **WHEN** the generator runs with a manifest listing a base config and three prompt files
- **THEN** exactly three config files are written into the sweep directory
- **AND** each one's `services.benchmarking.agent_md_file` equals one of the three prompt paths
- **AND** every other key in each generated config equals the base config's value

#### Scenario: Variant name derives from the prompt stem

- **WHEN** a prompt file `fasrc-cannon-v2-lean.md` is included in the sweep
- **THEN** the generated config sets `services.benchmarking.name` to `fasrc-cannon-v2-lean`

#### Scenario: Missing prompt file is rejected before any config is written

- **WHEN** the manifest lists a prompt path that does not exist on disk
- **THEN** the generator raises an error naming the missing path
- **AND** no partial set of configs is left in the sweep directory

### Requirement: Leaderboard aggregation over swept variants

After a multi-config run, the harness SHALL build a leaderboard that contains one row per config, where each row reports the variant `name`, its `agent_md_file`, and its four mean RAGAS metrics (`answer_relevancy`, `faithfulness`, `context_precision`, `context_recall`) read from that config's `total_results` aggregates. The leaderboard MUST be computed from per-config aggregates only and MUST NOT depend on the pairwise A/B comparison data.

#### Scenario: One row per swept config

- **WHEN** a run completes with three configs in `ResultHandler.results`
- **THEN** the leaderboard contains exactly three rows
- **AND** each row carries the variant `name`, its `agent_md_file`, and the four mean RAGAS metrics for that config

#### Scenario: Row name falls back to the prompt stem, not a config index

- **WHEN** a config has no `services.benchmarking.name`
- **THEN** its leaderboard row `name` is the `agent_md_file` filename stem
- **AND** the row `name` is never of the form `config_<index>`

#### Scenario: Leaderboard is independent of pairwise comparisons

- **WHEN** the leaderboard is built
- **THEN** it is derived solely from each config's `total_results` aggregates
- **AND** the existing `ab_comparison` / `ab_comparisons` outputs are produced unchanged alongside it

### Requirement: Ranking by a configurable primary metric

The leaderboard SHALL rank rows in descending order of a configurable `primary_metric`, defaulting to `faithfulness` when the manifest does not specify one. Tied scores MUST share the same rank. Every row MUST retain all four metric values regardless of which metric is primary.

#### Scenario: Default ranking by faithfulness

- **WHEN** no `primary_metric` is configured
- **THEN** rows are ordered by descending mean `faithfulness`
- **AND** the row with the highest `faithfulness` is assigned `rank` 1

#### Scenario: Configured primary metric overrides the default

- **WHEN** the manifest sets `primary_metric: answer_relevancy`
- **THEN** rows are ordered by descending mean `answer_relevancy`
- **AND** each row still contains all four metric values

#### Scenario: Ties share a rank

- **WHEN** two variants have an equal primary-metric value
- **THEN** they are assigned the same `rank`

### Requirement: Incomplete variants are surfaced and sorted last

When a variant produced no value for a metric (the aggregate key is absent or its value is NaN), the leaderboard MUST store `None` for that metric and mark the row `incomplete: true`. Incomplete rows MUST sort after all complete rows regardless of their primary-metric value, and the condition MUST be logged. Missing metrics MUST NOT be silently treated as zero.

#### Scenario: NaN metric marks the row incomplete

- **WHEN** a variant's `aggregate_faithfulness` is NaN
- **THEN** that row's `faithfulness` is `None`
- **AND** the row is marked `incomplete: true`

#### Scenario: Incomplete variant cannot rank first

- **WHEN** a complete variant and an incomplete variant are both present
- **THEN** the complete variant ranks ahead of the incomplete one
- **AND** the incomplete variant appears after all complete variants

### Requirement: Self-describing shared run context

The leaderboard SHALL record, once, the run context shared by all variants: `model`, `provider`, `evaluator_model`, `queries_path`, and `corpus_snapshot_id`. If any of `model`, `provider`, `evaluator_model`, or `queries_path` differs across the swept configs, the leaderboard MUST record the discrepancy in `shared_context.warnings` and log it, while still emitting the leaderboard.

#### Scenario: Shared context captured for an apples-to-apples sweep

- **WHEN** all configs share the same model, provider, judge model, and queries file
- **THEN** the leaderboard `shared_context` records those values once
- **AND** it includes the `corpus_snapshot_id`
- **AND** `shared_context.warnings` is empty

#### Scenario: Drifted config raises a warning but still ranks

- **WHEN** one config's `model` differs from the others
- **THEN** the discrepancy is recorded in `shared_context.warnings`
- **AND** the leaderboard rows are still emitted

### Requirement: Leaderboard emitted only for multi-variant runs

The leaderboard SHALL be written into the benchmark dump JSON under a `leaderboard` key only when two or more configs were run. A single-config run MUST NOT emit a `leaderboard` key, preserving today's output for ordinary evaluations.

#### Scenario: Multi-config run emits a leaderboard

- **WHEN** two or more configs are run in one invocation
- **THEN** the dump JSON contains a `leaderboard` key

#### Scenario: Single-config run emits no leaderboard

- **WHEN** exactly one config is run
- **THEN** the dump JSON contains no `leaderboard` key
- **AND** the output is otherwise identical to today's single-config dump
