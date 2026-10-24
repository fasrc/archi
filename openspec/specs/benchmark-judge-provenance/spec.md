# benchmark-judge-provenance Specification

## Purpose
What a benchmark result records about the RAGAS judge of each arm (its effective settings and its identity), and how those records decide whether arms can be ranked against each other. Created by archiving fix-issue-518-unjudged-ragas-settings (#518) and fix-issue-618-judge-identity-ranks (#618).
## Requirements
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

### Requirement: The benchmark record SHALL carry the effective RAGAS judge identity
Each arm's record SHALL carry `ragas_judge_identity_effective`, the judge identity of the arm: the `provider`, `model`, `base_url`, and `mode` that `get_ragas_llm_evaluator` used to build the judge (`mode` is `None` unless the provider is `local`), extended with `model_revision`, `precision`, `server_image_digest`, `server_args`, `decoding`, `timeout`, `max_workers`, `batch_size`, `chunk_size`, `embedding`, `metrics`, and `scorer`. The value SHALL be `None` when `ragas_effective_settings` is `None`. The four fields of #618 SHALL come from the same computation that selects the judge client, not from a second reading of the configuration, and the `decoding` and `embedding` fields SHALL come from the built judge and embedding objects. An inline judge SHALL record `None` for `model_revision`, `precision`, `server_image_digest`, `server_args`, and `chunk_size`, and its `scorer` SHALL describe the benchmark image. The identity SHALL hold no value of one job (job id, node, times); those go to `judge_execution`.

#### Scenario: A local judge records its resolved client mode
- **WHEN** a judged arm uses provider `local` and the client mode resolves to `openai_compat`
- **THEN** the record's `ragas_judge_identity_effective["mode"]` is `"openai_compat"`

#### Scenario: A non-local judge records no mode
- **WHEN** a judged arm uses provider `anthropic`
- **THEN** the record's `ragas_judge_identity_effective["mode"]` is `None`

#### Scenario: An unjudged arm records no identity
- **WHEN** RAGAS was a mode but `ragas_scored` is `False`
- **THEN** the record's `ragas_judge_identity_effective` is `None`

#### Scenario: An inline API judge records the extended shape
- **WHEN** an inline arm is judged by an API judge
- **THEN** its `ragas_judge_identity_effective` has every extended field, with `model_revision` and `precision` equal to `None` and `scorer` holding the benchmark image's code digest and package digest

### Requirement: A sweep scored by different RAGAS judges SHALL withhold its ranking
`ResultHandler.arms_incomparability_reason` SHALL return a reason that names the judge and the identity fields that differ when the records whose `ragas_judge_identity_effective` is present and not `None` hold more than one distinct identity. It SHALL compare identities by one shared canonical JSON form with sorted keys, in which an integral float equals the integer, so a nested value (a list or a dict) never raises. A record without the key, or with `None`, SHALL be skipped by this check.

#### Scenario: Judge client mode differs
- **WHEN** two records are identical except that `ragas_judge_identity_effective["mode"]` is `"ollama"` in one and `"openai_compat"` in the other
- **THEN** `arms_comparable(records)` is `False` and the reason contains `"judge"`

#### Scenario: Judge provider differs
- **WHEN** two records are identical except for `ragas_judge_identity_effective["provider"]`
- **THEN** `arms_comparable(records)` is `False` and the reason contains `"judge"`

#### Scenario: Old records still rank
- **WHEN** two records lack the `ragas_judge_identity_effective` key and match in every other checked field
- **THEN** `arms_comparable(records)` is `True`

#### Scenario: Nested identity values
- **WHEN** two records hold equal identities with nested values (the metric list and the embedding)
- **THEN** `arms_incomparability_reason(records)` is `None`, with no error

#### Scenario: Only the precision differs
- **WHEN** two records' identities differ only in `precision`
- **THEN** the reason contains `precision`, and it does not say "client mode"

#### Scenario: Equal numbers of different types
- **WHEN** one identity records `temperature: 0` and the other `temperature: 0.0`, with all other fields equal
- **THEN** `arms_comparable(records)` is `True`

### Requirement: The leaderboard shared context SHALL report judge-identity drift
`build_leaderboard` SHALL include the judge identity in its shared context, in the shared canonical JSON form, and SHALL add a warning that names `judge_identity` when the judged records hold more than one identity.

#### Scenario: Drift is reported
- **WHEN** two judged records carry different `ragas_judge_identity_effective` values
- **THEN** `shared_context["warnings"]` contains a warning that names `judge_identity`

#### Scenario: Equal numbers are not drift
- **WHEN** two judged records carry identities that differ only as `0` against `0.0`
- **THEN** `shared_context["warnings"]` contains no warning that names `judge_identity`

