## ADDED Requirements

### Requirement: Tool content accumulated inside the reasoning loop is reduced against a token budget

The agent runtime SHALL evaluate the prompt against a token budget on **every** model call
within the reasoning loop, not only on the assembled history before the loop begins. When the
evaluated prompt — including the `ToolMessage` payloads produced by tool calls made during the
loop — exceeds the budget, the runtime MUST reduce the reducible tool content before the call
reaches the provider, and MUST bring the prompt within budget whenever the non-reducible
content alone fits.

Non-reducible content is the system prompt, the tool schemas, the conversation messages, the
preserved most-recent tool results, any tool results exempted from reduction, and the residue
of results that *have* been reduced — their message framing, their retained originating
tool-call arguments, and the placeholders themselves, none of which reduction removes. Where
that content alone exceeds the budget the runtime SHALL reduce everything it can and allow the
existing reactive overflow handling to cover the remainder; it MUST NOT raise on its own.

Because that residue grows with the number of tool rounds rather than with their size, the
runtime SHALL re-evaluate the complete request after reducing and, where it remains over
budget, MUST log a warning carrying the measured overage so the residual is observable rather
than assumed.

The evaluation SHALL cover the **complete** request the provider will receive — system prompt
and tool schemas included, not the conversation messages alone — since an evaluation that omits
terms of the real prompt can sit below its threshold while the real request exceeds the window.

The bound SHALL be applied against accumulated **tokens**. A per-tool call-count cap MUST NOT
be treated as satisfying this requirement: tool results vary in size by more than an order of
magnitude, so a call count does not bound tokens.

#### Scenario: Accumulated tool results are reduced before the model call

- **WHEN** the messages assembled for a model call inside the loop exceed the configured token
  budget, and more tool results are present than the preserve count
- **THEN** the runtime reduces the accumulated tool content before the model call is issued
- **AND** the reduced prompt is what the provider receives

#### Scenario: The reduced request is within budget when reducible content suffices

- **WHEN** reduction runs and the non-reducible content is itself within the budget
- **THEN** the complete post-reduction request — system prompt, tool schemas and messages
  together — is within the budget

#### Scenario: The evaluation counts the system prompt and tool schemas

- **WHEN** the conversation messages alone are within the budget but the complete request
  including the system prompt and tool schemas exceeds it
- **THEN** the runtime treats the prompt as over budget and reduces

#### Scenario: Irreducible content over budget degrades rather than raising

- **WHEN** the non-reducible content alone exceeds the budget
- **THEN** the runtime reduces every reducible tool result it can
- **AND** does not raise
- **AND** logs a warning carrying the measured overage
- **AND** the existing reactive context-overflow handling covers any resulting provider error

#### Scenario: Many small tool rounds whose residue alone exceeds the budget

- **WHEN** the accumulated messages hold enough reduced tool rounds that their framing,
  retained tool-call arguments and placeholders alone exceed the budget
- **THEN** the runtime does not raise
- **AND** logs the measured overage rather than reporting the request as within budget

#### Scenario: A prompt within budget is left untouched

- **WHEN** the complete request assembled for a model call is within the token budget
- **THEN** no tool content is reduced
- **AND** the model receives the messages unchanged

#### Scenario: The bound applies on every model call, not once per invocation

- **WHEN** an agent run performs several tool/model round trips and the budget is exceeded only
  at a later round trip
- **THEN** the reduction is applied at that later model call
- **AND** does not depend on the budget having been exceeded before the loop started

### Requirement: Every tool result that survives reduction is bounded by an enforced ceiling

Every `ToolMessage` that survives reduction SHALL be bounded by an enforced per-result ceiling,
whether it survived by being preserved as one of the most recent results or by being exempted
by tool. Content beyond the ceiling MUST be truncated with a marker indicating the result is
partial. Both the preserve-count floor and the exemption floor are statements about retained
results, so neither holds unless a retained result has an enforced size.

This ceiling MUST be applied independently of which tool produced the result. Preservation
selects by recency across all tool results, so the preserved set can contain tools this
capability cannot enumerate — including tools loaded at runtime from external servers and
tools supplied by callers. A ceiling enforced only on individually named tools does not
satisfy this requirement, because it lapses as soon as another tool is enabled.

Individual tools MAY additionally bound their own output at the source, and the following two
SHALL do so; but the requirement above MUST hold independently of them.

The document-fetch tool SHALL clamp the size of the text it returns independently of the size
the model requests. A caller-supplied size larger than the ceiling MUST be reduced to the
ceiling, and a non-positive or otherwise invalid size MUST be treated as a request for the
ceiling rather than as "no limit".

The retrieval tool SHALL clamp its **complete serialized output**, not its per-document text
alone. A per-document text cap does not bound the result, because the rendered output also
interpolates document metadata — title, URL and identifiers — that no cap governs, so a single
document with pathological metadata can produce an arbitrarily large result.

#### Scenario: An oversized result from an unenumerated tool is bounded

- **WHEN** one of the preserved most-recent tool results came from a tool this capability does
  not name — a runtime-loaded or caller-supplied tool — and its output exceeds the per-result
  ceiling
