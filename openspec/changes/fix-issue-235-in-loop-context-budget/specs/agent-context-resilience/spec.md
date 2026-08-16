## ADDED Requirements

### Requirement: Tool content accumulated inside the reasoning loop is bounded by a token budget

The agent runtime SHALL bound the total prompt sent to the model on **every** model call
within the reasoning loop, not only on the assembled history before the loop begins. When the
accumulated prompt — including the `ToolMessage` payloads produced by tool calls made during
the loop — exceeds the budget, the runtime MUST reduce it before the call reaches the provider.

The bound SHALL be applied against accumulated **tokens**. A per-tool call-count cap MUST NOT
be treated as satisfying this requirement: tool results vary in size by more than an order of
magnitude, so a call count does not bound tokens.

#### Scenario: Accumulated tool results are reduced before the model call

- **WHEN** the messages assembled for a model call inside the loop exceed the configured token
  budget, and more tool results are present than the preserve count
- **THEN** the runtime reduces the accumulated tool content before the model call is issued
- **AND** the reduced prompt is what the provider receives

#### Scenario: A prompt within budget is left untouched

- **WHEN** the messages assembled for a model call are within the token budget
- **THEN** no tool content is reduced
- **AND** the model receives the messages unchanged

#### Scenario: The bound applies on every model call, not once per invocation

- **WHEN** an agent run performs several tool/model round trips and the budget is exceeded only
  at a later round trip
- **THEN** the reduction is applied at that later model call
- **AND** does not depend on the budget having been exceeded before the loop started

### Requirement: The in-loop token budget is derived from the model's context window

The in-loop token budget SHALL be computed from the runtime-reported context window of the
configured provider and model, reduced by a safety margin. The runtime MUST NOT hard-code a
context length. The safety margin SHALL be configurable and MUST default to the same 15% used
by the pre-loop prompt budget, so a single convention governs both.

#### Scenario: Budget tracks the reported context window

- **WHEN** the configured model reports a context window of N tokens and the default safety
  margin applies
- **THEN** the in-loop budget is N reduced by 15% of N

#### Scenario: A different model yields a different budget

- **WHEN** the configured model reports a different context window
- **THEN** the in-loop budget changes accordingly with no code change

#### Scenario: Unknown context window fails open

- **WHEN** the context window cannot be determined for the configured provider and model
- **THEN** no in-loop reduction is installed
- **AND** the agent behaves exactly as it did before this capability existed

#### Scenario: An invalid or non-positive context window fails open

- **WHEN** the reported context window is not a positive integer
- **THEN** no in-loop reduction is installed
- **AND** the agent runs without raising

### Requirement: Reduction preserves the most recent tool results and the grounding retrieval evidence

Reduction SHALL remove the **oldest** tool results first and MUST preserve a configurable
number of the most recent tool results at full fidelity, so the agent can still answer from
complete evidence rather than from uniformly degraded fragments.

Results produced by the vector retrieval tool SHALL be exempt from reduction: they carry the
grounding evidence the answer cites, and they are already bounded by the retrieval tool's own
document and character caps combined with its per-turn call budget.

A removed tool result MUST be replaced by a placeholder that states the result was cleared to
stay within the context window and directs the model not to re-request it. The originating
tool call's arguments on the assistant message MUST be retained, so the model can still see
*that* it made the call and does not re-issue it and spin to the recursion limit.

#### Scenario: The most recent tool results survive reduction

- **WHEN** reduction runs with a preserve count of N and more than N tool results are present
- **THEN** the N most recent tool results retain their original content

#### Scenario: Retrieval results are never cleared

- **WHEN** reduction runs and the accumulated messages include results from the vector
  retrieval tool
- **THEN** those results retain their original content regardless of age

#### Scenario: A cleared result carries an instructive placeholder

- **WHEN** a tool result is cleared
- **THEN** its content is replaced by a placeholder stating it was cleared for context reasons
  and instructing the model not to re-request it

#### Scenario: The originating tool call arguments are retained

- **WHEN** a tool result is cleared
- **THEN** the arguments of the assistant message's originating tool call are left intact

### Requirement: In-loop context management is configurable and enabled by default

The in-loop bound SHALL be configurable through the same three-layer lookup used by the
existing tool budgets — class default, then `services.chat_app`, then the per-pipeline
config — with later layers overriding earlier ones. It MUST be enabled by default so the
protection is not contingent on operator action, and it MUST be possible to disable it or to
adjust the safety margin and the preserve count without a code change.

Invalid configuration values MUST be ignored with a warning in favour of the defaults, rather
than disabling the bound or raising.

#### Scenario: Absent configuration yields the protective default

- **WHEN** no in-loop context configuration is present
- **THEN** the bound is installed using the default safety margin and preserve count

#### Scenario: Operator disables the bound

- **WHEN** the configuration disables in-loop context management
- **THEN** no reduction is installed
- **AND** the agent runs without raising

#### Scenario: Operator overrides the preserve count

- **WHEN** the configuration sets a preserve count different from the default
- **THEN** reduction preserves that many of the most recent tool results

#### Scenario: An invalid value falls back to the default

- **WHEN** the configuration supplies a non-numeric or out-of-range safety margin or preserve
  count
- **THEN** the runtime logs a warning, uses the default for that value, and still installs the
  bound

### Requirement: The canned context-overflow apology is not a routine outcome

With the in-loop bound active, a question answerable from documentation SHALL NOT terminate in
the canned "conversation history has grown too large" response merely because the agent read
several documents during the loop. The reactive overflow handler is retained as a last-resort
net for cases the bound cannot cover — such as a single tool result that alone exceeds the
window — and its existing behaviour is unchanged.

#### Scenario: A question requiring many document reads still answers

- **WHEN** an agent run performs enough document reads to exceed the in-loop token budget
- **THEN** the run returns a substantive answer
- **AND** the answer is not the canned context-overflow message

#### Scenario: The reactive handler still covers what the bound cannot

- **WHEN** an overflow error still reaches the runtime despite the in-loop bound
- **THEN** the existing graceful degradation behaviour applies unchanged
