## ADDED Requirements

### Requirement: Per-turn tool-call budget enforcement

The ReAct agent (`BaseReActAgent`) SHALL enforce a configurable per-tool call budget for each user turn. When a tool's call count exceeds its configured budget within a turn, the next invocation MUST short-circuit by returning a synthetic "search budget exhausted" string instead of invoking the underlying tool implementation. The agent loop SHALL NOT raise `GraphRecursionError` as a result of an over-budget call.

#### Scenario: First call passes through

- **WHEN** a tool with a configured budget of 2 is invoked for the first time in a user turn
- **THEN** the underlying tool implementation is called and its result is returned to the model unchanged
- **AND** the counter for that tool name is incremented to 1

#### Scenario: Second call passes through

- **WHEN** a tool with a configured budget of 2 is invoked for the second time in the same user turn
- **THEN** the underlying tool implementation is called and its result is returned to the model unchanged
- **AND** the counter for that tool name is incremented to 2

#### Scenario: Third call short-circuits

- **WHEN** a tool with a configured budget of 2 is invoked for the third time in the same user turn
- **THEN** the underlying tool implementation MUST NOT be invoked
- **AND** the tool MUST return a synthetic string beginning with `"Search budget exhausted:"`
- **AND** the synthetic string MUST identify the tool name and the limit
- **AND** the synthetic string MUST instruct the model to answer from already-retrieved chunks or disclose no-coverage to the user

### Requirement: Counter resets per user turn

The per-tool call counter MUST reset to zero at the start of every new user turn, automatically and without any explicit reset call from agent code.

#### Scenario: New turn after budget exhaustion

- **WHEN** a tool has been short-circuited in the previous user turn
- **AND** the agent begins a new user turn
- **THEN** the next invocation of that tool in the new turn invokes the underlying tool implementation
- **AND** the counter for that tool name starts at 1 after the invocation

#### Scenario: Recursion-handler retry preserves counter

- **WHEN** the agent's recursion-limit handler retries with trimmed inputs inside the same user turn
- **THEN** the existing per-tool counter values for that turn are preserved (the retry does not start a new turn)
- **AND** any tool that was already at or over budget continues to short-circuit during the retry

### Requirement: Budget configuration lookup

The agent SHALL determine the per-tool budget for a tool name by consulting, in order:

1. `pipeline_config.tool_budgets[<tool_name>]`
2. `services.chat_app.tool_budgets[<tool_name>]`
3. The class-level default `BaseReActAgent.DEFAULT_TOOL_BUDGETS[<tool_name>]`

If no value is found at any layer, the tool MUST NOT be subject to a budget (effectively unbounded), and the closure MUST behave identically to the no-budget code path.

#### Scenario: Pipeline config overrides chat-app config

- **WHEN** `pipeline_config.tool_budgets` defines a value for the tool
- **AND** `services.chat_app.tool_budgets` defines a different value for the same tool
- **THEN** the agent uses the `pipeline_config` value

#### Scenario: Chat-app config overrides class default

- **WHEN** `pipeline_config.tool_budgets` does not define a value for the tool
- **AND** `services.chat_app.tool_budgets` defines a value for the tool
- **THEN** the agent uses the `services.chat_app.tool_budgets` value

#### Scenario: Class default applies when config is absent

- **WHEN** neither config layer defines a value for the tool
- **AND** the tool name appears in `DEFAULT_TOOL_BUDGETS`
- **THEN** the agent uses the class default

#### Scenario: Tool without a configured budget is unbounded

- **WHEN** no config layer and no class default defines a value for the tool name
- **THEN** the tool is not subject to a budget
- **AND** invocations always reach the underlying implementation

### Requirement: `search_vectorstore_hybrid` has a default budget of 2

The class default `BaseReActAgent.DEFAULT_TOOL_BUDGETS` SHALL include an entry `"search_vectorstore_hybrid": 2`, so that deployments inherit the cap without requiring config edits.

#### Scenario: Default deployment caps search_vectorstore_hybrid

- **WHEN** a `BaseReActAgent` subclass is instantiated without overriding `tool_budgets` in any config layer
- **AND** `search_vectorstore_hybrid` is registered as a tool
- **THEN** the third invocation of `search_vectorstore_hybrid` in a single user turn returns the synthetic over-budget string

### Requirement: Backward compatibility for `create_retriever_tool` callers

`create_retriever_tool` callers that do not pass an `enforce_budget` callback MUST observe identical behavior to today: the closure invokes the retriever on every call, with no budget enforcement.

#### Scenario: Standalone smoke test usage

- **WHEN** `create_retriever_tool` is called without an `enforce_budget` argument
- **THEN** all subsequent tool invocations call the underlying retriever
- **AND** no synthetic "budget exhausted" string is ever returned
