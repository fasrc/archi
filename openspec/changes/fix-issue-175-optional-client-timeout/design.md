## Context

`ChatWrapper._prepare_chat_context` rejects a request whenever
`server_received_msg_ts.timestamp() - client_sent_msg_ts > client_timeout`
(`src/interfaces/chat_app/app.py:1710`). `_parse_chat_request` coerces absent timing fields
to `0` (`:4654-4655`), so a caller who omits them is measured as "sent at the Unix epoch,
willing to wait zero seconds" and is always rejected. The streaming loop asks the same
question of the same variable but guards it — `if client_timeout and ...` (`:2156`) — so the
file already contains the intended reading of `0`; the defect is that the earlier site does
not share it.

Three premises stated in issue #175 have drifted since it was filed on 2026-07-31 and were
re-checked against `origin/dev` at `2d532e32` before this design was written:

1. **Line numbers moved.** The guard is `:1710` (issue says 1654), the coercion `:4652-4655`
   (issue says 4593-4596), the streaming twin `:2156` (issue says 2101). Symbol names, not
   line numbers, are the durable anchors.
2. **The path is no longer untested.** The issue states no unit test reaches
   `_prepare_chat_context`'s body and that the module is ~14% covered. PR #182 (issue #177)
   has since merged `tests/unit/test_chat_refresh_context.py`, which drives the real method
   with a stub `self` via a `_wrapper()` helper, plus route-level tests through real Flask
   view functions. Its `_prepare()` helper even documents sending a matched timing pair
   specifically to keep "this unrelated bug" out of its results. The test work for this
   change is therefore *mirroring an established pattern*, not building one.
3. **The black-reflow trap does not apply.** `black --check src/interfaces/chat_app/app.py`
   reports the file already clean, so an in-place edit will not reflow unrelated code. The
   issue's instruction to route through a black-clean seam is a precaution that this file
   does not currently require.

## Goals / Non-Goals

**Goals:**
- A request omitting either or both timing fields is answered instead of rejected.
- A request that supplies a real, genuinely-exceeded deadline is still rejected with 408.
- The two timeout checks are cross-referenced in code so they cannot silently diverge again.
- `docs/docs/api_reference.md` describes the fields as optional, without losing the parts of
  the current warning that remain true after the fix.
- New executable lines are covered, satisfying the ≥80% diff-coverage gate on a file whose
  handler path starts largely uncovered.

**Non-Goals:**
- Changing `_parse_chat_request`'s `0` coercion to `None` (issue #175 step 5). See Decision 2.
- Supplying a default deadline when the client declares none. "No deadline" is the contract,
  not "the server's configured 600s".
- Any change to the streaming loop's own timeout behaviour at `:2156` beyond adding a comment.
- Raising coverage of `app.py` generally. Only the changed lines are in scope.

## Decisions

### Decision 1 — Guard the comparison on both timing values, mirroring `:2156`

The check becomes conditional on a client deadline having actually been supplied: a falsey
`client_timeout` **or** a falsey `client_sent_msg_ts` disables it.

Guarding on `client_timeout` alone is insufficient and would leave one of the issue's three
reproduction rows still failing — a caller sending `client_timeout: 600000` with no
`client_sent_msg_ts` is measured from the epoch, and `<seconds since 1970> > 600` is true.
A timeout without a send time has no baseline to measure elapsed time from, so the honest
reading is "no usable deadline" rather than "expired".

*Alternative considered — guard only `client_timeout`, matching `:2156` byte-for-byte.*
Rejected: `:2156` measures from `stream_start_time`, a server-side clock that is always
present, so it has no missing-baseline case to handle. `_prepare_chat_context` measures from
a caller-supplied value that may be absent. Copying the guard literally would import an
assumption that does not hold at this site.

### Decision 2 — Keep the `0` sentinel; do not coerce absent fields to `None`

Issue #175 step 5 invites making "absent" distinguishable from "zero" by parsing to `None`.
This is rejected, and the rejection is the load-bearing part of this design.

`client_sent_msg_ts` does not stop at the guard. It flows into
`datetime.fromtimestamp(client_sent_msg_ts, tz=timezone.utc)` at `:2532` (streaming) and
`:4741` (non-streaming), and the result is written to a timing row through `insert_timing`
(`:1508`). `datetime.fromtimestamp(None)` raises `TypeError`. Coercing to `None` would
therefore convert a wrong-status-code bug into a hard HTTP 500 on exactly the requests this
change is meant to fix — a strictly worse failure, and one the guard's own tests would not
catch because they call `_prepare_chat_context` directly and never reach `:2532` / `:4741`.

