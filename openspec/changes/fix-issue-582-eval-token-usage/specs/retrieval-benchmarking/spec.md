## ADDED Requirements

### Requirement: Each benchmark arm SHALL record the RAGAS judge's token usage
Each `benchmarking_results[*]` entry SHALL carry `judge_usage`, the sum of the tokens every RAGAS judge call reported for that arm in the shared `usage` shape (`input_tokens`, `output_tokens`, `calls`, `unreported_calls`, `by_model`), and SHALL record `judge_usage: null` when no judge ran for the arm.
The judge provider and model in `by_model` SHALL be the same ones the judge model was built from (`ragas_settings.evaluator_provider` and `evaluator_model`, else the benchmark `provider` and `model`). No dollar amount is recorded.

#### Scenario: Judge calls across metrics are summed per arm
- **WHEN** an arm scores two enabled metrics and the judge calls report 200 and 300 input tokens
- **THEN** that arm's `judge_usage.input_tokens` is 500
- **AND** `judge_usage.by_model` names the configured judge provider and model

#### Scenario: A sources-only arm records null
- **WHEN** an arm runs without the RAGAS mode
- **THEN** its entry has `judge_usage` present and `null`

#### Scenario: A later arm does not inherit an earlier arm's usage
- **WHEN** a sweep runs a judged arm and then an arm whose judge makes no call (no metric has an eligible row)
- **THEN** the second arm's `judge_usage` is `null`, not the first arm's totals
