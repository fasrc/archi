## 1. Confirm the premise on the current tree

- [x] 1.1 Re-read `_milliseconds_to_seconds` in
      `src/interfaces/chat_app/request_validation.py` (it is at `:21-31` on
      `origin/dev@3040e608`; re-anchor on the symbol name if it moved) and confirm the falsey
      guard `if not value: return 0` still precedes the `value / 1000` division. That ordering
      is the reason the boolean check has to go first — if it changed, stop and report rather
      than proceeding.
- [x] 1.2 Reproduce all four boolean cases without any app machinery:
      ```
      python -c "from src.interfaces.chat_app.request_validation import parse_client_timeout as p, parse_client_sent_msg_ts as q; print(p(True), p(False), q(True), q(False))"
      ```
      Expect `0.001 0 0.001 0`. All four must reproduce; if any already raises, stop and report.
- [x] 1.3 Reproduce the non-finite cases and record which are *already* refused:
      `parse_client_timeout` returns `inf`, `-inf` and `nan` verbatim, while
      `parse_client_sent_msg_ts` already raises `InvalidClientTiming` for all three via the
      `datetime.fromtimestamp` representable-time check. Confirm both halves — the change moves
      the `client_sent_msg_ts` refusal earlier rather than introducing it, and that distinction
      belongs in the commit body.
- [x] 1.4 Confirm the decoder really admits these tokens:
      `python -c 'import json; print(json.loads("{\"a\": NaN, \"b\": Infinity}"))'` prints
      `{'a': nan, 'b': inf}`. If it raises, the non-finite half of this change has no reachable
      caller and should be dropped with a note, not implemented anyway.
- [x] 1.5 Confirm no in-repo caller sends a boolean or a non-finite value:
      `grep -rn "client_timeout\|client_sent_msg_ts" src/interfaces/chat_app/static/ src/interfaces/chat_app/openai_compat.py`
      — the JS clients send `Date.now()` and a numeric constant, and `openai_compat.py`
      synthesizes `now.timestamp()`. Record the finding; do not change any caller.

## 2. Red tests first — booleans

- [x] 2.1 Add the four boolean cases to
      `tests/unit/test_chat_timing_field_validation.py::TestNormalizationItselfCannotRaise`'s
      `CASES` list: `("client_sent_msg_ts", True)`, `("client_sent_msg_ts", False)`,
      `("client_timeout", True)`, `("client_timeout", False)`. The existing `_post` harness
      already drives both routes through the real `_parse_chat_request` with a real JSON body,
      so this buys route-level coverage of both endpoints for both fields.
- [x] 2.2 Run the file and **watch all eight new parametrizations fail** (four cases × the
      non-streaming and streaming tests). Record the actual failure mode in the commit body —
      they fail with a 200/408 rather than 400, which is the point: the current code accepts
      them. Do not touch `request_validation.py` yet.
- [x] 2.3 Note in the class docstring (or a short comment on the boolean cases) *why* `False`
      is listed separately from `True`: it is falsey, so it exercises the ordering constraint
      rather than the type check, and a fix placed after the falsey guard leaves it green-by-
      accident. A reader deleting the `False` cases as redundant would remove the only test
      that pins the ordering.

## 3. Red tests first — non-finite values

- [x] 3.1 Add the non-finite cases for `client_timeout` to the same `CASES` list:
      `float("inf")`, `float("-inf")`, `float("nan")`. These are the genuinely unguarded ones.
      Watch them fail.
- [x] 3.2 Add the three matching `client_sent_msg_ts` cases. These already **pass** before the
      fix, because `datetime.fromtimestamp` refuses them — confirm that is why, so the change
      does not get credit for a rejection it did not add, and so a later regression in
      `_milliseconds_to_seconds` cannot hide behind the downstream check.
- [x] 3.3 Do **not** write any assertion that compares a `NaN` result to a value. `nan == nan`
      is `False`, so such an assertion is vacuous and passes regardless. Every non-finite case
      here is a rejection, so assert on status and the field name in the error message.
