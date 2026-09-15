## ADDED Requirements

### Requirement: Request-time LLM overrides are request-local

A request-time provider/model override SHALL affect only the request that carried it. The chat app MUST NOT mutate shared cross-request state — specifically the shared pipeline's `agent_llm`, its compiled agent, its active tool/middleware lists, its active run memory, or the shared reported-model field — in order to apply an override.

The single permitted exception is an idempotent, behaviour-neutral memoization of the pipeline's MCP tool list, which does not vary per request.

#### Scenario: Override does not mutate the shared pipeline

- **WHEN** a streaming request supplies a provider and model override
- **THEN** the shared pipeline's `agent_llm` is the same object before, during, and after the request
- **AND** the response is produced by the override LLM, not the shared default LLM

#### Scenario: Override does not leak into a later default request

- **WHEN** an overridden request completes
- **AND** a subsequent request supplies no provider/model override
- **THEN** the subsequent request is served by the configured default LLM and its `extra_kwargs`

#### Scenario: Failed override leaves shared state untouched

- **WHEN** constructing the override LLM or its request-local agent raises
- **THEN** the shared pipeline's `agent_llm` and compiled agent are unchanged
- **AND** the request yields an error or falls back to the default without disturbing other requests

### Requirement: An overridden request's retrieved documents stay with that request

Tools invoked on behalf of an overridden request SHALL record their retrieved documents, tool inputs, and tool-budget consumption into that request's own run memory. They MUST NOT write into the shared pipeline's run memory, which a concurrent default request may own.

#### Scenario: Static tool records into the request's own memory

- **WHEN** an overridden request's agent invokes a cached static tool (e.g. a catalog or local-file search, not a vectorstore tool)
- **THEN** the retrieved documents appear in that request's run memory and in its `source_documents`
- **AND** the shared pipeline's active run memory is left untouched

#### Scenario: Documents are not attributed to a concurrent request

- **WHEN** an overridden request and a default request are in flight together and both invoke tools
- **THEN** each request's answer cites only the documents retrieved on its own behalf
- **AND** neither request's tool-budget consumption is charged against the other

### Requirement: An overridden turn is persisted with the model that answered it

Making the reported model request-local SHALL NOT change what is recorded in conversation history. The persisted `model_used` for an overridden turn MUST be the override's `provider/model`, and the shared reported-model field MUST remain untouched.

#### Scenario: Persisted model reflects the override

- **WHEN** an overridden request completes and its conversation rows are written
- **THEN** the persisted `model_used` is the override's `provider/model`
- **AND** the streamed response reports that same model
- **AND** the shared reported-model field is unchanged from its value before the request

#### Scenario: Default requests persist the configured model

- **WHEN** a request with no override completes
- **THEN** the persisted `model_used` is the configured model, exactly as before this change

### Requirement: Concurrent overridden requests do not bleed into each other

Two or more streaming requests carrying different overrides SHALL each be served by their own override LLM for the whole turn, regardless of interleaving. Completion of one overridden request MUST NOT change the LLM observed by another in-flight request, nor the LLM observed by any request that starts afterwards.

#### Scenario: Two overlapping overrides each keep their own model

- **WHEN** request A (override X) and request B (override Y) overlap, with B starting before A completes
- **THEN** every model call made on behalf of A uses X and every model call made on behalf of B uses Y
- **AND** each request reports its own model, not the other's

#### Scenario: No residue after interleaved completion

- **WHEN** two overlapping overridden requests complete in either order
- **THEN** a request issued afterwards with no override is served by the configured default LLM
- **AND** neither override's `extra_kwargs` (e.g. `enable_thinking`) are observable on that request

### Requirement: Overridden requests execute in parallel without serialization

The request-local override path SHALL NOT serialize concurrent overridden requests. The A/B comparison UI issues two overridden streaming requests in parallel, and both MUST be able to make progress concurrently.

#### Scenario: A/B pair overlaps rather than queues

- **WHEN** two overridden streaming requests are started concurrently
- **THEN** the second request begins producing output before the first has completed
- **AND** neither request blocks waiting on a lock held for the duration of the other's turn

#### Scenario: Concurrent request-local builds initialize MCP exactly once

- **WHEN** two overridden requests concurrently build a request-local pipeline from a pipeline whose MCP tools have not yet been built
- **THEN** the MCP tool list is built exactly once
- **AND** no MCP client is constructed and then discarded

### Requirement: The non-override path is unchanged

A request that supplies no provider/model override SHALL continue to be served by the shared pipeline exactly as before, with no additional per-request pipeline or agent construction.

#### Scenario: Default request reuses the shared pipeline

- **WHEN** a streaming request supplies no provider or model
- **THEN** the shared pipeline instance serves the request directly
- **AND** no request-local pipeline view or agent is constructed
