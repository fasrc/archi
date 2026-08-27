# Spec delta — huit-bedrock-tool-calling

## ADDED Requirements

### Requirement: HUIT Bedrock models accept bound tools and report tool calls

`HuitBedrockChat` SHALL implement `bind_tools` on the class, and SHALL carry bound tools
through to the HUIT Bedrock proxy and back.

The override MUST be defined on `HuitBedrockChat` itself. langchain-core's default
`with_structured_output` decides whether it is usable by comparing
`type(self).bind_tools` against `BaseChatModel.bind_tools` — an identity check, not a
capability probe — and raises `NotImplementedError` when they match. Every structured-output
consumer in the codebase reaches the model that way, so an override defined anywhere else,
or attached at runtime, leaves the model refusing.

`bind_tools` SHALL accept the tool forms langchain accepts — a JSON-schema dict, a Pydantic
class, a `BaseTool`, or a plain callable — and SHALL convert each to the Anthropic tool
shape: `name`, `description`, and `input_schema` holding the parameter schema. It SHALL
also tolerate keyword arguments it does not recognize, because langchain-core's default
`with_structured_output` always passes `ls_structured_output_format` and a model that
rejected it would refuse exactly the calls this requirement exists to serve.

`tool_choice` SHALL map as follows: `None` omits the key entirely and lets the model
choose; `"any"` and `"auto"` become `{"type": "any"}` and `{"type": "auto"}`; any other
string becomes `{"type": "tool", "name": <string>}`; and a dict passes through unchanged.
`"any"` is the value langchain-core's default sends, so it is load-bearing rather than
conventional.

The request body SHALL carry `tools` and `tool_choice` only when tools are bound. A call
that binds no tools SHALL produce the request it produces today — no `tools` key, no
`tool_choice` key. This provider is on the live agent path and carries the RAGAS
benchmark's judge traffic, so tool support must be inert for callers that do not use it.

The response reader SHALL extract `tool_use` content blocks into `AIMessage.tool_calls`,
each carrying the block's `name`, its `input` as the call arguments, and its `id`. Text
blocks SHALL keep their present handling, and a response mixing text and a `tool_use` block
SHALL preserve both. Populating `tool_calls` is not decorative: langchain's
`JsonOutputKeyToolsParser` reads structured output from that field and matches on the tool
name, so a response parsed into text alone yields nothing no matter what the model returned.

`HuitBedrockChat` SHALL NOT define its own `with_structured_output`. Overriding
`bind_tools` is sufficient to make langchain-core's implementation work, and defining a
second one would fork the parser selection, the `include_raw` handling, and the
Pydantic-versus-dict schema split away from the library that maintains them.

#### Scenario: Structured output stops refusing

- **WHEN** a caller builds a HUIT Bedrock chat model and calls
  `with_structured_output(schema)` on it
- **THEN** it returns a runnable instead of raising `NotImplementedError`, and invoking
  that runnable against a response carrying a `tool_use` block yields the parsed object

#### Scenario: Tools reach the proxy in Anthropic shape

- **WHEN** tools are bound and the model is invoked
- **THEN** the request body contains a `tools` list whose entries carry `name`,
  `description` and `input_schema`, with the parameter schema under `input_schema`

#### Scenario: Forced tool choice is encoded, and the default is absent

- **WHEN** `tool_choice` is `"any"`, `"auto"`, a tool name, or omitted
- **THEN** the body carries `{"type": "any"}`, `{"type": "auto"}`,
  `{"type": "tool", "name": <name>}`, or no `tool_choice` key respectively

#### Scenario: A tool call comes back as a tool call

- **WHEN** the proxy returns content blocks containing a `tool_use` block
- **THEN** the resulting `AIMessage` carries a matching entry in `tool_calls` with the
  block's name, arguments and id, and any accompanying text block is still the message
  content

#### Scenario: Callers that bind no tools are unaffected

- **WHEN** the model is invoked with no tools bound
- **THEN** the request body carries neither `tools` nor `tool_choice`, and the response is
  handled exactly as before

### Requirement: The HUIT Bedrock model catalog keeps denying tool support for now

Every entry in the provider's default model list SHALL continue to report `supports_tools`
as `false` while multi-turn tool history remains unserializable.

The tempting move is the opposite one, and it is wrong. Nothing reads this flag to decide
whether to bind — it is advertisement, serialized out of the provider API
(`src/interfaces/chat_app/app.py:3954`) into the chat app's model picker. An operator
reading it there is choosing a model *for the agent*, and the agent drives multi-turn tool
loops: call a tool, feed the result back, continue. That still does not work, because
`_convert_messages` renders an assistant turn as `str(msg.content)` and drops its
`tool_use` blocks, so the following `tool_result` references a `tool_use_id` the proxy
cannot match. Flipping the flag would answer "can this model use tools" with `true` for the
one caller whose use of tools would break.

What this change delivers is single-shot bound-tool invocation, which is what structured
output needs and what the evaluator uses. That is narrower than what the flag claims. The
flag flips in the follow-up that fixes tool-call history serialization, not here.

#### Scenario: The catalog does not over-promise

- **WHEN** the provider's default model list is read
- **THEN** every entry still reports `supports_tools` as `false`, because multi-turn tool
  loops remain broken even though single-shot structured output now works

### Requirement: A profile's timeout reaches the HUIT transport

`HuitBedrockProvider.get_chat_model` SHALL honor a `timeout` keyword as an alias for
`request_timeout`, so a caller that asks for a longer call gets one.

This is the same judge-parity problem as the model id, one layer down. An evaluator profile
declares its timeout as `timeout`, and `ModelDescriptor.provider_kwargs` passes it under
that name. `get_chat_model` copies only `max_tokens`, `temperature`, `anthropic_version`
and `request_timeout` out of the caller's keywords, so a profile timeout is dropped without
a word and the model keeps its 120-second default. The RAGAS benchmark judges the same
model at 300 seconds
(`config/benchmarking/ragas.yaml`), so a judge call that RAGAS completes can time out here
— and the failure would look like a flaky proxy rather than a dropped setting.

An explicit `request_timeout` SHALL win over `timeout` when both are given, so a caller who
names the transport setting directly is never overridden by an alias.

#### Scenario: A profile timeout is honored

- **WHEN** a caller passes `timeout` to `get_chat_model`
- **THEN** the constructed model uses it as its `request_timeout` rather than the 120-second
  default

#### Scenario: The explicit setting wins

- **WHEN** both `timeout` and `request_timeout` are passed
- **THEN** `request_timeout` is used
