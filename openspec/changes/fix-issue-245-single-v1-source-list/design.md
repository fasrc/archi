# Design — fix-issue-245-single-v1-source-list

## Context

Two independent source-list builders run on the same non-streaming `/v1` response:

1. `ChatWrapper._finalize_result` (`src/interfaces/chat_app/app.py:1884-1890`) appends
   `format_links_markdown(self.get_top_sources(documents, scores))` to the answer. On the
   `stream()` path it is called with `render_markdown=False`, so the yielded `final` event's
   `response` (`app.py:2699-2718`) is `answer + "\n\n---\n<details>…Show all sources…"`.
2. `_non_streaming_response` (`src/interfaces/chat_app/openai_compat.py:398-422`) sets
   `final_content` to that whole string and appends `format_citations(source_documents,
   source_scores)` — `"\n\n---\n**Sources:**\n…"` — a second list.

The `final` event already exposes the raw inputs (`source_documents`,
`retriever_scores`, extracted at `app.py:2632-2641` precisely for `/v1` citation
formatting), but not the bare answer.

Constraints:

- `app.py` is imported by one unit test file (`test_get_top_sources.py`, since #240), but the
  lines changed here sit inside the `stream()` generator body, which no unit test executes —
  every changed line there is uncovered and eats into the ≥80% patch-coverage gate. Keep its
  diff to the minimum thin call site.
- The `final` event's `response` feeds the native chat UI (which needs the wrapper's list) and
  is persisted to Postgres via `insert_conversation` before the event is yielded — both must
  not change.
- Both touched files are black-clean on `origin/dev` (verified), so in-place edits do not
  trigger reformat churn.

## Goals / Non-Goals

**Goals:**

- A non-streaming `/v1` chat completion with source documents contains exactly one source
  list.
- `/v1` citation formatting is owned by one builder — `format_citations` — for both streaming
  and non-streaming modes.
- Existing consumers of `stream()`'s `final` event observe no change to existing fields.

**Non-Goals:**

- Changing the `/v1` streaming path (already single-list; ruled out in the proposal).
- Changing the native chat UI's source list, `get_top_sources`, or
  `order_and_filter_by_similarity` (#240 already fixed the ordering half of #245).
- Visibility/threshold filtering in `format_citations` (`get_top_sources` filters documents
  marked not-visible and below-threshold; `format_citations` does not). Pre-existing `/v1`
  behavior, shared with the streaming path — out of scope, tracked as a follow-up candidate.
- #244 (producer-side score normalization) — independent; nothing here reads score direction.

## Decisions

**D1 — The wrapper exposes the bare answer as a new additive `answer` field on the `final`
event; the endpoint prefers it.**

- Alternatives considered:
  - *Endpoint strips or detects the wrapper's list in `response`* (string matching on the
    `<details>` marker): fragile coupling to presentation markup; breaks silently when the
    wrapper's format changes.
  - *Endpoint keeps `response` as-is and drops `format_citations` for non-streaming*: zero
    `app.py` diff, but `/v1` non-streaming would present the wrapper's `<details>` block while
    `/v1` streaming presents `format_citations` — the same client gets different citation
    formats depending on `stream:`; also inherits the chat UI's HTML-flavored markup in an
    API-facing payload.
  - *Wrapper stops appending links on the stream path*: changes `response` for the native chat
    UI and changes what is persisted to Postgres — unacceptable blast radius.
- The additive field keeps `response` byte-identical for every existing consumer, costs one
  line in the yield dict, and lets `/v1` build citations exactly once from the same
  `format_citations` used by streaming.

**D2 — The `final` event is assembled by a tested helper,
`src/interfaces/chat_app/final_event.py::build_final_event`, and `answer` is omitted — never
silently defaulted — when the pipeline output lacks it.** `stream()`'s 17-line event dict
literal moves into the helper; the call site passes named locals 1:1. The helper extracts
`answer` from `last_output` itself: present → passthrough (empty string included, since
`_finalize_result` reads the same `result["answer"]` and an empty pipeline answer is
legitimately empty everywhere); absent/None → the key is left out of the event, which routes
the endpoint into its defensive arm (D3) instead of accepting a fabricated `""`. This kills
the answer-loss path adversarial round 3 identified: a producer-side extraction bug can no
longer masquerade as a legitimate empty answer, and the real event construction is
unit-testable pre-merge (the `config_fingerprint.py` thin-call-site pattern this repo
prescribes for `app.py`). A `.get("answer", "")` default at the yield site was rejected for
exactly that silent-conversion defect.

**D3 — Endpoint fallback: `answer` when the key is present (including empty string); when it
is absent, `response` verbatim with NO `format_citations` append.** An explicit `is not None`
check, not truthiness: an empty answer with sources must yield a citations-only message, not
fall back to the list-bearing `response` and resurrect the duplication. The absent-key arm is
defense-in-depth, not version-skew handling — `app.py` and `openai_compat.py` ship in the same
image and run in the same process, so the field cannot legitimately be missing. If a future
producer regression drops it, the endpoint degrades to the wrapper's own single list rather
than silently recreating the duplicate-list defect. (Adversarial review round 1 rejected the
earlier fallback, which appended citations onto `response` and reproduced the bug by
construction.)

**D4 — Two test seams.** Endpoint behavior: `tests/unit/test_openai_compat_endpoints.py`
using the existing `_make_mock_chat_wrapper` event-stub pattern; the single-source-section
assertion counts citation markers — content contains exactly one `**Sources:**` block and no
`Show all sources` block; the RED state on current `dev` is the duplicate pair. Producer
assembly: a new `tests/unit/test_final_event.py` drives `build_final_event` with realistic
pipeline output and asserts `answer` passthrough, `answer` omission when absent, `response`
passthrough untouched, and field parity with the event shape `dev` emits today.

## Risks / Trade-offs

- [The changed `app.py` lines are uncovered] → they reduce to a keyword-argument passthrough
  call of in-scope locals to `build_final_event`; the assembly logic itself (field parity,
  `answer` passthrough/omission) is unit-tested in the helper, and the gate verifies patch
  coverage ≥80% before commit.
- [A future `stream()` consumer uses `answer` expecting the finalized text] → the field name
  matches the pipeline's own `result["answer"]` key it forwards; docstring comment at the
  yield site states it is the answer without the appended source list.
- [Producer/consumer integration is not exercised end-to-end pre-merge] → the two failure
  classes are handled distinctly: an *absent* `answer` (producer stops calling the helper
  correctly) routes to D3's no-append arm — the wrapper's single list, never a duplicate,
  never a lost answer; an *incorrectly populated* `answer` is what the helper's unit tests
  catch pre-merge, because the real assembly code runs under test (D2). What remains — the
  passthrough call site wiring — is covered by the blocking live verification at the first
  post-merge dev redeploy (tasks 4.4); deploying the unmerged branch to the shared dev stack
  overnight was rejected (Postgres config re-seed + corpus re-scrape).

## Migration Plan

None — additive event field plus endpoint logic, deployed together in the app image. Rollback
is a revert.

## Open Questions

None.
