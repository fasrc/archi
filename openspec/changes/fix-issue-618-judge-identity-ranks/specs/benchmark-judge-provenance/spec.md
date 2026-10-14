## ADDED Requirements

### Requirement: The benchmark record SHALL carry the effective RAGAS judge identity
Each arm's record SHALL carry `ragas_judge_identity_effective`, the judge identity that `get_ragas_llm_evaluator` used to build the judge: `provider`, `model`, `base_url`, and `mode` (`mode` is `None` unless the provider is `local`). The value SHALL be `None` when `ragas_effective_settings` is `None`. The identity SHALL come from the same computation that selects the judge client, not from a second reading of the configuration.

#### Scenario: A local judge records its resolved client mode
- **WHEN** a judged arm uses provider `local` and the client mode resolves to `openai_compat`
- **THEN** the record's `ragas_judge_identity_effective["mode"]` is `"openai_compat"`

#### Scenario: A non-local judge records no mode
- **WHEN** a judged arm uses provider `anthropic`
- **THEN** the record's `ragas_judge_identity_effective["mode"]` is `None`

#### Scenario: An unjudged arm records no identity
- **WHEN** RAGAS was a mode but `ragas_scored` is `False`
- **THEN** the record's `ragas_judge_identity_effective` is `None`

### Requirement: A sweep scored by different RAGAS judges SHALL withhold its ranking
`ResultHandler.arms_incomparability_reason` SHALL return a reason that names the judge when the records whose `ragas_judge_identity_effective` is present and not `None` hold more than one distinct identity. A record without the key, or with `None`, SHALL be skipped by this check.

#### Scenario: Judge client mode differs
- **WHEN** two records are identical except that `ragas_judge_identity_effective["mode"]` is `"ollama"` in one and `"openai_compat"` in the other
- **THEN** `arms_comparable(records)` is `False` and the reason contains `"judge"`

#### Scenario: Judge provider differs
- **WHEN** two records are identical except for `ragas_judge_identity_effective["provider"]`
- **THEN** `arms_comparable(records)` is `False` and the reason contains `"judge"`

#### Scenario: Old records still rank
- **WHEN** two records lack the `ragas_judge_identity_effective` key and match in every other checked field
- **THEN** `arms_comparable(records)` is `True`

### Requirement: The leaderboard shared context SHALL report judge-identity drift
`build_leaderboard` SHALL include the judge identity in its shared context, and SHALL add a warning that names `judge_identity` when the judged records hold more than one identity.

#### Scenario: Drift is reported
- **WHEN** two judged records carry different `ragas_judge_identity_effective` values
- **THEN** `shared_context["warnings"]` contains a warning that names `judge_identity`
