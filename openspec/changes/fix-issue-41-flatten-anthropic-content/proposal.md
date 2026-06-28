## Why

When the deployment runs on the Anthropic provider (e.g. a failover from vLLM), chat
returns HTTP 200 but the answer renders as a literal Python dict — e.g.
`{'text': "I found the relevant FASRC documentation..."}` — instead of clean prose.
LangChain's `ChatAnthropic` returns message `.content` as a list of content-block dicts,
and the agent's `_message_content` normaliser stringifies each block with `str(part)`
(its `repr`) rather than extracting the `text` field. The same path on the
OpenAI/vLLM provider (where `.content` is a plain string) renders correctly, so the
defect is provider-specific and user-visible whenever dev is failed over to Anthropic.

## What Changes

- Add a new black-clean module `src/archi/pipelines/agents/message_content.py` with:
  - `flatten_message_content(content)` — flattens a list-valued `.content` by
    extracting the `text` field from content-block dicts (and passing through bare
    string parts), dropping non-text blocks (e.g. `tool_use`) instead of emitting a
    dict literal; non-list content is returned via `str()` unchanged.
  - `MessageContentMixin` — overrides `_message_content` to delegate to
    `flatten_message_content`, for concrete agents to mix in before `BaseReActAgent`.
- Wire `FASRCDocsAgent` (the dev deployment's agent, the subject of issue #41) to the
  mixin: `class FASRCDocsAgent(MessageContentMixin, BaseReActAgent)`.
- The plain-string content path (OpenAI/vLLM) is left byte-for-byte unchanged.
- Add unit coverage for the content-block-list, multiple-block, plain-string, and
  mixed/non-text cases, the mixin, and the agent wiring.

**Why a mixin instead of editing `BaseReActAgent._message_content` directly:** the
defect lives in `base_react.py`, but that module is not black-clean, and the quality
gate's black writer reflows ~560 of its low-coverage lines into any diff that touches
it, failing the ≥80% patch-coverage check (the in-place edit measured 17%). Routing the
fix through a small black-clean module + a mixin keeps the behavioural change fully
covered and leaves `base_react.py` untouched. `CMSCompOpsAgent` (whose file is likewise
not black-clean) is **not** wired here — deferred to a follow-up alongside a base_react
normalisation.

## Capabilities

### New Capabilities
- `agent-answer-rendering`: How a ReAct agent's final message content is normalised to
  a printable answer string, across providers whose message `.content` is a plain
  string vs. a list of content-block dicts.

### Modified Capabilities
<!-- None: no existing spec covers agent message normalisation. -->

## Impact

- Code: new `src/archi/pipelines/agents/message_content.py`
  (`flatten_message_content`, `MessageContentMixin`); `FASRCDocsAgent` now mixes in
  `MessageContentMixin`, so its inherited `_message_content` is overridden. The result
  feeds `_format_message` and `_build_output_from_messages` → `PipelineOutput.answer` →
  `src/interfaces/chat_app/app.py` rendering. `base_react.py` is unchanged.
- Tests: `tests/unit/test_message_content.py` (function + mixin) and a wiring assertion
  in `tests/unit/test_fasrc_docs_agent.py`.
- Dependencies: none added (no change to `pyproject.toml` / `requirements-base.txt`).
- User-facing: FASRC docs agent answers on the Anthropic provider render as prose, not a
  dict literal.
- Known gap: `CMSCompOpsAgent` still inherits the unflattened base method (deferred).
