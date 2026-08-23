## ADDED Requirements

### Requirement: Suppress reasoning that precedes the orphan closing tag

The agent SHALL NOT stream a reasoning model's private chain-of-thought to the
user as visible text. When the provider the agent is about to call is configured
to emit thinking, the streaming paths (`stream()` and `astream()`) SHALL hold
visible text output while no `</think>` has been observed in the
accumulated content, and SHALL release it once a `</think>` is observed. Held text
that is never released by a `</think>` SHALL still reach the user through the
existing end-of-stream `final` event, so holding never discards content.

#### Scenario: Orphan reasoning precedes the closing tag

- **GIVEN** the active provider is configured with `enable_thinking` true
- **WHEN** the stream delivers chunks `["some reasoning", " continues", "</think>", "\n\nThe answer"]`
- **THEN** no visible `text` event is emitted that contains the reasoning text ("some reasoning" or "continues")
- **AND** no emitted chunk contains a bare `</think>` tag
- **AND** the visible answer ("The answer") is emitted only after the `</think>` boundary is observed

#### Scenario: Orphan reasoning with no answer after the tag

- **GIVEN** the active provider is configured with `enable_thinking` true
- **WHEN** the stream delivers only reasoning followed by `</think>` and then ends
- **THEN** no visible `text` event containing the reasoning is emitted
- **AND** the final visible answer is empty

#### Scenario: Thinking-enabled provider never emits a closing tag

- **GIVEN** the active provider is configured with `enable_thinking` true
- **WHEN** the stream delivers `["The ", "quick ", "answer"]` with no `</think>` anywhere
- **THEN** no incremental visible `text` event is emitted during the stream
- **AND** the end-of-stream `final` event still carries the complete answer ("The quick answer")

### Requirement: Providers that do not emit thinking stream unchanged

When the active provider is not configured to emit thinking, the agent SHALL
stream a plain answer to the user chunk-by-chunk exactly as it does today, and the
suppression logic SHALL add no buffering and no latency to that path.

#### Scenario: Plain answer streams incrementally

- **GIVEN** the active provider is not configured with `enable_thinking` true
- **WHEN** the stream delivers chunks `["The ", "quick ", "answer"]` with no `</think>` tag anywhere
- **THEN** visible `text` events are emitted incrementally as the content grows ("The", "The quick", "The quick answer")

#### Scenario: Absent or malformed provider configuration

- **GIVEN** the provider block, its `extra_kwargs`, or its `chat_template_kwargs` is missing or is not a mapping
- **WHEN** the agent streams a response
- **THEN** the agent SHALL treat the provider as not emitting thinking and stream incrementally
- **AND** SHALL NOT raise

### Requirement: The suppression gate follows the request-local model override

The gate SHALL be resolved from the provider the agent will actually call for this
request, not from the deployment's configured default provider, so that a chat-UI
model switch changes the gate with it.

#### Scenario: Request switches to a thinking-enabled model

- **GIVEN** the deployment default provider has `enable_thinking` unset
- **AND** `adopt_request_local_model()` has bound the view to a provider configured with `enable_thinking` true
- **WHEN** that request streams orphan reasoning followed by `</think>` and an answer
- **THEN** the reasoning is suppressed for that request