- [x] 3.4 Add the direct-call unit assertions the issue's acceptance criteria name, alongside
      the route-level ones: `parse_client_timeout(True)`, `parse_client_timeout(False)`,
      `parse_client_sent_msg_ts(True)` and `parse_client_sent_msg_ts(False)` each raise
      `InvalidClientTiming`.

## 4. Green — the two checks

- [x] 4.1 Add `isinstance(value, bool)` as the **first statement** in
      `_milliseconds_to_seconds`, raising `InvalidClientTiming` with a message naming the field
      and consistent with the existing wording. It must precede the falsey guard; a check after
      it cannot see `False`. Leave a comment saying so, naming `bool`'s `int` subclassing as
      the reason the value reaches the division at all.
- [x] 4.2 Add the non-finite check **after** the division, using `math.isfinite`. Placed there
      it is only ever handed a real number, because a non-numeric value already raised
      `TypeError` and an over-large integer already raised `OverflowError`. Add `import math`
      to the module. Use `isfinite`, not `isnan` — the infinities are broken the same way.
- [x] 4.3 Do not narrow the falsey guard. `fix-issue-175-optional-client-timeout` specified
      that *any* falsey value means "not supplied"; changing that guard would alter the
      contract for `""`, `[]` and `{}` as collateral damage.
- [x] 4.4 Re-run the test file and confirm every case from sections 2 and 3 passes, including
      the three `client_sent_msg_ts` non-finite cases that were already green.
- [x] 4.5 Confirm the discriminating negatives still pass — these are what prove the fix did
      not overreach: `parse_client_timeout(600000) == 600.0`,
      `parse_client_sent_msg_ts(1700000000000) == 1700000000.0`,
      `parse_client_timeout(None) == 0`, and
      `test_a_normal_timeout_still_reaches_the_pipeline_in_seconds`. Rejecting `false` must not
      break omission.
- [x] 4.6 Run the sibling suites that exercise the same fields —
      `tests/unit/test_chat_timeout_guard.py` and `tests/unit/test_chat_timing_persistence.py`
      — and confirm neither regressed. The epoch sentinel for an absent send time must still be
      `1970-01-01T00:00:00Z`.

## 5. Documentation

- [x] 5.1 In the `client_sent_msg_ts` row of the chat request-body table in
      `docs/docs/api_reference.md`, add a boolean and a non-finite literal to the list of values
      rejected with **400**, and delete the clause stating that "A JSON boolean slips through
      this rule as a usable number instead of being refused" along with its `issue #195` link.
      Keep the sentence that a negative or fractional value does *not* need rejecting — that
      remains true and is the reason scope stops where it does.
- [x] 5.2 In the `client_timeout` row, replace the passage describing the badly-behaved inputs:
      `true` no longer "becomes a 1 ms deadline" and a literal decoding to infinity or `NaN` no
      longer "disables the deadline outright" — both are now 400. Remove the `issue #195`
      pointer. **Keep** the negative-number clause and the `issue #191` reference to the
      stalled-provider limit, which are unrelated and still accurate.
