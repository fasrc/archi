## ADDED Requirements

### Requirement: The per-turn search budget counts model-initiated calls only
The per-turn call budget for `search_vectorstore_hybrid` (`tool_budgets`, default 2) SHALL apply
to tool calls issued by the model. A forced initial retrieval injected by the agent before the
model's first turn MUST NOT increment that budget's counter.

#### Scenario: Forced round present, model keeps its full budget
- **WHEN** an agent with a budget of 2 injects the forced initial retrieval for a fresh user turn
  and the model then calls `search_vectorstore_hybrid` three times
- **THEN** the first two model calls reach the retriever and the third returns the synthetic
  "Search budget exhausted" response

#### Scenario: Forced round leaves the counter untouched
- **WHEN** the forced initial retrieval runs for a fresh user turn
- **THEN** the run memory's call count for `search_vectorstore_hybrid` is 0 immediately afterwards

### Requirement: The forced round still grounds the turn
The forced initial retrieval SHALL invoke the same retriever as the model-facing tool, SHALL pass
the retrieved documents to the agent's document store so they populate `source_documents`, and
SHALL prefill the completed tool round (an `AIMessage` tool call plus the matching `ToolMessage`).

#### Scenario: Documents and messages as before
- **WHEN** the forced initial retrieval runs with a retriever that returns documents
- **THEN** those documents are recorded in the active run memory and the returned message list ends
  with an `AIMessage` carrying one `search_vectorstore_hybrid` tool call followed by a `ToolMessage`
  whose `tool_call_id` matches it

#### Scenario: Retriever failure fails open
- **WHEN** the retriever raises during the forced initial retrieval
- **THEN** the message list is returned unchanged and the turn proceeds

### Requirement: The forced round is recorded under its own tool-call id
After the forced initial retrieval, the agent SHALL register the forced tool-call id with the
arguments `{"query": <user question>}` in the active run memory, and MUST NOT leave a pending
(unclaimed) tool input for `search_vectorstore_hybrid`.

#### Scenario: Forced call id carries its own arguments
- **WHEN** the forced initial retrieval runs for the question Q with an active run memory
- **THEN** `tool_inputs_by_id()` contains the forced call id with tool input `{"query": Q}`

#### Scenario: A later model call keeps its own arguments
- **WHEN** the forced initial retrieval has run and the model's first `search_vectorstore_hybrid`
  call streams with empty arguments and then executes with query Q1
- **THEN** resolving the empty arguments yields no pending input, and after execution the model
  call's recorded tool input is `{"query": Q1}`

### Requirement: Missing unbudgeted tool is visible, not silent
An agent SHALL fall back to the budgeted tool when it reaches the forced initial retrieval without
an unbudgeted retriever tool instance, and it MUST log a warning that the forced round will consume
a search slot.

#### Scenario: Fallback with warning
- **WHEN** an agent has model-facing vector tools but no unbudgeted forced-retrieval tool
- **THEN** the forced initial retrieval still runs through the budgeted tool and a warning is logged
