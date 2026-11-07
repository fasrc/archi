## ADDED Requirements

### Requirement: The judge's own local mode is reachable through the CLI render

The rendered config SHALL carry a configured `evaluator_provider_mode` through to the RAGAS judge.

The RAGAS judge reads its own local mode from
`services.benchmarking.mode_settings.ragas_settings.evaluator_provider_mode` and falls back
to the system-under-test's `provider_mode` only when the judge's key is absent or empty. A
key nothing renders is a key that is always absent, so the judge inherited the SUT's mode
on every CLI-deployed benchmark and a judge-specific mode could be neither honoured nor
refused. The render carries the key so the documented judge/SUT split is reachable.

The key is emitted **by presence, not by truthiness**, and serialized with `tojson` rather
than interpolated bare — the same two rules the system-under-test's `provider_mode` uses,
for the same two reasons. A truthiness guard drops a configured `false` and `0`; a bare
interpolation lets YAML 1.1 retype the operator's string before any validator reads it.

#### Scenario: A configured judge mode reaches the rendered config

- **WHEN** the operator sets the judge's `evaluator_provider_mode` to `openai_compat`
- **THEN** the rendered config carries `evaluator_provider_mode` of `openai_compat`

#### Scenario: A judge mode of false survives the render

- **WHEN** the operator sets the judge's `evaluator_provider_mode` to `false`
- **THEN** the rendered config carries the boolean `false` rather than omitting the key

#### Scenario: A judge mode of zero survives the render

- **WHEN** the operator sets the judge's `evaluator_provider_mode` to `0`
- **THEN** the rendered config carries the integer `0` rather than omitting the key

#### Scenario: A rendered false judge mode is refused rather than inherited

- **WHEN** a rendered judge mode of `false` reaches `resolve_local_mode`
- **THEN** it raises `ValueError` instead of the judge inheriting the SUT's mode

#### Scenario: A YAML-special judge mode string keeps its string type

- **WHEN** the operator sets the judge's `evaluator_provider_mode` to `"null"`, `"Null"`, `"NULL"`, `"~"`, `"on"`, `"off"`, `"yes"` or `"no"`
- **THEN** the rendered config carries that value as the string the operator wrote

#### Scenario: A YAML-special judge mode string is refused rather than auto-detected

- **WHEN** a rendered judge mode of `"null"` reaches `resolve_local_mode`
- **THEN** it raises `ValueError` instead of auto-detecting a dialect from the URL

#### Scenario: An absent judge mode leaves the key out of the rendered config

- **WHEN** the operator sets no `evaluator_provider_mode`
- **THEN** the rendered config has no `evaluator_provider_mode` key

#### Scenario: A null judge mode leaves the key out of the rendered config

- **WHEN** the operator writes `evaluator_provider_mode` with no value
- **THEN** the rendered config has no `evaluator_provider_mode` key

#### Scenario: An empty judge mode still inherits the system-under-test's mode

- **WHEN** the operator sets the judge's `evaluator_provider_mode` to the empty string
- **THEN** the rendered config carries the empty string and the judge inherits the SUT's mode
