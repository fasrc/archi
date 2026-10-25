## ADDED Requirements

### Requirement: An agent run SHALL report every LLM call on its invoke path to the caller's callbacks
When `BaseReActAgent.invoke()` receives `callbacks`, every LLM call it makes SHALL receive those callbacks: the main agent call, the history summarizer, the context-overflow retry, and the recursion-limit wrap-up. A call made without `callbacks` SHALL behave as before.

#### Scenario: The recursion-limit wrap-up is counted
- **WHEN** an agent invoked with a `UsageRecorder` in `callbacks` hits the recursion limit, and the wrap-up call reports 12 input and 4 output tokens
- **THEN** the recorder's snapshot has `calls` 1, `input_tokens` 12 and `output_tokens` 4

#### Scenario: The context-overflow retry receives the callbacks
- **WHEN** an agent invoked with `callbacks` overflows its context window and retries with the last user message
- **THEN** the retry's config holds `recursion_limit` 10 and the same `callbacks`

#### Scenario: The history summarizer is counted
- **WHEN** an agent invoked with a `UsageRecorder` in `callbacks` compresses a long history with one summarizer call
- **THEN** the recorder counts that call

#### Scenario: The async wrap-up is counted
- **WHEN** the async wrap-up runs with a `UsageRecorder` in `callbacks`
- **THEN** the recorder counts the wrap-up call

#### Scenario: A run without callbacks is unchanged
- **WHEN** an agent is invoked without `callbacks` and hits the recursion limit
- **THEN** the wrap-up call runs and the agent returns its wrap-up answer as before

### Requirement: A retry summary's token totals SHALL count only the LLM calls the retry made
For a run directory written by `QAWorkflow.retry` (the evaluation retry), `summary.json` `provenance.usage` SHALL sum only the rows the retry produced: `prepare` SHALL be `null` because the retry copies `preparation.jsonl` and makes no extractor call, `run` SHALL sum the answers the retry re-ran, and `score` SHALL sum the results the retry re-scored. The row files SHALL keep each copied row's own `usage`. A run directory that is not a retry SHALL keep its totals.

#### Scenario: Copied preparation usage is not repeated
- **WHEN** a parent run's inferred-atom preparation rows report extractor usage, and a retry re-runs one failed attempt
- **THEN** the retry's `provenance.usage.prepare` is `null`
- **AND** the retry's copied `preparation.jsonl` rows still carry the parent's `usage`

#### Scenario: Carried answers and results are not repeated
- **WHEN** a retry re-runs and re-scores one attempt and carries the parent's answer and result for a second attempt
- **THEN** the retry's `provenance.usage.run` equals the re-run answer's `usage`
- **AND** the retry's `provenance.usage.score` equals the re-scored result's `usage`

#### Scenario: Parent plus retry is the total spend
- **WHEN** the parent's and the retry's `provenance.usage` are added with `sum_usage`
- **THEN** each LLM call the two runs made is counted once

### Requirement: Each recorded LLM call SHALL carry the provider and model its chat model was built with
`get_model` SHALL tag each chat model it builds with `metadata["archi_provider"]` (the canonical provider type value) and `metadata["archi_model"]` (the model name it was given), and `UsageRecorder` SHALL record a tagged call under the tag's provider. The model SHALL be the response's model name when the response reports one, else the tag's model. A call with no tag SHALL keep the recorder's constructor provider and model.

#### Scenario: Two models on two providers get two provider labels
- **WHEN** a classic pipeline declares `condense_model: openai/m1` and `chat_model: anthropic/m2`, and both models answer one call each under one `UsageRecorder("local", "default-m")`
- **THEN** `by_model` holds one entry with `provider` `openai` and one with `provider` `anthropic`

#### Scenario: An unreported tagged call uses the tag's model
- **WHEN** a call tagged `anthropic` / `m2` finishes without usage and without a response model name
- **THEN** it is counted as an unreported call under `provider` `anthropic` and `model` `m2`

#### Scenario: An untagged call keeps the constructor label
- **WHEN** a call from a model built outside `get_model` finishes
- **THEN** it is recorded under the recorder's constructor provider

#### Scenario: A failed call leaves no label behind
- **WHEN** a tagged call raises, and a later call ends with the same run id and no tag
- **THEN** the later call is recorded under the constructor provider

#### Scenario: Existing metadata is kept
- **WHEN** a provider returns a chat model that already has `metadata`
- **THEN** `get_model` keeps those keys and adds the two tag keys

### Requirement: A classic pipeline SHALL build each model with its provider's configuration
`BasePipeline._init_llms` SHALL pass `get_model` the provider configuration from `services.chat_app.providers.<provider>`, built by the same rule as the agent pipelines, for the default model and for each declared model, so that a classic pipeline can be constructed.

#### Scenario: A classic pipeline with declared models builds
- **WHEN** a classic pipeline declares `condense_model: openai/m1` and `chat_model: anthropic/m2`, and `services.chat_app.providers.openai.base_url` is set
- **THEN** `_init_llms` builds both models without error
- **AND** the `openai` model is built with that `base_url`

#### Scenario: A classic pipeline with only a default model builds
- **WHEN** a classic pipeline declares no models and has a default provider and model
- **THEN** `_init_llms` builds the default model without error