- **THEN** that result is truncated to the ceiling and marked as partial
- **AND** the complete post-reduction request is within budget

#### Scenario: An oversized exempted result is bounded

- **WHEN** an exempted retrieval result exceeds the per-result ceiling
- **THEN** it is truncated to the ceiling and marked as partial
- **AND** exemption from clearing does not exempt it from the ceiling

#### Scenario: A result within the ceiling is passed through unmodified

- **WHEN** a surviving tool result is within the per-result ceiling
- **THEN** its content is unchanged and carries no truncation marker

#### Scenario: An oversized requested document-read size is clamped

- **WHEN** the model requests a document-read size larger than the enforced ceiling
- **THEN** the returned text is no longer than the ceiling

#### Scenario: A non-positive requested size does not disable the limit

- **WHEN** the model requests a document-read size of zero or a negative number
- **THEN** the returned text is no longer than the ceiling
- **AND** the full document is not returned

#### Scenario: A smaller requested size is still honoured

- **WHEN** the model requests a document-read size below the enforced ceiling
- **THEN** the returned text respects the smaller requested size

#### Scenario: Retrieval output with oversized metadata is still bounded

- **WHEN** retrieved documents carry titles, URLs or identifiers large enough that the rendered
  result would exceed the ceiling even though every per-document text cap is respected
- **THEN** the serialized retrieval result is no longer than the ceiling

#### Scenario: The exemption floor is computed from values readable at runtime

- **WHEN** the runtime sizes the exempted retrieval content
- **THEN** it uses the retrieval tool's per-turn call budget and the enforced output ceiling
- **AND** does not depend on formatter-internal document or character limits that no
  configuration path can reach

### Requirement: The in-loop token budget is derived from the model's context window

The in-loop token budget SHALL be computed from the runtime-reported context window of the
configured provider and model, reduced by a reserve. The runtime MUST NOT hard-code a context
length.

The reported context window is a **total** sequence length covering both the prompt and the
model's generation, so the reserve exists to leave room for the response; a budget equal to the
full window would be exceeded by any answer the model produces. The reserve SHALL be
configurable and MUST default to the same 15% used by the pre-loop prompt budget, so a single
convention governs both.

#### Scenario: Budget tracks the reported context window

- **WHEN** the configured model reports a context window of N tokens and the default reserve
  applies
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
number of the most recent tool results unreduced, so the agent can still answer from
complete evidence rather than from uniformly degraded fragments.

Results produced by the vector retrieval tool SHALL be exempt from reduction while that
exemption is provably cheap: they carry the grounding evidence the answer cites, and they are
bounded by the retrieval tool's own document and character caps combined with its per-turn call
budget.

Because those caps are configurable rather than invariant, the runtime SHALL compute the worst
case size of the exempted content from the values actually in force and compare it against the
budget. Where the exempted content could occupy more than a configurable fraction of the
budget, the runtime MUST log a warning identifying the values responsible and MUST drop the
exemption, so exempted content can never become a second unbounded floor.

A removed tool result MUST be replaced by a placeholder that states the result was cleared to
stay within the context window and directs the model not to re-request it. The originating
tool call's arguments on the assistant message MUST be retained, so the model can still see
*that* it made the call and does not re-issue it and spin to the recursion limit.

#### Scenario: The most recent tool results survive reduction

- **WHEN** reduction runs with a preserve count of N and more than N tool results are present
- **THEN** the N most recent tool results are not cleared
- **AND** they retain their original content where within the per-result ceiling, and its truncated
  partial form otherwise

#### Scenario: Retrieval results are exempt while the exemption is cheap

- **WHEN** reduction runs, the accumulated messages include results from the vector retrieval
  tool, and the worst-case exempted size is within the configured fraction of the budget
- **THEN** those results are not cleared regardless of age
- **AND** they retain their original content where within the per-result ceiling, and its truncated
  partial form otherwise

#### Scenario: An oversized exemption is dropped rather than honoured

- **WHEN** the retrieval tool's configured call budget and result caps could produce exempted
  content exceeding the configured fraction of the budget
- **THEN** the runtime logs a warning naming the responsible values
- **AND** retrieval results become reducible like any other tool result

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
adjust the generation reserve, the preserve count, and the exemption fraction without a code
 change.

Invalid configuration values MUST be ignored with a warning in favour of the defaults, rather
than disabling the bound or raising.

#### Scenario: Absent configuration yields the protective default

- **WHEN** no in-loop context configuration is present
- **THEN** the bound is installed using the default generation reserve and preserve count

#### Scenario: Operator disables the bound

- **WHEN** the configuration disables in-loop context management
- **THEN** no reduction is installed
- **AND** the agent runs without raising

#### Scenario: Operator overrides the preserve count

- **WHEN** the configuration sets a preserve count different from the default
- **THEN** reduction preserves that many of the most recent tool results

#### Scenario: An invalid value falls back to the default

- **WHEN** the configuration supplies a non-numeric or out-of-range generation reserve or preserve
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
