## 1. Confirm the premise

- [x] 1.1 Re-read `src/interfaces/chat_app/app.py:1635-1658` and confirm the three history-source
  branches, the refresh trim at `:1650-1652`, and the skipped append at `:1657-1658`.
- [x] 1.2 Confirm the two duplicated error-message chains at `:2019-2025` (streaming) and
  `:4668-4674` (non-streaming route) know only `408` and `403`.
- [x] 1.3 Confirm no in-repo client sends the rejected combination: `openai_compat.py:272` sends
  `is_refresh: False`; `static/script.js:781`/`:826` send `is_refresh` with a `conversation_id`.

## 2. RED — the defect and the boundaries around it

- [x] 2.1 New `tests/unit/test_chat_refresh_context.py`. Failing test: `is_refresh=True`,
  `conversation_id=None`, `external_history=None` returns `(None, 400)`. Drive
  `ChatWrapper._prepare_chat_context` with a stub `self`, stubbing `create_conversation`,
  `query_conversation_history` and `update_conversation_timestamp`. Pass a
  `client_sent_msg_ts`/`client_timeout` pair that satisfies the check at `:1654` — that unguarded
  comparison is #175 and must not be tripped or fixed here.
- [x] 2.2 Failing test: the same request calls `create_conversation` **zero** times.
- [x] 2.3 Guard test (must pass before *and* after): a refresh with `external_history` and no
  `conversation_id` is **not** rejected, and its trailing assistant turns are trimmed. This is the
  test that stops the guard from being written as "reject when `conversation_id is None`".
- [x] 2.4 Guard test: refresh **with** a `conversation_id` still trims trailing assistant turns and
  still does not append the incoming message.
- [x] 2.5 Guard test: `is_refresh=False` with no `conversation_id` still creates a conversation and
  appends the message.

## 3. GREEN — the guard

- [x] 3.1 Add the precondition. **Superseded by 9.1** — this task specified the field-presence form
  (`is_refresh and conversation_id is None and external_history is None`) placed before the history
  branches. That form is a proxy for "prior turns exist" and has three holes; the shipped guard
  tests the resolved, post-trim history instead. Kept here so the correction is legible rather than
  looking like the task was always right.
- [x] 3.2 Run 2.1–2.5 until green.

## 4. RED+GREEN — the shared error message

- [x] 4.1 Failing tests for a new module-level `_chat_error_message(error_code)`: `400` returns text
  naming the unsatisfiable refresh; `408` and `403` return their existing strings verbatim; any
  other status returns the generic server-error text.
- [x] 4.2 Implement it and replace both duplicated chains (`:2019-2025`, `:4668-4674`) with a call.
  The existing `408`/`403` strings must be reproduced exactly — assert on the constants, not on
  retyped literals.
- [x] 4.3 Assert the streaming path emits `{"type": "error", "status": 400, "message": ...}` with the
  new message, using the existing wrapper harness in `tests/unit/test_chat_override_persistence.py`
  as the model.

## 5. Mutation-check (non-vacuity)

- [x] 5.1 Delete the guard → 2.1 and 2.2 fail, and nothing else.
- [x] 5.2 Narrow the guard to `is_refresh and conversation_id is None` → 2.3 fails. This is the
  specific regression the change exists to avoid.
- [x] 5.3 Make `_chat_error_message` return the generic text for `400` → 4.1 and 4.3 fail.
- [x] 5.4 Restore and confirm green.

## 6. Docs

- [x] 6.1 Update the `is_refresh` row in `docs/docs/api_reference.md`. It currently documents the
  broken behaviour (new empty conversation, message dropped, pipeline invoked with no user turn);
  replace it with the `400` and the precondition. **The precondition stated here originally — "a
  refresh needs a `conversation_id` **or** `external_history`" — is superseded by 9.1**: either field
  can still resolve to no surviving turn (an empty named conversation, `external_history=[]`, an
  assistant-only history), and all three are rejected. The shipped wording is "needs a prior user
  turn", which is what the page says.
- [x] 6.2 Add `400` to the page's error-status coverage for both endpoints, consistent with the
  existing two-error-channels section: a real HTTP `400` from `POST /api/get_chat_response`, and an
  in-band `{"type": "error", "status": 400}` under HTTP 200 from the streaming endpoint, since
  `_prepare_chat_context` is called inside the generator.
- [x] 6.3 `cd docs && mkdocs build --strict` exits 0, and every in-page anchor still resolves.

## 7. Gate and ship

