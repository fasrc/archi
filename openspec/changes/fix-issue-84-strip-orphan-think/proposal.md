## Why

`BaseReActAgent._parse_thinking_content` only strips **balanced** `<think>…</think>`
pairs. When Qwen3 reasoning is enabled, the chat template prefills the opening `<think>`
into the prompt, so the model's *output* carries only an **orphan closing `</think>`**
with no matching open tag. That orphan is never matched, so the chain-of-thought
preceding it survives into the visible answer. This already happened on the FASRC
`archi-dev` deployment: users received answers full of ReAct narration with three orphan
`</think>` tags. A stale-config restart fixed the live symptom; this change is the
defense-in-depth follow-up so a stray `</think>` can never reach a user again even if
thinking is accidentally re-enabled (bad config, a redeploy dropping the flag, or a model
template that emits stray tags).

## What Changes

- Harden `BaseReActAgent._parse_thinking_content` (`src/archi/pipelines/agents/base_react.py`)
  so that, in addition to removing balanced `<think>…</think>` pairs, it also removes
  reasoning demarcated by orphan `</think>` closing tags: everything up to and including
  the **last** remaining `</think>` is treated as thinking; only text after it is visible.
- The removed orphan reasoning is accumulated into the returned `thinking_content` so it is
  still captured (for logs / thinking panes), just not shown as the answer.
- Add unit coverage for `_parse_thinking_content` (currently untested): balanced-pair
  regression, single orphan, multiple orphans (the real-incident shape), no-tags, and
  empty-string cases.
- Scope guard: only `</think>`-demarcated reasoning is removed. Untagged residual model
  prose is out of scope (that is governed by `enable_thinking` / the agent prompt).

Not breaking: the balanced-pair behavior is preserved exactly; the single method fix
covers both the sync `stream()` and async `astream()` paths, which share it.

## Capabilities

### New Capabilities
- `react-thinking-sanitization`: The ReAct agent's visible answer never contains
  chain-of-thought reasoning delimited by `<think>`/`</think>` tags, including orphan
  closing tags with no matching open tag; the reasoning is still captured separately.

### Modified Capabilities
<!-- None: no existing capability owns think-tag stripping. -->

## Impact

- **Code:** `src/archi/pipelines/agents/base_react.py` — the `_parse_thinking_content`
  method body only (single definition at ~line 195; callers in `stream()` and `astream()`
  are unchanged).
- **Tests:** new `tests/unit/test_base_react_thinking_parse.py`.
- **Behavior:** cleaner chat answers when thinking output leaks; no change on the normal
  (balanced-pair / no-tag) path. Behavior-only, no API/config/dependency change.
