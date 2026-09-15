# fix-issue-245-single-v1-source-list

## Why

A non-streaming `/v1/chat/completions` request that returns source documents produces **two
source sections** in one message (issue #245, raised by Codex on PR #242). The chat wrapper's
`_finalize_result` appends its own source list (`format_links_markdown(get_top_sources(...))`)
to the answer before yielding it as the `final` event's `response`; the `/v1` endpoint's
`_non_streaming_response` then takes that whole string as `final_content` and appends a second
list via `format_citations`. Both lists now share the descending, higher-is-better convention
(#208/#240 fixed the ordering half of #245), but the duplication itself stands on `dev`.

## What Changes

- `ChatWrapper.stream()`'s `final` event additionally carries the bare answer (the pipeline
  answer **without** the wrapper's appended source list) in a new `answer` field. The existing
  `response` field is unchanged — the native chat UI and every other consumer keep working as-is.
- The final-event assembly moves out of `stream()`'s inline dict literal into a new tested
  helper, `src/interfaces/chat_app/final_event.py::build_final_event` (the
  `config_fingerprint.py` thin-call-site pattern), which omits `answer` — never defaults it —
  when the pipeline output lacks one, so the real event construction is unit-tested pre-merge.
- `_non_streaming_response` in `src/interfaces/chat_app/openai_compat.py` builds the message
  content from the `final` event's `answer` when the key is present, appending
  `format_citations` output exactly once — the single citation builder for `/v1`, consistent
  with the `/v1` streaming path. If the key is ever absent (a producer regression — the two
  modules ship in one image), the endpoint uses `response` verbatim and appends nothing, so
  no arm of the logic can emit two source lists.
- The `/v1` streaming path is ruled out, not changed: `_streaming_response` accumulates chunk
  events emitted mid-pipeline and never re-emits the wrapper's finalized `response`, so it
  already emits exactly one source list (`format_citations`).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `source-citations`: adds the requirement that an OpenAI-compatible `/v1` chat completion
  containing source documents presents exactly one source list, built by `format_citations`.

## Impact

- `src/interfaces/chat_app/app.py` — `stream()`'s `final` event dict literal becomes a
  `build_final_event(...)` call (thin call site; these lines sit inside the generator body no
  unit test executes, so the diff there stays minimal).
- `src/interfaces/chat_app/final_event.py` — new tested helper assembling the `final` event.
- `src/interfaces/chat_app/openai_compat.py` — `_non_streaming_response` content selection
  (covered by `tests/unit/test_openai_compat_endpoints.py`).
- `tests/unit/test_final_event.py` — new tests for the event assembly (`answer`
  passthrough/omission, field parity).
- `tests/unit/test_openai_compat_endpoints.py` — new tests for single-source-section,
  defensive-arm, and no-sources behaviors.
- No API surface, dependency, or deployment changes. `deploy/fasrc-dev/**` untouched.
- Sequencing note from #245: independent of #244 (producer-side score normalization); this
  change neither assumes nor blocks it.
