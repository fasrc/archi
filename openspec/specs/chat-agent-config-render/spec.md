# chat-agent-config-render Specification

## Purpose
Chat-agent settings declared under `services.chat_app` in a deployment config (`force_initial_retrieval`, `tool_budgets`) survive the config render into the runtime config, with explicit `false` and maps preserved and undeclared or null values left to the code defaults.
## Requirements
### Requirement: Forced initial retrieval is configurable per deployment
The config render template SHALL emit `services.chat_app.force_initial_retrieval` into the rendered
runtime config when a deployment declares it with a non-null value, preserving the declared value
and its type. The template MUST NOT emit the key when it is undeclared or null.

#### Scenario: Explicit false survives the render
- **WHEN** a deployment config declares `services.chat_app.force_initial_retrieval: false`
- **THEN** the rendered config's `services.chat_app.force_initial_retrieval` is the boolean `false`

#### Scenario: Explicit true survives the render
- **WHEN** a deployment config declares `services.chat_app.force_initial_retrieval: true`
- **THEN** the rendered config's `services.chat_app.force_initial_retrieval` is the boolean `true`

#### Scenario: Undeclared or null leaves the code default in force
- **WHEN** a deployment config omits `force_initial_retrieval` or sets it to null
- **THEN** the rendered `services.chat_app` block has no `force_initial_retrieval` key

### Requirement: Per-turn tool budgets are configurable per deployment
The config render template SHALL emit `services.chat_app.tool_budgets` into the rendered runtime
config when a deployment declares it with a non-null value, preserving every declared tool name and
value. The template MUST NOT emit the key when it is undeclared or null.

#### Scenario: A declared budget map survives the render
- **WHEN** a deployment config declares
  `services.chat_app.tool_budgets: {search_vectorstore_hybrid: 3}`
- **THEN** the rendered config's `services.chat_app.tool_budgets` equals
  `{search_vectorstore_hybrid: 3}`

#### Scenario: Undeclared or null leaves the code default in force
- **WHEN** a deployment config omits `tool_budgets` or sets it to null
- **THEN** the rendered `services.chat_app` block has no `tool_budgets` key

### Requirement: The new keys add no boolean-coercing defaults
The template SHALL NOT express either key with a `default(<literal>, true)` filter, so the
existing falsy-default guards pass with their frozen baseline unchanged.

#### Scenario: Falsy-default guards unchanged
- **WHEN** the falsy-default guard tests run against the template
- **THEN** they pass with `_NON_BOOL_DEFAULT_BASELINE` unchanged

