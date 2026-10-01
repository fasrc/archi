## ADDED Requirements

### Requirement: Run detail SHALL hide the answer key from callers who cannot manage evaluations
The `/api/evaluations/runs/<history_id>` response SHALL omit oracle truth, gold atoms, and raw agent answers when `_can_manage()` is false, SHALL include them when `_can_manage()` is true, and SHALL name every omitted field in one constant in `src/evaluation/qa/run_visibility.py`.
Before this change any holder of `Permission.Evaluations.VIEW` could read the approved answers, gold atoms, live-oracle observations, and agent answers of every run, which breaks benchmark isolation. The split reuses the job-detail pattern `include_hidden=_can_manage()` and adds no permission. The omitted fields are: in `preparation` and `prepared_items` rows, `answer`, `answer_sha256`, `gold_atoms`, `oracle`, `oracle_metadata`, `oracle_calls`; in `live_checks` rows, `answer`, `answer_sha256`, `metadata`, `calls`; in `answers` and `evaluation_results` rows, `answer`; in `evaluation_results` judgments, `rationale`. The console service's `get_run` SHALL default to the viewer form.

#### Scenario: A VIEW-only caller gets scores without the answer key
- **WHEN** a logged-in caller with `Permission.Evaluations.VIEW` and without `Permission.Evaluations.MANAGE` requests a scored run's detail
- **THEN** the response keeps the manifest, metadata, summary, item IDs, statuses, questions, and judgment outcomes
- **AND** no row in the response carries any field that the constant names

#### Scenario: A manager gets the full run
- **WHEN** a logged-in caller with `Permission.Evaluations.MANAGE` requests the same run's detail
- **THEN** the response carries every field of `EvaluationHistory.get_run`, unchanged

#### Scenario: Auth-off deployments keep the full run
- **WHEN** the deployment runs with auth disabled and an anonymous request asks for run detail
- **THEN** the response carries every field, because `_can_manage()` is true without a session

#### Scenario: The service fails closed
- **WHEN** code calls the console service's `get_run` without `include_hidden`
- **THEN** it returns the viewer form

#### Scenario: Report and list do not change
- **WHEN** a VIEW-only caller and a manager request the run list and a run's report
- **THEN** both receive the same responses as before this change

#### Scenario: The resolved agent config is never served
- **WHEN** any caller requests run detail
- **THEN** the response does not contain the content of `agent_config.resolved.yaml`
