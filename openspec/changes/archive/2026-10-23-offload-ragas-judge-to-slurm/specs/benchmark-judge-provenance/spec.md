## MODIFIED Requirements

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
