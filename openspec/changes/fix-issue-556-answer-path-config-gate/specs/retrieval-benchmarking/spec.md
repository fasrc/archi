## ADDED Requirements

### Requirement: The paired comparison tool SHALL refuse arms whose recorded answer-path settings differ
The benchmark comparison tool SHALL read `services.chat_app.context_editing` and `services.chat_app.recursion_limit` from every arm's recorded `configuration`, SHALL refuse the comparison with the gate exit code (2) when any arm's recorded value for either path differs from another arm's, SHALL treat a path that is absent from a recorded configuration as a value distinct from a recorded null, and SHALL name every differing path and each arm's recorded value in the refusal.
The in-loop context bound and the recursion limit decide which questions the agent can finish at all, so a delta between arms that differ in them measures the configuration rather than the change under test. On 2026-09-19 a golden-set run with `context_editing: null` lost 6 of 109 questions to context overflow against a baseline that carried the 32768-token bound, and no gate caught it: `divergence_from_selected_file` compares a run with its own selected file, never one arm with another. The gate is G10, "one answer path", and it runs on the compared arms after the Procedure E divergence gate. It compares what the artifacts recorded, by canonical JSON rendering, not what a value might mean at runtime.

#### Scenario: The in-loop bound is present in one arm and absent in the other
- **WHEN** two arms are compared and one records `services.chat_app.context_editing` as `{"trigger": 32768, "keep": 1}` while the other's recorded configuration has no `context_editing` key
- **THEN** the tool exits with the gate exit code (2)
- **AND** the refusal names `G10` and `services.chat_app.context_editing`
- **AND** the refusal prints the bound's recorded value and the word `absent` for the arm that lacks it

#### Scenario: A recorded null is a difference from the bound
- **WHEN** two arms are compared and one records the bound while the other records `services.chat_app.context_editing: null`
- **THEN** the tool exits with the gate exit code (2)
- **AND** the refusal prints `null` for the arm that recorded it and the bound's value for the other

#### Scenario: The recursion limit differs
- **WHEN** two arms record `services.chat_app.recursion_limit` as `50` and `25` and are otherwise identical
- **THEN** the tool exits with the gate exit code (2)
- **AND** the refusal names `services.chat_app.recursion_limit` with both values

#### Scenario: Every differing path is named
- **WHEN** two arms differ in both `services.chat_app.context_editing` and `services.chat_app.recursion_limit`
- **THEN** the refusal names both paths with each arm's value, not only the first one found

#### Scenario: Identical answer-path settings pass
- **WHEN** every arm records the same value for both refused paths
- **THEN** the comparison proceeds
- **AND** the gate table carries a row with id `G10`, name `one answer path` and status `pass` whose detail lists each refused path's per-arm values
- **AND** the JSON report's `gates` list carries the same row

#### Scenario: Artifacts that recorded an empty configuration are unaffected
- **WHEN** every arm records `configuration: {}`
- **THEN** every refused path is absent in every arm, so the values are equal and the gate passes
- **AND** the existing comparison tests that build such artifacts keep passing without change

### Requirement: The tool SHALL waive one named answer-path difference per `--config-differs-by-design` value without hiding it
The benchmark comparison tool SHALL accept a repeatable `--config-differs-by-design <dotted.path>` flag whose value is one of the two refused paths, SHALL let a named path differ without refusing, SHALL report the gate row as `OVERRIDDEN (--config-differs-by-design <path>)` naming each path that was both named and differed while still printing every arm's value, SHALL keep refusing a differing path that was not named, and SHALL exit with the usage exit code (1) when a value is not one of the refused paths.
The flag mirrors `--corpus-differs-by-design`: it is the operator's declaration that the difference is the treatment, and the report records the declaration next to both values so a reader can see what was waived. A value outside the refused list can waive nothing, so accepting it silently would let a mistyped path look like an override; the reported path `services.benchmarking.agent_md_file` is not accepted either, because it is never refused.

#### Scenario: The named path is waived and both values stay visible
- **WHEN** two arms differ only in `services.chat_app.context_editing` and the tool runs with `--config-differs-by-design services.chat_app.context_editing`
- **THEN** the tool exits 0
- **AND** the `G10` row's status begins with `OVERRIDDEN` and names `--config-differs-by-design services.chat_app.context_editing`
- **AND** the row's detail prints each arm's recorded value for the path, including `absent` where the key is missing

#### Scenario: Naming a different path does not waive the one that differs
- **WHEN** two arms differ only in `services.chat_app.recursion_limit` and the tool runs with `--config-differs-by-design services.chat_app.context_editing`
- **THEN** the tool exits with the gate exit code (2)
- **AND** the refusal names `services.chat_app.recursion_limit`
- **AND** the same pair run with `--config-differs-by-design services.chat_app.recursion_limit` exits 0

#### Scenario: One of two differing paths is named
- **WHEN** two arms differ in both refused paths and only one path is named
- **THEN** the tool exits with the gate exit code (2)
- **AND** the refusal names the path that was not waived

