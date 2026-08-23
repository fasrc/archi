## Context

All anchors below are verified against `origin/dev` @ `4fb0050c` (2026-08-23).

`BaseReActAgent.stream()` (:453) and `astream()` (:778) turn an accumulating LLM
response into `PipelineOutput` events for the chat UI. On each delta they append
to `accumulated_content` and — when the provider does not report a separate
`reasoning_content` field — call `_parse_thinking_content(accumulated_content)`
(:613 sync, :924 async) to split it into `(visible_content, thinking_content)`.
The visible half is emitted as an `event_type: "text"` event, but only when it
differs from `last_visible_content` (:619 sync, :930 async).

`_parse_thinking_content()` (:265) handles the orphan case by moving everything up
to and including the **last** `</think>` into thinking. That works once a
`</think>` is present. The leak happens before it arrives: the accumulated prefix
is pure reasoning with no tag to strip, so `visible_content` **is** the reasoning
and is emitted. When `</think>` later arrives the parse returns the real answer,
but the reasoning already reached the screen, and the chat layer forwards only
truthy text chunks, so an empty post-strip result cannot retract it. PR #121 fixed
only the stored/final answer at `_build_output_from_messages` (:1766/:1781).

## Goals / Non-Goals

**Goals:**
- No pre-`</think>` reasoning is ever emitted as a visible `text` event, in both
  `stream()` and `astream()`.
- No bare `</think>` tag ever appears in an emitted visible chunk.
- A provider that does not emit thinking keeps streaming chunk-by-chunk with no
  added latency.
- Both paths share one decision function, so they cannot drift.

**Non-Goals:**
- Changing `_parse_thinking_content()` semantics or the stored-answer sanitize.
- A chat-layer retract event, or a start-of-reasoning content heuristic.
- Any change to non-streaming `invoke()`.
- The `reasoning_content` provider path (Ollama), which already keeps reasoning
  separate at :606 and does not leak.

## Decisions

**Decision 1: the predicate recorded in July is a no-op, and is replaced.**

The July design gated suppression on "`_parse_thinking_content` has produced
thinking content **and** no `</think>` has been seen". Those two conditions are
mutually exclusive. `_parse_thinking_content` produces thinking content only from
a `<think>...</think>` pair or an orphan `</think>`; with no tag seen yet it
returns `(text, "")` — empty thinking. So the predicate is false during exactly
the window that leaks, and implementing it literally changes no behavior. This is
recorded because the acceptance test in issue #122 would still have failed against
a faithful implementation of the old plan.

**Decision 2: the discriminator is configuration, not content (operator, 2026-08-23).**

