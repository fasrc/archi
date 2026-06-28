## Context

`BaseReActAgent._message_content` (`src/archi/pipelines/agents/base_react.py:1278-1283`)
normalises a `BaseMessage`'s `.content` to a printable string:

```python
def _message_content(self, message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        content = " ".join(str(part) for part in content)
    return str(content)
```

LangChain providers disagree on the shape of `.content`:
- `langchain_openai.ChatOpenAI` (used for vLLM/`openai_compat`) → `.content` is a `str`.
- `langchain_anthropic.ChatAnthropic` → `.content` is a `list` of content-block dicts,
  e.g. `[{"type": "text", "text": "..."}]`.

For the list case, `str(part)` renders each dict's Python `repr`, so the user sees
`{'text': "..."}` instead of prose. The method feeds `_format_message` (logging) and
`_build_output_from_messages` → `PipelineOutput.answer`, which the chat app renders
verbatim.

## Goals / Non-Goals

**Goals:**
- Flatten list-valued `.content` by extracting the `text` of each content-block dict.
- Pass through bare string parts within a list unchanged.
- Drop non-text blocks (e.g. `tool_use`) from the printable string — never emit a dict
  literal.
- Leave the plain-string path byte-for-byte unchanged (OpenAI/vLLM unaffected).

**Non-Goals:**
- No change to how blocks are stored or to tool-call handling elsewhere.
- No new dependency; no provider-layer changes.
- No live-deployment verification in this change (optional, separate).

## Decisions

- **Deliver via a new module + mixin, not an in-place `base_react.py` edit.** The defect
  is in `BaseReActAgent._message_content`, but `base_react.py` is not black-clean: the
  gate's black writer reflows ~560 of its low-coverage lines into any diff that touches
  the file, and the resulting patch coverage (17%) fails the ≥80% gate. So the logic
  lives in a new black-clean module `message_content.py` as a pure function
  `flatten_message_content`, plus a `MessageContentMixin` whose `_message_content`
  delegates to it. Concrete agents opt in by listing the mixin first in their bases
  (`class FASRCDocsAgent(MessageContentMixin, BaseReActAgent)`), so it wins MRO over the
  base method. Only `FASRCDocsAgent` (issue #41's agent, and a black-clean file) is wired
  now; `CMSCompOpsAgent`'s file is also non-black-clean, so it is deferred to a follow-up
  that normalises `base_react.py` and moves the override to the base.
- **Extract `text` per block.** For each part of a list `.content`: if it is a `dict`
  with a `"text"` key, append `str(part["text"])`; if it is a `str`, append it as-is;
  otherwise skip it (non-text block). Join the collected pieces with a single space —
  matching the existing join separator so multi-block text reads naturally.
- **Why drop, not stringify, non-text blocks.** A `tool_use` block has no human-readable
  text; stringifying it reproduces the original bug (a dict literal in the answer).
  Dropping keeps the answer clean. The textual content is what the user needs.
- **Keep the `str()` fallback for non-list content.** Preserves current behavior for
  `str`, `None`, and any scalar `.content`.

## Risks / Trade-offs

- **A list containing only non-text blocks yields an empty string.** Acceptable: such a
  message has no prose to show, and an empty string is preferable to a dict literal.
  Today this is rare for a *final* answer message (the case that reaches the user).
- **Block dicts without a `"text"` key but with other text-bearing keys** are dropped.
  Acceptable for the Anthropic text-block shape, which always uses `"text"`; broaden
  later only if a real provider needs it.