- [x] 5.3 Verify no anchor drift: `git diff origin/dev -- docs/docs/api_reference.md | grep -c '#L[0-9]'` must return `0`. This change does not touch `app.py`, so every
      `[name]: …/app.py#Lnnn` definition stays correct — do not "helpfully" renumber any
      (issue #190 owns that).
- [x] 5.4 Confirm `grep -c boolean docs/docs/api_reference.md` is ≥ 1 and that each hit now
      describes a *rejected* value rather than an accepted one. The count alone was already ≥ 1
      before this change, so read the hits rather than trusting the number.
- [x] 5.5 Grep the rest of `docs/` for any other claim that a boolean or a non-finite value is
      accepted in these fields, and correct anything found.
- [x] 5.6 Remove `false` from the omitted-value ("falsey") enumeration in **both** rows, and
      state the boolean rule *before* the falsey rule so the prose runs in the order the code
      does. 5.1/5.2 only added booleans to the rejected list; each row then listed `false` in
      both lists at once, so a client reading the table would send `false` to omit a field and
      get a 400 (Codex, PR #203). Guarded by `tests/unit/test_api_reference_timing_contract.py`,
      which parses the enumeration out of the table and runs every literal in it through the
      real parser — the same drift cannot pass review-by-eye twice.

## 6. Verify and land

- [x] 6.1 Run `bash scripts/gate.sh` **bare** — no pipe, no redirect, since redirecting it
      trips the harness protected-path guard and reads as a failure that is not one. It must
      pass format, lint and tests.
- [x] 6.2 Confirm diff coverage is **≥80%** on the changed lines from the gate's diff-coverage
      output. `request_validation.py` is a small pure-function module with direct tests, so
      100% on the added lines is the realistic target.
- [x] 6.3 Re-check every acceptance criterion in issue #195 one at a time against the working
      tree, and confirm `black --check` reports `request_validation.py` unchanged — no reflow of
      unrelated lines.
- [x] 6.4 Run `openspec validate fix-issue-195-timing-bool-nonfinite --strict` and confirm it
      passes. (Skipped in the unattended run — no openspec CLI there. Run on the operator host
      2026-08-05 with openspec 1.4.1: `Change 'fix-issue-195-timing-bool-nonfinite' is valid`.)
- [x] 6.5 Commit only green, short lowercase subject, no `Co-Authored-By` or AI-attribution
      trailer. Push the branch and open a PR into `fasrc/archi:dev` whose **body** contains
      `closes #195` (the keyword works in the body only — a title reference leaves the issue
      unlinked). **Do not merge** — a human merges.
- [x] 6.6 File a follow-up issue for the two things this change deliberately left alone, so
      neither is lost: a negative `client_timeout` expiring the deadline immediately, and
      `openai_compat.py:274` sending `now.timestamp()` — **seconds**, not milliseconds — into a
      field that is divided by 1000 again. Do not fix either here; both are outside issue #195.

## 7. Live deployment validation (Deployment & Validation Policy, `AGENTS.md`)

Unit and gate evidence cannot satisfy the policy on its own: it requires at least one
end-to-end check against the *running* deployment, naming the service and the code path that
service actually imports. Recorded here rather than only in a PR comment so the evidence
travels with the change.

- [x] 7.1 Deploy the PR head to the dev stack and name what ran. `deploy/scripts/redeploy.sh`
      on 2026-08-05, deployment `dev`, services `chatbot-dev` + `postgres-dev` + `data-manager-dev`,
      `SOURCE_COMMIT=fd73066d-dirty` (the `-dirty` suffix is untracked operator files, no tracked
      edits), config pin `deploy-pin-2026-07e@4d6873e3` `match=yes`.
- [x] 7.2 Confirm the *running* service imports the changed code, not a stale copy. `chatbot-dev`
      runs `python -u src/bin/service_chat.py` from `WorkingDir=/root/archi`; the imported file
      `/root/archi/src/interfaces/chat_app/request_validation.py` hashes
      `sha256:925c5856f8db73dc…`, byte-identical to the branch. (The pre-deploy image predated the
      file entirely, so this check is what separates "deployed" from "assumed deployed".)
- [x] 7.3 Exercise the rejection matrix the review asked for: `true`, `false`, `Infinity`,
      `-Infinity`, `NaN` in each of `client_sent_msg_ts` and `client_timeout`, on **both**
      `POST /api/get_chat_response` and `POST /api/get_chat_response_stream` — 20 requests, all
      **HTTP 400**. Each assertion requires the JSON error body to name the offending field, so a
      400 from a malformed-JSON parse failure cannot be mistaken for a validator rejection.
- [x] 7.4 Confirm the 400 precedes the pipeline and any write. Row counts for `conversations`,
      `timing`, `agent_traces`, `conversation_metadata` and `feedback` were identical before and
      after the 20 rejections (`14,7,3,8,0` → `14,7,3,8,0`), and the batch took **0.03s** total
      against a ~15s served turn — no generation was paid for.
- [x] 7.5 Confirm the guards reject narrowly. `client_timeout: null` still returns **200** (the
      documented "same as omitting" path), and a valid request answers on both routes — 200 with a
      real answer on `/api/get_chat_response`, 200 with an NDJSON stream on the streaming route.
      23/23 live checks passed.