Distinguishing absent-from-zero has no consumer that needs it: no caller can send
`client_timeout: 0` meaningfully, since a zero-second deadline is expired on arrival by
definition. `0` and absent therefore *should* collapse, which is what `:2156` already assumes.

*Alternative considered — parse to `None` and additionally guard the two `fromtimestamp`
sites.* Rejected as scope the issue does not require: it widens the diff across three call
sites in a file with almost no handler coverage, in exchange for a distinction nothing reads.

### Decision 3 — New test module mirroring `test_chat_refresh_context.py`

Add `tests/unit/test_chat_timeout_guard.py` using the same `object.__new__(ChatWrapper)`
stub-`self` construction as the merged refresh tests, stubbing `create_conversation`,
`query_conversation_history` and `update_conversation_timestamp`. Four cases: both fields
absent, each one absent alone, and an explicitly-exceeded deadline that must still 408.

The fourth case is the one that gives the suite teeth. Without it, deleting the check
outright passes every other test — so it is the test that distinguishes "corrected the guard"
from "removed the guard", which is exactly the regression a reviewer cannot see in a
one-line diff.

*Alternative considered — extend `test_chat_refresh_context.py`.* Rejected: that module is
about refresh semantics and states so in its docstring. But it must not be left alone
either — its `_prepare()` helper documents deliberately avoiding this bug, and that comment
becomes misleading the moment the guard lands.

### Decision 4 — Preserve the endpoint-divergence documentation when removing the warning

The admonition at `docs/docs/api_reference.md:45-85` mixes two subjects. Most of it describes
the #175 defect and is retired by this change. One sub-section — "How the rejection reaches
you differs by endpoint" — documents that a 408 arrives from `/api/get_chat_response` as an
HTTP status but from `/api/get_chat_response_stream` as HTTP 200 plus an
`{"type": "error", "status": 408}` NDJSON event (`:2075`). That divergence is a property of
the streaming transport, not of the bug, and remains true for the explicit-deadline 408 that
this change keeps. Deleting the admonition wholesale would destroy accurate documentation
while fixing an unrelated defect.

The sub-section is therefore relocated — to the timeout discussion in the streaming endpoint's
own section — rather than deleted.

Mechanically, all four reference-link definitions used by the admonition (`[parse]`,
`[check]`, `[streamerr]`, `[stream]`, at `:87-90`) are referenced exactly once each, from
inside the admonition. `[streamerr]` must survive with the relocated sub-section; the other
three are removed with the text that used them. `[refreshguard]` is used elsewhere on the
page and must not be touched.

## Risks / Trade-offs

- **A wholesale deletion of the docs admonition loses still-true content** → Decision 4
  relocates the endpoint-divergence sub-section; the spec's "obsolete warning is removed"
  scenario is scoped to the #175 material specifically, not the whole block.
- **Removing rather than correcting the check would pass a naive test suite** → the
  explicitly-exceeded-deadline test (Decision 3) fails if the check is deleted, and the spec
  states this as a normative requirement.
- **Diff coverage on a ~14%-covered file** → the change adds only a guard clause and comments
  to `app.py`; the four unit tests execute both branches of the new condition. Verify with the
  gate's diff-coverage output on the changed lines, not the project total.
- **Orphaned or dangling reference links after the docs edit** → the link definitions are
  enumerated in Decision 4 with their usage counts; the tasks check for both dangling uses and
  unused definitions after editing.
- **The two checks drift apart again** → the cross-reference comments are a normative
  requirement, not a nicety, and each names the other's differing baseline so a future reader
  does not "unify" them into a single wrong check.
- **Cross-change reconciliation** → unarchived change `fix-issue-138-chat-docstring-payload-shape`
  carries a `chat-api-request-contract` delta requiring the timing fields be marked "required
  in practice **for as long as #175 is open**". The clause self-retires, but a human archiving
  both changes merges them into one capability and should drop that requirement then. Out of
  scope to edit another change's artifacts here.

## Migration Plan

No data migration, no config change, no deploy coupling. The change strictly widens the set of
accepted request bodies: every request accepted before is still accepted with identical
behaviour, so no client needs updating and rollback is a plain revert.

## Open Questions

None blocking. The one judgement call — whether the API reference should keep the timing
fields in its runnable example (as latency-accounting fields) or drop them to demonstrate that
they are now optional — is settled in the tasks as *keep them, still generated at send time*,
so the example continues to exercise the real deadline path rather than only the new
no-deadline path.
