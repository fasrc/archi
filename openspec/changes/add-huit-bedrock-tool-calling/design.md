# Design — HUIT Bedrock tool calling

## The one decision that shapes everything else

**Override `bind_tools` only; do not write a `with_structured_output`.**

langchain-core's `BaseChatModel.with_structured_output` is not a stub — it is a complete
implementation that refuses to run only because `bind_tools` is missing. Reading it
(langchain-core 1.2.13) fixes our entire contract:

```python
if type(self).bind_tools is BaseChatModel.bind_tools:
    raise NotImplementedError("with_structured_output is not implemented for this model.")

llm = self.bind_tools(
    [schema],
    tool_choice="any",
    ls_structured_output_format={"kwargs": {"method": "function_calling"}, "schema": schema},
)
...
key_name = convert_to_openai_tool(schema)["function"]["name"]
output_parser = JsonOutputKeyToolsParser(key_name=key_name, first_tool_only=True)
return llm | output_parser
```

Three requirements fall straight out of those lines, and they are the reason the
implementation is small:

1. The override must be on the class — the guard is an identity check on
   `type(self).bind_tools`, not a `hasattr`.
2. `bind_tools` must tolerate `tool_choice="any"` and an unknown keyword
   (`ls_structured_output_format`). langchain passes both unconditionally.
3. `JsonOutputKeyToolsParser` reads `AIMessage.tool_calls` and matches on `name`. So
   `_generate` must populate `tool_calls` with the tool's real name — parsing `tool_use`
   blocks is not a nicety, it is the half that makes structured output work at all.

Writing our own `with_structured_output` instead would mean owning a parser, an
`include_raw` branch, and the Pydantic-vs-dict schema split, all of which langchain already
has and keeps in step with its own parsers. We would gain nothing and inherit maintenance.

## Message shape: Anthropic native, because the proxy is

`_generate` already speaks Bedrock's Anthropic dialect — `anthropic_version`,
`messages`, `system`, `stop_sequences` — POSTed to
`{base_url}/model/{model_id}/invoke`. Tools ride in the same body under `tools`, each as:

```json
{"name": "...", "description": "...", "input_schema": {...}}
```

`langchain_core.utils.function_calling.convert_to_openai_tool` normalizes every input form
langchain accepts (dict schema, Pydantic class, `BaseTool`, plain callable) into
`{"type": "function", "function": {"name", "description", "parameters"}}`. Converting from
*that* rather than from the raw argument means we handle all four input forms without
inspecting any of them ourselves; the Anthropic mapping is then a three-key rename, with
`parameters` becoming `input_schema`.

This was verified against the live proxy before the design was written, not assumed: a
POST carrying `tools` and `tool_choice` returned `HTTP 200`, `stop_reason: tool_use`, and a
`tool_use` block whose `input` matched the declared schema with correct types. The proxy
forwards tools; only our client lacked them.

## `tool_choice` mapping

| Caller passes | Body carries |
|---|---|
| `None` (default) | no `tool_choice` key — model decides |
| `"any"` | `{"type": "any"}` — what `with_structured_output` sends |
| `"auto"` | `{"type": "auto"}` |
| any other string | `{"type": "tool", "name": <string>}` |

The string-name case is what a caller forcing one specific tool would write, and it is
also what several langchain providers accept, so honoring it costs one branch and avoids a
surprise. A dict is passed through untouched: a caller who already speaks Anthropic should
not be re-encoded.

## Response parsing

Today `_generate` keeps `type == "text"` blocks and drops the rest
(`huit_bedrock_provider.py:192-197`). The change reads the block list once and splits it:
text blocks concatenate as they do now; `tool_use` blocks become `ToolCall` entries
(`name`, `args` from the block's `input`, `id`).

Content stays a string rather than becoming a block list. When Anthropic is forced to a
tool it returns *only* the `tool_use` block, so content is `""` — which is correct, and
which `JsonOutputKeyToolsParser` never looks at. Returning a block list instead would be a
visible change to every existing non-tool caller for no gain here.

## Why the unbound path gets its own test

This provider is on the live agent path for any HUIT Bedrock deployment, and the RAGAS
benchmark's judge runs through it today. The change must be inert for every call that binds
no tools. A test asserts the request body for an unbound call contains neither `tools` nor
`tool_choice` — pinning "inert" as a property rather than trusting that the new branches
are guarded correctly.

## Alternatives considered

- **Route the console at direct Anthropic instead** (`provider: anthropic`,
  `claude-sonnet-4-5`). Zero code, and it was measured working end to end. Rejected as the
  goal: it is the same model over a different route and a different key, so the console's
  judge would still not be the RAGAS judge, and the divergence would be permanent and
  invisible in the results. Kept as the documented fallback if this change stalls.
- **Prompt-and-parse structured output** (ask for JSON in the system prompt, parse the
  text). Works without tool support, but replaces a schema the model is constrained to with
  a schema it is merely asked for, and the scorer's atom judgments are exactly where a
  silently malformed object is worst. Rejected: the proxy supports tools, so there is no
  reason to accept a weaker guarantee.
- **Swap `HuitBedrockChat` for `ChatAnthropic` pointed at the proxy.** The proxy is not
  API-compatible with the Anthropic SDK at the auth layer — it takes `x-api-key` against a
  `/model/{id}/invoke` path rather than the Anthropic Messages endpoint — so this is a
  larger rewrite with a live-traffic blast radius, for a provider whose only current gap is
  tool calling.