Before the first `</think>`, reasoning bytes and a plain answer are
indistinguishable — the same characters in the same order. Any content-based rule
is either a start-detection heuristic (rejected in issue #122) or an
unconditional hold, and an unconditional hold stops every plain answer on every
model from streaming incrementally, which contradicts the issue's own second
acceptance criterion. So the signal must come from outside the stream.

The signal already exists: `chat_template_kwargs.enable_thinking`, resolved for
the default provider by `src/interfaces/chat_app/config_fingerprint.py:48`. A
Qwen3 deployment with thinking enabled has the opener pre-filled by the chat
template, which is precisely why the model emits a closing tag with no opener —
so the orphan case and the config flag describe the same deployments.

This keeps the operator's original Option 1 mechanism (suppress-until-decided) and
its stated cost model ("latency only in the ambiguous case"), and defines
"ambiguous" as "this provider can emit reasoning at all".

**Decision 3: the gate reads `self.default_provider`, and is provider-granular.**

`adopt_request_local_model()` (:1641) rewrites `self.default_provider` and
`self.default_model` on the request-local view before the stream runs. Reading the
gate from `self.default_provider` therefore tracks a request-local **provider**
switch with no extra plumbing. Reusing `resolved_enable_thinking()` as-is would
**not**: it resolves `services.chat_app.default_provider` from config, which is the
configured default, not the provider this request will call. The new helper takes
the provider name explicitly for that reason. This is the same class of defect as
issue #262, which is why it is called out rather than left implicit.

The gate is **provider-granular, not model-granular**, and that matches the
mechanism rather than merely the config layout. A provider block holds a `models`
list and one `extra_kwargs` (`src/cli/templates/base-config.yaml:110`,
`deploy/fasrc-dev/config.example.yaml:102-113`); `enable_thinking` lives in
`extra_kwargs.extra_body.chat_template_kwargs`, which is spread verbatim into the
request body for **every** model called through that provider. There is no
per-model `enable_thinking` in the schema, so a model-keyed gate would key on data
that does not exist. A switch between two models of one provider therefore leaves
the gate unchanged — correctly, because it also leaves the transmitted kwarg
unchanged. The cost of that granularity is recorded as a risk below.

**Decision 4: compute the gate once per stream call, not per delta.**

The provider cannot change mid-stream — `adopt_request_local_model()` runs before
`stream()` — so the config walk happens once next to the other stream-local state
(`last_visible_content`), and the per-delta cost is one boolean test plus one
substring check.

**Decision 5: release on `</think>`; the existing final event covers the rest.**

"Decided" means a `</think>` has appeared in `accumulated_content`. There is no
separate end-of-stream release path to build: `stream()` already emits a `final`
event whose `final_answer` is parsed from the accumulated content (:726-746, and
:1047-1055 in `astream()`). If a thinking-enabled provider never emits a
`</think>`, the held text is still delivered there — as one event at the end
rather than incrementally. On a stream that completes normally, nothing is lost;
only the incremental display is. Streams that end early are Decision 7.

Two consequences of the existing final path, both verified rather than assumed:

- A **non-empty** held answer is delivered by the `final` branch at :761-768.
- An **orphan-only** stream (reasoning, `</think>`, then end) parses to an empty
  `final_answer`, so :769-776 takes the fallback and
  `_build_output_from_messages()` substitutes the placeholder "No answer generated
  by the agent." (:1783). The final answer in that case is therefore the
  placeholder, **not** an empty string — the behavior PR #121 deliberately
  installed, as the comment at :1779 records. The spec scenario asserts the
  placeholder for that reason; asserting an empty answer would be a permanently
  red test against a contract this change does not touch.

**Decision 6: put the logic in a helper module, not inline in `base_react.py`.**

`base_react.py` is 1900+ lines and is a known diff-coverage hazard. Two small pure
functions in `src/archi/pipelines/agents/utils/thinking_gate.py` are directly
unit-testable and keep the `base_react.py` diff to a handful of lines, mirroring
how `utils/context_budget.py:245` holds the config walk for the #235 bound.

**Decision 7: on an early error exit, discard the held text — do not flush it.**

`stream()` and `astream()` do not always reach the final block. Both return early
on `GraphRecursionError` and on a context-overflow exception, yielding only the
error output (`:628-693` sync, `:938-1003` async). Held text is dropped on those
paths, so Decision 5's guarantee is scoped to normal completion.

Discarding is the correct behavior here, not a gap to patch. Held text is by
definition text that never reached a `</think>`, so the change cannot tell whether
it is an answer or reasoning. Flushing it on the way out would leak exactly the
chain-of-thought this change exists to suppress, and it would leak it in the
degraded case, where the user is least able to tell reasoning from an answer. The
cost is bounded and only applies to thinking-enabled providers: a partial answer
that would previously have appeared before the error message now does not, and the
error output itself is unchanged. The spec pins this as a scenario so a later
"flush on error" refactor is caught.

## Risks / Trade-offs

- **[A thinking-enabled provider answers with no `</think>`]** → The whole answer
  is held to the `final` event and appears at once instead of incrementally. This
  is the accepted cost of Decision 2, and it is bounded: the answer still arrives,
  and only thinking-enabled providers are affected. Pinned by a test.
- **[Config says thinking is off but the model emits reasoning anyway]** → The
  leak persists for that deployment. This is a deliberate limit of a config-keyed
  gate; the fix is to correct the config, which
  `config_fingerprint.py` already surfaces on `/api/health`. Noted in the spec.
- **[One provider serves both a thinking and a non-thinking model]** → Every model
  on that provider is gated, so the non-thinking model's plain answers are held to
  the `final` event instead of streaming incrementally. The schema offers no
  per-model `enable_thinking` to key on (Decision 3), and the same provider-level
  kwarg is sent for both models regardless, so this is a limit of the config shape
  rather than of the gate. The degradation is bounded — the answer still arrives —
  and the remedy available today is to declare the two models as two providers.
- **[The gate is inert on fasrc-dev as currently configured]** → That deployment
  sets `enable_thinking: false` (`deploy/fasrc-dev/config.example.yaml:113`) as the
  standing workaround for this very leak, so `thinking_possible` is false and
  nothing is held. The change is preventive there: it closes the leak for the day
  an operator turns thinking back on, and for any deployment that already has.
  This is recorded so no one reads a quiet dev stack as evidence the fix works.
- **[No standing deployment exercises the gate]** → `AGENTS.md:58-63` requires an
  end-to-end check against a running deployment, and neither default surface can
  provide a meaningful one. The PR preview stack runs Ollama
  (`.github/workflows/pr-preview.yml:228`), which reports reasoning through the
  separate `reasoning_content` field and so never takes the leaking branch at all;
  fasrc-dev sets `enable_thinking: false`. A validation that proves anything needs
  a provider deliberately configured with `enable_thinking: true` against an
  OpenAI-compatible reasoning endpoint. Task group 5 states that precondition
  rather than letting a green run on an unaffected stack read as proof.
- **[Malformed or absent config]** → The helper walks each level with an
  `isinstance` check and returns `False` (stream as today) rather than raising.
  A streaming path must not crash on a config typo.
- **[Sync/async drift]** → Both call the same `hold_visible()`; mirrored tests
  cover both paths.
- **[Over-suppression of an answer mentioning the word "think"]** → The predicate
  keys on the literal `</think>` tag, never on the word.

## Migration Plan

Pure in-process code change to one module plus a new helper and tests. No data,
config, or API migration; no new config keys. Rollback = revert the branch.
Verified by `bash scripts/gate.sh` (black/isort, pytest, ≥80% diff coverage vs
`origin/dev`). No redeploy is needed to land the PR; the live dev chatbot picks the
change up on its next redeploy, since it runs baked site-packages code.

## Open Questions

- None. Decision 2 was taken with the operator on 2026-08-23 and supersedes the
  predicate recorded on 2026-07-23.
