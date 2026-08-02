## 1. Confirm the premise on the current tree

- [x] 1.1 Re-read the three sites before editing anything and confirm they still match this
      change's description: the unguarded check at `src/interfaces/chat_app/app.py:1710`, the
      `if x else 0` coercion at `:4652-4655`, and the guarded streaming twin at `:2156`. Line
      numbers drifted once already between the issue being filed and this proposal — if they
      moved again, re-anchor on the symbol names (`_prepare_chat_context`, `_parse_chat_request`)
      and note the new numbers in the commit body.
- [x] 1.2 Confirm the arithmetic without any app machinery: for
      `(sent, timeout)` in `(None, None)`, `(1769900000000, None)`, `(None, 600000)`, compute
      `s = sent/1000 if sent else 0`, `t = timeout/1000 if timeout else 0` and show
      `now.timestamp() - s > t` is `True` in all three cases. All three must reproduce; if any
      does not, stop and report rather than proceeding.

## 2. Red tests first

- [x] 2.1 Create `tests/unit/test_chat_timeout_guard.py` with a module docstring stating what
      the guard means (a falsey `client_timeout` or `client_sent_msg_ts` = no client deadline)
      and why the explicit-timeout test exists (it fails if the check is deleted rather than
      corrected). Build the stub-`self` wrapper the same way
      `tests/unit/test_chat_refresh_context.py::_wrapper` does — `object.__new__(ChatWrapper)`
      with `create_conversation`, `query_conversation_history` and
      `update_conversation_timestamp` stubbed. Do not import the existing module's private
      helpers; mirror the pattern, so neither file constrains the other's evolution.
- [x] 2.2 Add the failing test for both fields absent: call the real
      `_prepare_chat_context` with `client_sent_msg_ts=0, client_timeout=0` and assert it
      returns a context with no error status. Run it and **watch it fail** with the current
      `(None, 408)` — record the failure output in the commit body. Do not touch `app.py` yet.
- [x] 2.3 Add the two single-field cases as failing tests: `client_sent_msg_ts=0` with a
      non-zero `client_timeout`, and a non-zero `client_sent_msg_ts` with `client_timeout=0`.
      Both must fail with 408 before the fix — confirm both, since a guard on `client_timeout`
      alone would leave the first one passing for the wrong reason.
- [x] 2.4 Add the explicit-deadline test that must **pass** both before and after: a non-zero
      `client_sent_msg_ts` with a non-zero `client_timeout` where `server_received_msg_ts`
      exceeds the window still returns `(None, 408)`. Add its companion in-window case that
      asserts a deadline not yet reached is accepted.

## 3. Fix the guard

- [x] 3.1 In `_prepare_chat_context`, make the timeout comparison conditional on both
      `client_sent_msg_ts` and `client_timeout` being truthy. Keep the edit minimal and
      black-formatted — `app.py` is currently black-clean, so `black --check` on it must still
      report unchanged afterwards and `git diff` must show no reflow of unrelated lines.
- [x] 3.2 Run `tests/unit/test_chat_timeout_guard.py` and confirm every test from section 2
      now passes, including the two that were already green.
- [x] 3.3 Add the cross-reference comment at the `_prepare_chat_context` check naming the
      streaming check, and the reciprocal comment at the streaming check naming this one. Each
      states that both read a falsey `client_timeout` as "no client deadline" and that the
      differing baselines (client send time vs `stream_start_time`) are deliberate.

## 4. Retire the workaround the old bug forced on other tests

- [x] 4.1 Update the `_prepare()` helper docstring in `tests/unit/test_chat_refresh_context.py`,
      which currently explains that it sends a matched timing pair to keep "this unrelated bug"
      out of its results and cites `app.py:1710` as unguarded. Restate it as a deliberate
      choice of a valid deadline rather than a workaround for a live defect. Do not change what
      the helper passes — those tests are about refresh semantics and must keep behaving
      identically.

## 5. Simplify the API reference

- [x] 5.1 In `docs/docs/api_reference.md`, change the `Required` column for
      `client_sent_msg_ts` and `client_timeout` from **yes, in practice** to `no`, and rewrite
      both descriptions around what the fields are for — latency accounting, and declaring a
      deadline the server honours when supplied — with no reference to rejection for omission.
- [x] 5.2 Before deleting the warning admonition, lift out the "How the rejection reaches you
      differs by endpoint" sub-section and relocate it to the streaming endpoint's own section.
      It documents that a 408 arrives as HTTP 200 plus an `{"type": "error", "status": 408}`
      NDJSON event on the streaming route, which stays true for the explicit-deadline 408 this
      change preserves. Reword it to describe an exceeded deadline the caller *did* declare.
- [x] 5.3 Delete the rest of the admonition: the "look optional and are not" framing, the
      three-row reproduction table, the quoted unguarded comparison, and the closing paragraph
      calling it a handler bug tracked as #175.
- [x] 5.4 Clean up the reference-link definitions the admonition owned (`[parse]`, `[check]`,
      `[streamerr]`, `[stream]`). Keep `[streamerr]` if the relocated sub-section still uses it;
      remove any definition left unused. Leave `[refreshguard]` alone — it is used elsewhere on
      the page. Then grep the file for both dangling link uses and orphaned definitions and
      confirm neither exists.
- [x] 5.5 Check the runnable `curl` example and the shape template still read correctly with
      the fields documented as optional. Keep both timing fields in the example, and keep
      `client_sent_msg_ts` generated at send time via `date +%s000` rather than a literal —
      a stale literal plus the retained `client_timeout` is a genuinely expired deadline and
      would still 408.
- [ ] 5.6 Grep the whole `docs/` tree for any other claim that the timing fields are required
      or that omitting them yields 408, and correct anything found.

## 6. Verify and land

- [ ] 6.1 Run `bash scripts/gate.sh` **bare** — no pipe, no redirect, since redirecting it
      trips the harness protected-path guard and reads as a failure that is not one. It must
      pass format, lint and tests.
- [ ] 6.2 Confirm **diff coverage ≥80% on the changed lines of `app.py`** specifically, from
      the gate's diff-coverage output — not merely a passing project total. The handler path
      starts largely uncovered, so a passing total proves nothing about these lines.
- [ ] 6.3 Re-check the acceptance criteria in issue #175 one by one against the working tree,
      including that `git diff` on `app.py` shows no unrelated black reflow.
- [ ] 6.4 Run `openspec validate fix-issue-175-optional-client-timeout --strict` and confirm it
      passes.
- [ ] 6.5 Commit only green, with a short lowercase subject and no `Co-Authored-By` or
      AI-attribution trailer. Push the branch and open a PR into `fasrc/archi:dev` whose body
      says `closes #175`. **Do not merge** — a human merges.
