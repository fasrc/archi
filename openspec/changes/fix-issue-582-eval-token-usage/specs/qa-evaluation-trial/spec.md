## ADDED Requirements

### Requirement: QA run artifacts SHALL record the LLM token usage of every evaluator and tested-agent call
Every QA row that made an LLM call SHALL carry a `usage` object that sums the input and output tokens each call reported, attributed per `(provider, model)` in a `by_model` list, and SHALL count calls that reported no usage in `unreported_calls` instead of recording them as zero tokens.
The rows are `preparation.jsonl` rows whose gold atoms the extractor inferred (prepared or failed), every new `answers.jsonl` row (tested agent), and `evaluation_results.jsonl` rows with status `scored` or `evaluation_failed` (comparator). A token count is recorded only when the provider reported it as a non-negative integer. No dollar amount is recorded anywhere.

#### Scenario: An extractor call's tokens land on the preparation row
- **WHEN** the gold-atom extractor answers one item and its `AIMessage.usage_metadata` reports 120 input and 30 output tokens
- **THEN** that item's `preparation.jsonl` row has `usage.input_tokens` 120, `usage.output_tokens` 30 and `usage.calls` 1
- **AND** `usage.by_model` holds one entry with the extractor component's `provider` and `model`

#### Scenario: A provider that sets no model name is still counted
- **WHEN** the reporting message has `usage_metadata` but no `response_metadata["model_name"]`, as the HUIT Bedrock provider returns
- **THEN** the tokens are recorded under the configured model name, not dropped

#### Scenario: A call without usage is counted, not zeroed
- **WHEN** a call's message carries no usage in `usage_metadata` or `response_metadata`
- **THEN** the row's `usage.calls` includes the call and `usage.unreported_calls` is 1
- **AND** no token count is added for that call

#### Scenario: A failed extraction or judgment still records the tokens it spent
- **WHEN** the extractor or comparator returns output that fails validation
- **THEN** the `preparation_failed` or `evaluation_failed` row carries the `usage` that the call reported

#### Scenario: Supplied atoms make no call and add no key
- **WHEN** a dataset item supplies its own gold atoms
- **THEN** its `preparation.jsonl` row has no `usage` key and is otherwise unchanged

### Requirement: QA summaries SHALL record per-phase token totals
`summary.json` SHALL carry `provenance.usage` with keys `prepare`, `run` and `score`, each the sum of that phase's row `usage` objects in the run directory, or `null` when no row of the phase carries usage.

#### Scenario: Totals sum the rows
- **WHEN** a run is scored whose two answer rows report 100 and 50 input tokens for the same provider and model
- **THEN** `summary.json` `provenance.usage.run.input_tokens` is 150 and `provenance.usage.run.calls` is the sum of both rows' calls

#### Scenario: A phase with no usage is null
- **WHEN** every preparation row used supplied atoms
- **THEN** `provenance.usage.prepare` is `null`

### Requirement: QA artifacts written before usage recording SHALL keep loading
The QA loaders and `scripts/benchmarking/compare_runs.py` SHALL read run directories whose rows have no `usage` key, and SHALL read run directories whose rows have one, without error.

#### Scenario: An old preparation file loads
- **WHEN** a `preparation.jsonl` written before this change, with no `usage` keys, is loaded
- **THEN** it loads as before

#### Scenario: compare_runs reads a run directory with usage keys
- **WHEN** `compare_runs.load_qa_run` reads a run directory whose rows carry `usage`
- **THEN** it returns the same scored counts it returns for the same rows without `usage`
