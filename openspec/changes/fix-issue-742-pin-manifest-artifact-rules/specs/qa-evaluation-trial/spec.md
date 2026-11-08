## ADDED Requirements

### Requirement: Tests pin the artifact rules of the run-manifest validator
The unit tests SHALL fail when `RunManifest.from_dict` accepts a `qa-v2` manifest with status `attention_required`, `run_completed` or `scored` that has no `live_checks.jsonl` artifact, or a `scored` manifest that lacks `summary.json` or `report.md`.
Each test asserts the exact `ValueError` message, and first asserts that the unchanged document is accepted.

#### Scenario: A scored manifest without its live-check artifact is refused
- **GIVEN** the `qa-v2` manifest of a real run that ends `scored`, which `RunManifest.from_dict` accepts
- **WHEN** `live_checks.jsonl` is removed from its `artifacts`
- **THEN** `RunManifest.from_dict` raises `ValueError` with the message `qa-v2 manifest is missing live-check artifacts`

#### Scenario: A scored manifest without a result artifact is refused
- **GIVEN** the `qa-v2` manifest of a real run that ends `scored`, which `RunManifest.from_dict` accepts
- **WHEN** `summary.json`, or in a second case `report.md`, is removed from its `artifacts`
- **THEN** `RunManifest.from_dict` raises `ValueError` with the message `scored manifest is missing result artifacts`

#### Scenario: The live-check rule covers run_completed and attention_required
- **GIVEN** the scored manifest edited to `run_completed` (no `score` phase, no `SCORE_FILES` artifacts), or edited to `attention_required`, which `RunManifest.from_dict` accepts
- **WHEN** `live_checks.jsonl` is removed from its `artifacts`
- **THEN** `RunManifest.from_dict` raises `ValueError` with the message `qa-v2 manifest is missing live-check artifacts`

#### Scenario: The real first-run pause manifest needs its live-check artifact
- **GIVEN** the `attention_required` manifest that a real first run writes at the live-mismatch gate, which `RunManifest.from_dict` accepts
- **WHEN** `live_checks.jsonl` is removed from its `artifacts`
- **THEN** `RunManifest.from_dict` raises `ValueError` with the message `qa-v2 manifest is missing live-check artifacts`

#### Scenario: Disabling a rule fails exactly its tests
- **WHEN** a throwaway copy disables the live-check condition and runs `TestValidatorFence`
- **THEN** exactly the 4 live-check cases fail
- **AND** when a second copy disables only the scored condition, exactly the `summary.json` and `report.md` cases fail

#### Scenario: The validator source does not change
- **WHEN** the change is complete
- **THEN** `git diff origin/dev -- src/evaluation/qa/schema.py` prints nothing
