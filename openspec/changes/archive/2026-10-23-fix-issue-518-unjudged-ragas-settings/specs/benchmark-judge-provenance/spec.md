## ADDED Requirements

### Requirement: The benchmark record SHALL claim judge settings only for an arm the judge scored

`ResultHandler.handle_results` MUST write `ragas_effective_settings: null` when `RAGAS` is among the executed modes but the judge scored nothing for the arm (`ragas_scored=False`). When the judge scored the arm, or when the caller does not pass `ragas_scored`, the record MUST keep the effective settings (`timeout`, `max_workers`) as before. The configuration digest MUST NOT change, because `modes_executed` still contains `RAGAS`.

#### Scenario: RAGAS arm with no scorable input

- **WHEN** a RAGAS-mode arm has an empty `ragas_input`, so `_process_config` does not call `get_ragas_results`
- **THEN** the arm's record has `ragas_effective_settings` equal to `None`

#### Scenario: RAGAS arm with input but no eligible row

- **WHEN** a RAGAS-mode arm has a non-empty `ragas_input`, but no enabled metric has an eligible row (for example, only `context_recall` is enabled and every row is a draft with no reference), so `get_ragas_results` never calls the judge
- **THEN** the arm's record has `ragas_effective_settings` equal to `None`

#### Scenario: RAGAS arm the judge scored

- **WHEN** at least one enabled metric calls the judge for a RAGAS-mode arm
- **THEN** the arm's record has `ragas_effective_settings` with the effective `timeout` and `max_workers`

#### Scenario: Caller that does not pass ragas_scored

- **WHEN** `handle_results` is called with `modes_executed={"RAGAS"}` and no `ragas_scored` argument
- **THEN** the record has the effective settings, the same as before this change

#### Scenario: Digest of an unjudged arm

- **WHEN** a RAGAS-mode arm is recorded with `ragas_scored=False`
- **THEN** its `config_version` digest equals the digest of the same arm recorded with `ragas_scored` omitted

### Requirement: A sweep with an unjudged arm SHALL withhold its ranking

When one arm of a sweep was judged and another was not, `ResultHandler.arms_incomparability_reason` MUST return the judge-pressure reason ("... or one arm was not judged ..."), and the leaderboard MUST NOT rank the arms.

#### Scenario: One judged and one unjudged arm

- **WHEN** a sweep holds one record with effective judge settings and one record with `ragas_effective_settings: null`
- **THEN** `arms_incomparability_reason` returns the reason that names different judge pressure