- [x] 7.1 `openspec validate fix-issue-177-refresh-requires-history --strict` passes.
- [x] 7.2 `bash scripts/gate.sh` green through the pre-commit hook, **≥80% diff coverage on changed
  lines**; never `--no-verify`. Result: 1399 passed / 1 xfailed, diff coverage **92.9%**
  (14 lines, 1 missing — the non-streaming route's helper call, which no unit test reaches).
- [x] 7.3 `git diff origin/dev -- src/interfaces/chat_app/app.py` shows no unrelated black reflow.
- [x] 7.4 Adversarial review on the branch; address what holds, push back with reasons on what does
  not.
- [x] 7.5 Open the PR to `fasrc/archi:dev` with `closes #177`, then request `@codex review`. Do NOT
  merge.

## 8. Line anchors (unplanned — surfaced while updating the docs)

- [x] 8.1 The guard and the shared-message helper shift every line below `app.py:263`, which
  invalidates **every** `app.py#L<n>` link on `docs/docs/api_reference.md` — 31 of them. Remap
  mechanically rather than by hand: build an exact old→new line map from
  `difflib.SequenceMatcher` opcodes over `origin/dev`'s `app.py` versus the branch's, then
  rewrite the URL anchors, the `app.py:<n>` display text and the short `` `:<n>` `` form.
  Content-matching is not sufficient — lines like `)` and `if include_tool_steps:` occur dozens
  of times, so only a positional diff resolves them.
- [x] 8.2 One line does not map, because this change rewrote it: the streaming error `yield`
  (old `:2024`). Point it at the new `yield {` by hand.
- [x] 8.3 Verify every remapped anchor resolves to a plausible source line, printing the target
  line's content for each. This caught a **double-mapping** bug — the range anchor
  `#L4595-L4596` was remapped by the range pass and then remapped *again* by the single-number
  pass, yielding the nonsense `#L4669-L4633`.
- [x] 8.4 Two anchors authored in #159 pointed at a blank line (`[ovrguard]`) and a closing
  paren (`[ovrwarn]`). Corrected to the `if (` and the `yield` they were describing.
- [x] 8.5 **`[modelused]` was used but never defined** — it shipped that way in #159 and renders
  as a literal `[modelused]` on `dev` today. Added the definition. The round-8 check that should
  have caught it grepped a hand-written list of the refs I remembered adding; the check is now
  generic — *any* `[label]` not followed by `(` is an unresolved reference — and reports none.

## 9. Adversarial review round 1 — two findings, both confirmed

- [x] 9.1 **[high] The guard tested a proxy, not the invariant.** `external_history is None` is a
  proxy for "prior turns exist" and admits three requests that reach the identical unsatisfiable
  state: `external_history=[]` (not `None`), an assistant-only history (emptied by the trim), and a
  `conversation_id` naming an empty conversation — the third reaching it through a branch the
  original guard never considered. Reproduced all three before fixing. The check now runs on the
  **resolved, post-trim history** (`if not history: return None, 400`), which collapses the three
  routes into one condition. My own spec said "no source of prior turns"; the implementation tested
  source *presence* instead, so the spec was right and the code did not match it.
- [x] 9.2 History resolution is now **side-effect free**: `create_conversation` and
  `update_conversation_timestamp` are deferred until the request is known to be serviceable, so a
  refusal writes nothing. Previously the `external_history=[]` path created a row *before* any
  validation could reject it. Three tests pin the side effects that must still happen (existing
  conversation is timestamped; supplied history is not; a refused refresh writes neither), because
  moving side effects is exactly the kind of change that silently drops one.
- [x] 9.3 `external_history` is copied rather than aliased — the trim pops from the resolved list,
  which previously mutated the caller's argument. Pinned by a test.
- [x] 9.4 **[low] The mechanical anchor remap produced a reversed range**, `app.py:2436-2405`: the
  range pass mapped it correctly and the single-number pass then remapped the result. I had fixed
  this same double-mapping for `#L4595-L4596` and not looked for other instances — the class again,
  not the instance. The remap is now a **single regex pass** with ranges and singles in one
  alternation, plus a `start <= end` assertion and a display-vs-URL consistency check. Both caught
  further real errors: two anchors I had hand-set to new-file coordinates were re-mapped as if they
  were old ones, and `[ovrreject]`'s display and URL had drifted apart.

## 10. PR review round 1 (#182) — three findings, all confirmed

- [x] 10.1 **[P1] Validate through the endpoints, not stubs.** The tests drove
  `_prepare_chat_context` directly and *replaced* it for the streaming assertion, so nothing
  exercised the HTTP layer where the status code is actually chosen — while the change alters live
  API status behaviour. Both view functions are now registered on a Flask app and driven with
  `test_client()`: the real `400` from `POST /api/get_chat_response`, the unchanged `408`/`403`
  mappings, and the streaming endpoint's `200` carrying an in-band `400`. This also covers the route
  line the design had accepted as a known gap — changed-line coverage is now **100%**.
  Not done: validation against a *deployed* service. That needs a redeploy of a shared environment
  for an unmerged branch, which is a human's call; the in-process route tests cover the status-code
  and channel claims that the stubs could not, and the limit is recorded in the design's risks.
- [x] 10.2 **[P2] The error message recommended an unreachable remedy.** It told callers to "supply
  the prior turns", but `_parse_chat_request` never reads `external_history` off the payload — that
  parameter reaches `_prepare_chat_context` only from in-process callers like the OpenAI-compatible
  shim. It also said "send a conversation_id", which is wrong for the empty-conversation case the
  same guard rejects. Reworded to the two levers an HTTP caller actually has, with the reasoning
  recorded at the constant so it is not "simplified" back.
- [x] 10.3 **[P2] The design still recorded the superseded guard.** Code and delta spec moved to the
  resolved-history check; `design.md` still showed the field-presence proxy and said to place it
  before history resolution — following it would reintroduce all three holes. Rewritten to the
  shipped invariant, including why the proxy fails and the general lesson.
- [x] 10.4 Swept the change artifacts for the same class rather than fixing only what was reported.
  That found two more: task 3.1 still prescribed the old guard form (now marked superseded), and a
  risk entry still claimed the route is untested (resolved by 10.1).

## 11. PR review round 2 (#182) — four findings, all confirmed

- [x] 11.1 **[P1, repeat, sharpened] The route tests injected a synthetic 400.** Round 1's route
  tests stubbed `self.chat` with a lambda, so they proved the route *maps* an error code while
  assuming the thing under change produced one. Both route tests now drive a **real `ChatWrapper`**
  with only its datastore collaborators stubbed: route → `__call__`/`stream` →
  `_prepare_chat_context` → the guard → the shared message → the response. Proven non-vacuous by
  mutation: deleting the guard now fails both route tests, which the synthetic versions could not
  have detected. Deployment validation remains outstanding and is a human's call; the design's risk
  entry says so plainly rather than implying otherwise.
- [x] 11.2 **[P2] The deferred writes broke a timing milestone.** `query_convo_history_ts` bounds the
  conversation-store work, and moving `create_conversation`/`update_conversation_timestamp` past the
  refresh check moved them out from under it — so new `timing` rows would attribute that latency to
  the next interval and stop being comparable with historical rows. A measurement series breaking
  silently is worse than a visibly wrong number. The milestone is recorded after the writes again,
  and a test asserts the ordering by checking, from inside the stubbed write, that the milestone is
  not yet set.
- [x] 11.3 **[P2] The requirement specified HTTP 400 for both endpoints.** The streaming response is
  constructed before `_prepare_chat_context` runs, so it returns HTTP 200 with an in-band `400` —
  which the route test and the API reference both assert. The normative text made the shipped
  behaviour non-compliant. Split by endpoint. The sweep for the same class then found the proposal's
  Impact line claiming "previously returned `200`, now returns `400`", which is wrong for streaming
  — it still returns `200`, with an error event instead of an answer.
- [x] 11.4 **[P2] The anchors were stale again.** The five-line comment added in round 1 shifted
  every location below it and I did not re-run the remap. Re-derived from `8db5b02a` (the last state
  where they were correct) and re-verified: every anchor prints its target line, ranges cover what
  they claim, display text matches its URL. The remap is a checked-in habit at this point, not a
  one-off — any edit to `app.py` in this repo invalidates every line anchor on that page.

## 12. PR review round 3 (#182) — four findings, all confirmed

- [x] 12.1 **The shared mapping was not actually shared.** Three of the streaming generator's own
  error paths — `ConversationAccessError`, the generic exception, and the no-output branch — kept
  hard-coded copies of the `403`/`500` text. They agreed with the mapping *by coincidence*, which is
  why the duplication survived a refactor whose stated purpose was removing it: editing a shared
  string would have silently made the endpoints disagree. All three now call `_chat_error_message`.
  Tested by **replacing the mapped entry with a sentinel** and requiring the branch to follow it —
  asserting equality with the mapped text would have passed either way. Mutation-checked: reverting
  each branch to its literal fails its own test. Out of scope and left alone: the trace-metadata
  route's `404 "conversation not found"`, a different status on a different endpoint.
- [x] 12.2 **The no-write scenario over-promised.** It said *any* rejected refresh writes nothing,
  but a refresh with valid history is still rejected *after* the writes by the timeout `408` and the
  query-limit `500`. Narrowed to the missing-history rejection, with the later paths named so the
  boundary is explicit rather than implied.
- [x] 12.3 **The 400-message scenario over-reached.** It applied the refresh-specific text to every
  `400` on either endpoint, but a missing `client_id` returns `400` before the handler is entered and
  a streaming provider-override `ValueError` emits its own in-band `400`. Neither must claim that
  prior history is missing. Scoped to this rejection.
- [x] 12.4 **The proposal still carried the superseded predicate and an unconditional `400`.** Third
  round running that an artifact lagged the implementation, and this time my own sweep missed it —
  the grep filtered out lines mentioning "streaming", which is exactly what the stale bullet said.
  Rewritten. A negative-filter sweep can hide the thing it is looking for.

## 13. PR review round 4 (#182) — two findings, both confirmed

- [x] 13.1 **A fourth copy of a mapped message.** The in-loop timeout (`app.py:2165-2169`, raised
  *during* pipeline iteration rather than before it) still emitted `CLIENT_TIMEOUT_ERROR_MESSAGE`
  directly, so a changed `408` would have been followed by the pre-pipeline check and ignored here.
  Routed through the helper and covered with the same sentinel technique; mutation-checked.
  **This is the fourth round in which I fixed the reported instances and not the class.** So this
  time the sweep was exhaustive by construction: enumerate every emission carrying a mapped status
  in the chat paths and check each one. Result — every `403`/`408`/`500` in `ChatWrapper.stream`,
  `__call__` and the two chat routes now reads the mapping. Two literals remain **by design**: the
  provider-override `ValueError`, which carries the exception's own text, and `client_id missing`,
  which is a different `400`; both were established as out of scope in round 3 and are recorded as
  such in the spec.
- [x] 13.2 **Task 6.1 still stated the superseded precondition** ("needs a `conversation_id` **or**
  `external_history`") and, unlike task 3.1, was not marked superseded — so it contradicted both the
  shipped guard and the correction in 9.1. Marked superseded with the shipped wording.

## 14. Merge `dev` after PR #179 landed

PR [#179](https://github.com/fasrc/archi/pull/179) (`c06fc45c`, issue #167 — validate the
`last_message` shape on both chat endpoints) merged into `dev` while this branch was review-clean,
touching the same two files. Merged `origin/dev` in rather than rebasing, so the fix SHAs cited in
the review threads still resolve.

- [x] 14.1 **`app.py` auto-merged; verified semantically, not just textually.** #179 adds a
  route-level `parse_last_message` guard *before* the response is constructed; this change guards
  inside `_prepare_chat_context`, which runs after. Different layers, no interaction — confirmed by
  running both changes' suites together: 58/58 pass (26 here, 32 from #179).
- [x] 14.2 **`api_reference.md` conflicted in two hunks, both resolved by content.** The
  `last_message` paragraph: this branch had only re-anchored the old "does not return a clean error"
  text, which #179 makes false — took #179's rewrite wholesale. The pre-stream error table: kept
  both rows.
- [x] 14.3 **Anchors remapped against the merged `app.py`.** 61 references in `api_reference.md`,
  4 in the test docstrings, 16 across the proposal/design/spec. The remapper had the range bug
  *again* in a new form: GitHub's anchor range is `#L4650-L4651`, whose tail is a bare `L`, and the
  dash group only matched `-` or `-#L`, so it rewrote the start and left the end — yielding
  `#L4654-L4651`. Caught by the reversed-range assertion before anything was written. The assertion
  is now scoped to refs the pass produces, because a whole-file scan trips on the reversed ranges
  task 9.4 *quotes* as evidence. `tasks.md` is excluded from the remap: its line numbers are quoted
  evidence of what was found at the time, not navigation aids.
- [x] 14.4 **Fixed a pre-existing off-by-one this verification exposed.** `[streamopen]` pointed at
  the closing `}` of the `headers` dict, one line above the `return Response(stream_with_context(…))`
  it names. Shipped that way in #159. Verification was 11 anchors spot-checked by comparing the
  source line text pre- and post-merge; all 11 matched, which is what surfaced the one that had
  never matched.
- [x] 14.5 **De-enumerated the route-level `400` carve-out in the spec.** The scenario from 12.3
  listed the two other `400`s by name; #179 added a third and would have made the list wrong. Restated
  as a rule about *where* the rejection is decided — route-level rejections keep their own text,
  handler-level ones read the shared mapping — so a `400` added later cannot fall out of compliance
  silently. Same defect class as 12.4 and 13.1: a closed enumeration of an open set.
- [x] 14.6 **Documented that `400` now appears on both channels of the streaming endpoint.** A
  malformed `last_message` is a real HTTP `400`; a refresh with nothing to refresh is an in-band
  `400` under HTTP `200`. Neither PR's review could have caught the ambiguity, since each saw only
  its own half.
