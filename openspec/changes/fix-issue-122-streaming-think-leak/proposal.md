## Why

For Qwen-style models that emit reasoning terminated only by a bare `</think>`
closing tag (no opening `<think>`), the ReAct agent's streaming paths yield that
reasoning to the user as **visible text** before the `</think>` arrives. PR #121
(issue #84) fixed the stored/final answer, but the **live-streaming display**
still leaks chain-of-thought (Codex review finding 1 on PR #121). A user watching
the stream sees the model's private reasoning appear on screen; when `</think>`
finally arrives, later chunks are stripped, but the reasoning already shown is
never retracted — the chat layer forwards only truthy text chunks, so an empty
post-strip result cannot clear what was displayed.

Anchors are re-verified against `origin/dev` @ `4fb0050c` (2026-08-23). The
anchors in issue #122 were taken at `bd2d519c` and have all drifted; PR #265
(issue #235) moved this file.

## What Changes

- Add `src/archi/pipelines/agents/utils/thinking_gate.py`, a small tested helper
  that answers two questions: does the provider about to be called emit thinking,
  and is the accumulated stream still inside the pre-`</think>` window.
- In `BaseReActAgent.stream()` and `astream()`, hold ("suppress") visible text
  chunks while that window is open, and release once a `</think>` is observed in
  the accumulated content. The already-existing end-of-stream `final` event
  delivers the answer if no `</think>` ever arrives, so holding never loses text.
- **Scope the hold to providers configured to emit thinking.** Pre-`</think>`,
  reasoning bytes and a plain answer are byte-identical, so no in-band signal can
  separate them; the discriminator is the provider's configured
  `chat_template_kwargs.enable_thinking`. A provider with thinking off streams
  exactly as it does today, with no added latency.
- **Follow the request-local model override.** The gate reads
  `self.default_provider`, which `adopt_request_local_model()` rewrites per
  request, so a dropdown model switch changes the gate with it.
- No chat-layer contract change (no retract event, no start-detection heuristic).
- Add streaming tests for both the sync and async paths.

## Capabilities

### New Capabilities
- `agent-streaming-thinking`: how the ReAct agent decides what reasoning-model
  output is safe to stream to the user as visible text, so private
  chain-of-thought (including the orphan-`</think>` case) is never displayed.

### Modified Capabilities
<!-- None: no existing capability spec covers streaming thinking-content display. -->

## Impact

- **Code**: `src/archi/pipelines/agents/base_react.py` — the emit gate in
  `stream()` (:619) and `astream()` (:930), plus one gate value computed once per
  stream call. New helper `src/archi/pipelines/agents/utils/thinking_gate.py`.
  `_parse_thinking_content()` (:265) is unchanged.
- **Tests**: a new streaming test module covering both paths, plus unit tests for
  the helper.
- **Config**: no new keys. The gate reads the existing
  `services.chat_app.providers.<provider>.extra_kwargs.extra_body.chat_template_kwargs.enable_thinking`,
  the same value `src/interfaces/chat_app/config_fingerprint.py:48` reports.
- **User-facing**: on a thinking-enabled provider the live chat stream no longer
  shows pre-`</think>` reasoning. On a thinking-disabled provider nothing changes.
- **Out of scope**: the stored/final-answer sanitize already fixed in PR #121 at
  `_build_output_from_messages` (:1766/:1781); a retract-on-close chat contract; a
  start-of-reasoning content heuristic; non-streaming `invoke()` output.
