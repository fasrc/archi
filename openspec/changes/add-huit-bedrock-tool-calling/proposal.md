# Give HUIT Bedrock tool calling, so it can judge QA evaluations

## Why

`HuitBedrockChat` is the only hand-rolled chat model in the provider set
(`src/archi/providers/huit_bedrock_provider.py:120`). It subclasses `BaseChatModel` and
implements `_generate` and nothing else — no `bind_tools`, no `with_structured_output`.
Every other provider returns a vendor chat model (`ChatAnthropic`, `ChatOpenAI`, …) that
carries both.

langchain-core's default `with_structured_output` refuses outright when `bind_tools` was
never overridden:

```python
if type(self).bind_tools is BaseChatModel.bind_tools:
    msg = "with_structured_output is not implemented for this model."
    raise NotImplementedError(msg)
```

Measured on this deployment (langchain-core 1.2.13):

```
provider=huit_bedrock model=us.anthropic.claude-sonnet-4-5-20250929-v1:0
  construct: OK HuitBedrockChat
  with_structured_output: FAIL NotImplementedError
```

That closes the QA evaluation console to the provider the project already judges with.
`LangChainEvaluatorRuntime` scores every gold atom through
`model.with_structured_output(schema)` (`src/evaluation/qa/runtime.py:223`), so an
evaluator profile naming `huit_bedrock` raises before it scores anything.

The judge this blocks is not an arbitrary one. `config/benchmarking/ragas.yaml:102-103`
pins the RAGAS judge to `huit_bedrock` /
`us.anthropic.claude-sonnet-4-5-20250929-v1:0`, chosen — per the comment directly above
it — "pinned for reproducibility, independent of the SUT to break the 'judge rates its
own style higher' bias". The QA console cannot use that judge today, so the two
evaluation stacks would grade the same agent with different models over different API
routes on different billing. That is a benchmark-integrity defect, and the release plan
orders benchmark integrity ahead of retrieval quality precisely because it is retrieval
quality's evidence rig.

Nothing else is in the way. The provider resolves correctly from an empty provider
config — `LangChainEvaluatorRuntime` passes `{}` (`src/evaluation/qa/runtime.py:210-213`),
and `HuitBedrockChat` falls back to its own `DEFAULT_HUIT_BEDROCK_BASE_URL`
(`:44`, applied at `:231`) with `HUIT_API_KEY` from the environment. And the HUIT proxy
forwards Anthropic tool calls unchanged; probed against the live endpoint with the pinned
model:

```
HTTP 200   stop_reason: tool_use
content block types: ['tool_use']
tool_use name: Probe   input: {'ok': True, 'why': 'probe'}
```

So the capability exists on both sides of the proxy and only the client is missing it.
Two specific gaps: the request body never carries `tools`
(`src/archi/providers/huit_bedrock_provider.py:163`), and the response reader keeps only
`type == "text"` blocks (`:192-197`), so a `tool_use` block would be silently dropped even
if one arrived.

## What Changes

- `HuitBedrockChat.bind_tools(tools, *, tool_choice=None, **kwargs)` converts each tool to
  the Anthropic shape (`name`, `description`, `input_schema`) and binds it. Overriding it
  is what makes langchain-core's default `with_structured_output` stop refusing; this
  change adds **no** `with_structured_output` of its own, so the parser and the
  tool-choice convention stay langchain's rather than becoming ours to maintain.
- `tool_choice` accepts what langchain-core's default passes and what callers use:
  `"any"` and `"auto"` map to `{"type": "any"}` / `{"type": "auto"}`, a bare tool name maps
  to `{"type": "tool", "name": ...}`, and `None` omits the key so the model chooses.
- `_generate` puts `tools` and `tool_choice` into the Bedrock body when they are bound, and
  reads `tool_use` blocks out of the response into `AIMessage.tool_calls`. Text blocks keep
  their current handling, so a response mixing prose and a tool call preserves both.
- Unbound calls are byte-for-byte unchanged: with no tools bound the body carries no
  `tools` key and no `tool_choice` key. A test pins that, because this provider is on the
  agent path for HUIT Bedrock deployments and this change must not alter their requests.
- `tests/unit/test_huit_bedrock_tool_calling.py` covers the conversion, both `tool_choice`
  spellings, the response parsing, the unbound-request regression, and one end-to-end
  `with_structured_output(...).invoke(...)` against a faked transport that asserts the
  parsed dict comes back.

## Impact

- Affected code: `src/archi/providers/huit_bedrock_provider.py` and one new test module.
  No config, template, or deployment change; no other provider is touched.
- Affected capability: a new `huit-bedrock-tool-calling` spec. The QA console's own
  requirements do not change — the console already accepts any provider whose model
  supports structured output, and this makes `huit_bedrock` one of them.
- Unblocks an evaluator profile naming the RAGAS judge, which is the parity the QA console
  needs before its numbers can be compared with the RAGAS benchmark's. It does not by
  itself enable or configure any profile.
- Also removes a general limitation: no agent using a HUIT Bedrock model can call tools
  today. That is out of scope to exercise here, but the same override is what would fix it.
