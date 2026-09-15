## Why

`ChatWrapper.stream` checks the client deadline *inside* its consume loop
(`app.py:2174`, `if client_timeout and time.time() - stream_start_time > client_timeout`),
and Python cannot reach that `if` until the upstream generator yields. A provider that
accepts the request and then stalls without emitting an event blocks
`next()` on the `for output in self.archi.stream(...)` header at `app.py:2161-2165`
forever: no 408 event, no trace closure, and a `client_timeout` the caller explicitly
declared is silently unenforced. The comment at `app.py:2166-2173` already records the
limitation and points at issue #191.

No provider-level timeout exists to fall back on — `grep -rn "request_timeout\|timeout="
src/archi/providers/ src/archi/pipelines/` returns nothing — so nothing else in the stack
bounds the wait. This is pre-existing behaviour, not a regression from #185, and the
in-loop check remains correct for what it covers (a slow stream); it simply cannot see a
generator that never yields.

## What Changes

- Enforce the deadline around **advancement** of the upstream generator, not only around
  the events it produces. Each `next()` is submitted to a single-worker
  `ThreadPoolExecutor` and awaited with `future.result(timeout=remaining)`; when the
  deadline expires while the provider blocks, the stream emits the same in-band
  `{type: error, status: 408}` event and closes the trace exactly as the in-loop path
  does today (`status="error"`, `cancelled_by="system"`,
  `cancellation_reason="Client timeout"`).
- Bind the deadline to a monotonic clock imported as `from time import monotonic`, not
  `time.monotonic()`. The seam is load-bearing, not stylistic — see Decision 2 in
  `design.md`.
- Take the worker path **only when `client_timeout` is truthy**. A caller that declared no
  deadline iterates the generator directly, exactly as before — no thread, no executor, no
  behaviour change.
- Retire the caveat this defect forced into the published contract: the `client_timeout`
  row in `docs/docs/api_reference.md` no longer warns that a stalled provider escapes the
  deadline, and the code comment at `app.py:2166-2173` no longer describes the gap it used
  to.
- Add the stall test the existing suite deliberately omits — a generator that really
  sleeps past a short `client_timeout`, with **no clock monkeypatching**, so the assertion
  is about wall-clock enforcement rather than about an arithmetic branch.
- **Explicitly out of scope:** threading `client_timeout` into provider construction
  (Option B in the issue), changing the in-loop slow-stream check, and any `deploy/`
  change.

## Capabilities

### New Capabilities
- `chat-api-request-contract`: extended with what the streaming endpoint guarantees about
  a declared client deadline when the *provider* — not the stream — is the thing that
  stops. Not yet present in `openspec/specs/`; changes
  `fix-issue-138-chat-docstring-payload-shape`, `fix-issue-175-optional-client-timeout`
  and `fix-issue-195-timing-bool-nonfinite` also carry unarchived deltas for it (see
  Impact).

### Modified Capabilities
<!-- None. No capability in openspec/specs/ changes; the related requirements live in the
     three unarchived deltas named above, which is a cross-change reconciliation concern
     rather than a requirement modification here. -->

## Impact

**Code**
- `src/interfaces/chat_app/app.py` — `ChatWrapper.stream`: the loop header at `:2161-2165`
  becomes an explicit advance, plus a `from concurrent.futures import ThreadPoolExecutor`
  and a `from time import monotonic` import. The ~325-line loop **body** (`:2193-2487`)
  keeps its current indentation level and is not otherwise touched, which both keeps the
  diff small and keeps `black` from reflowing unrelated code — a one-line edit to this file
  has previously produced ~17% diff coverage that way. Verified black-clean at
  `origin/dev@0a157cdc` before starting.
- `tests/unit/test_chat_timeout_guard.py` — a new stall test alongside the existing
  classes.

**Docs**
- `docs/docs/api_reference.md:36` — the `client_timeout` row's "bounds a slow stream but
  **not** a provider that stalls" clause and its issue-#191 link.

**Behaviour** — a request that previously hung until the provider returned now ends with a
408 event once its declared deadline passes. A request with no `client_timeout` is
unaffected, and so is every request whose provider emits within budget. Nothing that
currently succeeds starts failing.

**Resource note (accepted, not solved)** — the abandoned worker thread keeps running until
the provider call finally returns, so this bounds *client-visible latency*, not
server-side resource usage. The operator resolved this trade-off on 2026-08-10 in favour
of Option A; `design.md` Risks records the interpreter-shutdown consequence that follows
from it, and the PR body must state it.

**Validation** — end-to-end streaming validation against a live deployment is
**deferred to post-merge redeploy** by operator decision (issue #191, 2026-08-10). Issue
acceptance criterion 7 was removed by that decision; the gate plus the new unit test are
the landing evidence, and the PR body must carry the deferral note.

**Cross-change** — the `#191` reference this change removes from
`docs/docs/api_reference.md` is the same one `fix-issue-195-timing-bool-nonfinite`
task 5.2 deliberately *preserved* ("keep the `issue #191` reference to the stalled-provider
limit, which is unrelated and still accurate"). That instruction was correct when written
and this change is what retires it; whoever archives these deltas into one
`chat-api-request-contract` capability must reconcile them rather than treat the removal as
drift.