#### Scenario: A named path that does not differ changes nothing
- **WHEN** every arm records the same answer-path values and the tool runs with `--config-differs-by-design services.chat_app.context_editing`
- **THEN** the `G10` row's status is `pass`

#### Scenario: A value outside the refused list is a usage error
- **WHEN** the tool runs with `--config-differs-by-design services.chat_app.default_model` or `--config-differs-by-design services.benchmarking.agent_md_file`
- **THEN** the tool exits with the usage exit code (1)
- **AND** the message lists the two accepted paths

### Requirement: The tool SHALL report a differing `services.benchmarking.agent_md_file` and SHALL NOT refuse on it
The benchmark comparison tool SHALL read `services.benchmarking.agent_md_file` from every arm's recorded configuration, SHALL print each arm's value in the `G10` row's detail when the values differ, and SHALL NOT change the gate's status or refuse the comparison because of it.
Prompt arms vary the agent instructions file on purpose; the difference is the treatment, and the report must show it without treating it as a defect.

#### Scenario: Prompt arms differ only in the instructions file
- **WHEN** two arms record identical refused paths and `services.benchmarking.agent_md_file` values `prompts/a.md` and `prompts/b.md`
- **THEN** the tool exits 0 and the `G10` row's status is `pass`
- **AND** the row's detail names `agent_md_file` with both values

#### Scenario: An identical instructions file is not mentioned
- **WHEN** every arm records the same `services.benchmarking.agent_md_file`, or none records it
- **THEN** the `G10` row's detail does not mention `agent_md_file`

### Requirement: The tool SHALL refuse an arm whose configuration was not recorded unless every refused path is waived
The benchmark comparison tool SHALL treat an arm whose artifact entry has no `configuration` mapping — the key missing, or recorded as null or a non-mapping — as not recorded, SHALL print `not recorded` for that arm, SHALL refuse the comparison with the gate exit code (2) even when every compared arm is unrecorded, and SHALL waive the refusal only when every refused path is named in `--config-differs-by-design`.
This is the G3 rule for an unrecorded corpus fingerprint applied to the configuration: the artifact cannot show what the arm ran with, and two arms that both hide it are not thereby equal. An unrecorded configuration hides every path at once, so only a waiver of every path covers it.

#### Scenario: Two arms without a configuration refuse
- **WHEN** two arms are compared and neither artifact entry carries a `configuration` key
- **THEN** the tool exits with the gate exit code (2)
- **AND** the refusal names `G10` and says `not recorded`

#### Scenario: One recorded arm and one unrecorded arm refuse
- **WHEN** one arm records a configuration and the other's entry has no `configuration` key
- **THEN** the tool exits with the gate exit code (2)
- **AND** the refusal says `not recorded` next to the unrecorded arm's label

#### Scenario: A null configuration is not recorded
- **WHEN** an arm's entry records `configuration: null`
- **THEN** the tool treats it exactly as a missing key

#### Scenario: Waiving every refused path accepts an unrecorded arm
- **WHEN** two unrecorded arms are compared with both `--config-differs-by-design services.chat_app.context_editing` and `--config-differs-by-design services.chat_app.recursion_limit`
- **THEN** the tool exits 0
- **AND** the `G10` row's status begins with `OVERRIDDEN` and its detail says `not recorded`
- **AND** the same pair with only one of the two paths named exits with the gate exit code (2)

### Requirement: The gate SHALL be named where the comparison tool's gates are listed
The comparison tool's module docstring and the benchmark interpretation guide SHALL describe the G10 answer-path gate, its two refused paths, the `--config-differs-by-design` flag, and that `services.benchmarking.agent_md_file` is reported rather than refused.
The guide is what a person reads before trusting a comparison; a gate the guide does not name is a gate a reader cannot expect, and a flag the guide does not list is one they cannot know to use.

#### Scenario: The docstring lists the gate
- **WHEN** a reader opens `scripts/benchmarking/compare_runs.py`
- **THEN** the module docstring has a bullet for G10 alongside the G3–G8 and Procedure E bullets
- **AND** the `Exit codes` line is unchanged

#### Scenario: The interpretation guide lists the gate and the flag
- **WHEN** a reader opens the Procedure C section of `docs/docs/interpreting_benchmark_results.md`
- **THEN** the implements sentence covers G10, the flag table has a `--config-differs-by-design` row, the "What it refuses" list has a bullet for differing answer-path settings, and the Gap 1 paragraph names the refusal
- **AND** no new heading or anchor was added

### Requirement: The noise-replicate check SHALL be unchanged by the answer-path gate
The answer-path gate SHALL run only on the compared arms, and the noise-replicate check SHALL keep its existing rules — one bank, one corpus, one `code_version.digest` and one `config_version.digest` among the replicates, with the baseline outside the identity check — without a new answer-path comparison.
Replicates that recorded different answer-path settings already record different `config_version.digest` values and are already refused. The issue leaves this check unchanged unless a test shows a gap, and none does.

#### Scenario: Existing replicate refusals still hold
- **WHEN** the noise-replicate tests that exist at the base commit run
- **THEN** each passes unchanged
- **AND** `--noise-runs` replicates are not compared for answer-path settings against the baseline
