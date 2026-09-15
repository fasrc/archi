## ADDED Requirements

### Requirement: Concurrent default requests keep isolated run memory

Concurrent **default** (non-overridden) requests served by the shared pipeline instance SHALL
each accumulate their retrieved documents, tool inputs, and tool-budget consumption into their
own run memory. The active run memory MUST be scoped to the request's execution context (a
`contextvars.ContextVar`), not stored on a shared instance attribute, so that no request's
retrieved documents, tool inputs, or tool-budget accounting are attributed to another request.

#### Scenario: Two concurrent default requests do not cross-attribute documents

- **WHEN** two default requests run concurrently through the same shared pipeline instance and both invoke document-retrieving tools
- **THEN** each request's run memory contains only the documents retrieved on its own behalf
- **AND** each request's answer cites only its own documents
- **AND** neither request's tool-budget consumption is charged against the other

#### Scenario: Tool callbacks resolve the calling request's memory

- **WHEN** a static tool callback (`_store_documents`, `_store_tool_input`, or `_consume_tool_budget`) runs on behalf of a request
- **THEN** it records into the run memory bound to the current request context (`ContextVar`)
- **AND** it does not read or write any shared instance attribute holding another request's memory

#### Scenario: No active memory between turns fails open

- **WHEN** a tool callback runs with no active run memory set in the current context (between turns or a non-agent context)
- **THEN** the callback fails open exactly as before (no exception, no attribution) rather than raising

### Requirement: The default path introduces no per-request agent recompile

Resolving active memory from a request-context `ContextVar` SHALL NOT introduce any per-request
construction of a pipeline view or recompilation of the agent graph on the default path. A
default request MUST continue to be served directly by the shared pipeline instance, and the
memory-driven `_static_tools` rebuild MUST NOT be required for isolation.

#### Scenario: Default request reuses the shared pipeline without a recompile

- **WHEN** a request supplies no provider/model override
- **THEN** the shared pipeline instance serves the request directly
- **AND** no request-local pipeline view is constructed and the agent graph is not recompiled for that request

### Requirement: Async and single-threaded paths keep isolation semantics

The request-context active memory SHALL resolve correctly for both the threaded synchronous
`stream()` path and the async `astream()` path, and single-threaded `invoke()` semantics MUST be
unchanged beyond memory now resolving through the `ContextVar`.

#### Scenario: Async streaming request resolves its own memory

- **WHEN** a request is served via the async `astream()` path
- **THEN** its tool callbacks resolve the run memory started for that request's context
- **AND** the retrieved documents appear in that request's `source_documents`

#### Scenario: Single-threaded invoke is unchanged

- **WHEN** the agent is driven by `invoke()` in a single thread
- **THEN** the run's retrieved documents, tool inputs, and tool-budget accounting are recorded exactly as before this change

### Requirement: Overridden-request isolation from #86 is preserved

Removing the memory-driven `_static_tools` rebuild on the request-local view SHALL NOT weaken the
isolation delivered by issue #86. An overridden request's retrieved documents, tool inputs, and
tool-budget consumption MUST remain isolated from a concurrent default request, and the shared
pipeline's state MUST remain untouched by an override.

#### Scenario: Overridden and default requests in flight stay isolated

- **WHEN** an overridden request and a default request are in flight together and both invoke tools
- **THEN** each request's answer cites only the documents retrieved on its own behalf
- **AND** neither request's tool-budget consumption is charged against the other
- **AND** the shared pipeline's `agent_llm` and compiled agent are unchanged by the override
