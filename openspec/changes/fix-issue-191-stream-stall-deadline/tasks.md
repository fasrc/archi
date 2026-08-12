## 1. Confirm the premise on the current tree

- [x] 1.1 Re-read `ChatWrapper.stream` in `src/interfaces/chat_app/app.py` — the consume
      loop is at `:2161-2165` and the in-loop deadline check at `:2174` on
      `origin/dev@0a157cdc`; re-anchor on the symbol names if they moved. Confirm the check
      is still the *first statement of the loop body* rather than of the loop header. That
      ordering is the entire defect; if it changed, stop and report instead of proceeding.
- [x] 1.2 Confirm the pipeline really is lazy — `BaseReActAgent.stream`
      (`src/archi/pipelines/agents/base_react.py:422`) contains `yield`, so its body,
      including `_prepare_agent_inputs` at `:425`, does not run until the first `next()`.
      If the body ran eagerly, a stall would happen before the loop and this design would
      not cover it.
- [x] 1.3 Confirm nothing downstream already bounds the wait:
      `grep -rn "request_timeout\|timeout=" src/archi/providers/ src/archi/pipelines/`
      returns nothing. If a provider timeout has appeared since the issue was written, say
      so in the PR body — it changes the argument for this change, though not its scope.
- [x] 1.4 Confirm `app.py` and `tests/unit/test_chat_timeout_guard.py` are `black`-clean
      **before** editing (`black --check`). A file that is already dirty will be reflowed on
      commit, dragging unrelated lines into the diff and sinking diff coverage — the ~17%
      failure recorded in `fix-issue-175`'s proposal.
- [x] 1.5 Read `TestTheInStreamCheckNeedsOnlyTheTimeout`
      (`tests/unit/test_chat_timeout_guard.py:162-244`) and note its clock stub:
      `SimpleNamespace(time=...)` with no `monotonic`. This is the constraint behind
      Decision 2 — verify it for yourself rather than trusting the design doc, because the
      whole monotonic-import decision rests on it.

## 2. Bound the stall — failing test and fix in one task

- [x] 2.1 Add a stall test to `tests/unit/test_chat_timeout_guard.py`, in a new class
      alongside the existing ones. Its `archi.stream` must be a generator that really
      `time.sleep()`s past a short `client_timeout` (0.5s or less — keep the suite fast)
      before yielding, and the test must **not** monkeypatch the clock; wall-clock
      enforcement is the thing under test. Reuse the existing `_streaming_wrapper` stub
      shape so the test exercises the real `stream` method. Run it and **watch it hang or
      fail** — on the parent commit the generator blocks and no 408 is produced. Record
      the observed failure mode in the commit body. Give the test a hard upper bound (e.g.
      assert the elapsed time is well under the sleep) so a regression fails fast instead
      of hanging CI.
- [x] 2.2 In the same task, implement the fix so the suite ends green — never leave the
      tree red at a task boundary, since the gate refuses to commit and the loop halts. In
      `ChatWrapper.stream`:
      - `from concurrent.futures import ThreadPoolExecutor` and `from time import
        monotonic` at module top (the `from` import for `monotonic` is load-bearing —
        Decision 2 — and needs a comment saying why, or a later tidy-up will "restore
        consistency" with `time.monotonic()` and break §1.5's test).
      - Bind `gen = self.archi.stream(...)` and replace the `for` header with a `while
        True:` that assigns `output` and `break`s on exhaustion. **Do not re-indent the
        ~325-line body** (`:2193-2487`) — same indentation level, minimal diff.
      - Create one `ThreadPoolExecutor(max_workers=1)` per stream, and only when
        `client_timeout` is truthy; a falsey timeout keeps iterating directly with no
        thread. Compute `deadline = monotonic() + client_timeout` once, and per advance
        `remaining = deadline - monotonic()`; if it is already `<= 0`, take the timeout
        path without submitting.
      - Advance with `executor.submit(..., next, gen, sentinel)` — `next()`'s
        two-argument form. A bare `next` raises `StopIteration` into `future.result()`,
        and PEP 479 turns that into `RuntimeError: generator raised StopIteration` inside
        this generator. Use a module-level sentinel object.
      - On `concurrent.futures.TimeoutError`, emit the 408 and close the trace via the
        shared helper from §4, then `return`.
- [x] 2.3 Do **not** call `gen.close()` on the timeout path — the worker is still inside
      the generator, so it raises `ValueError: generator already executing`. Leave a comment
      saying so; the next reader will otherwise "fix" the missing cleanup.
- [x] 2.4 Comment the accepted trade-off at the timeout site: the abandoned thread keeps
      running until the provider returns, so this bounds client-visible latency, not
      server-side resource usage. Note the second-order consequence too —
      `concurrent.futures.thread` joins non-daemon workers at interpreter exit, so process
      shutdown can block on a still-parked worker. Both belong in the PR body as well.
- [x] 2.5 Re-run the new test and confirm it now produces the 408 event and the trace
      closure (`status="error"`, `cancelled_by="system"`,
      `cancellation_reason="Client timeout"`), then run the whole file and confirm
      `TestTheInStreamCheckNeedsOnlyTheTimeout` passes **with no edit to it**. If it needed
      an edit, Decision 2 was not implemented as written — fix the import, not the test.

## 3. Preserve the caller's context across the worker boundary

- [x] 3.1 Add a test that fails without context propagation and passes with it, then make
      it pass in the same task. Assert the *positive*: an advance performed through the
      worker sees a Flask request context (`has_request_context()` is true inside the
      generator body), and a context variable set on the first advance is still readable on
      the second. Absence-of-crash proves nothing here — both real regressions fail open
      silently.
- [x] 3.2 Implement: capture `contextvars.copy_context()` once in the request thread at
      stream start and submit `ctx.run(next, gen, sentinel)`. **One snapshot per stream,
      reused for every advance** — a fresh snapshot per advance discards what earlier
      advances set, and `start_run_memory()` runs on the first advance
      (`base_react.py:1418`), so `_ACTIVE_MEMORY` would read `None` from advance 2 onward
      and `if self.active_memory:` (`base_react.py:466`) would silently stop recording
      tool calls.
- [x] 3.3 Comment why the snapshot exists, naming both fail-open sites —
      `src/archi/pipelines/agents/tools/base.py:36-42` (no request context → tool access
      *allowed*) and `prompt_utils.py:14-18` (no request context → roles dropped from the
      prompt). Without that note the `ctx.run` looks like ceremony and will be simplified
      away.
- [x] 3.4 Confirm the RBAC gate's behaviour is genuinely unchanged for a normal streaming
      request by running the existing RBAC/tool suites
      (`grep -rl "has_request_context\|tools:.*permission" tests/` and run what it names).
      A green run here is what says this change did not quietly disable permission checks.

## 4. One emission path for both timeout branches

- [x] 4.1 Extract the 408 emission from the in-loop branch (`:2174-2192`) into a single
      local helper that closes the trace (`status="error"`, `cancelled_by="system"`,
      `cancellation_reason="Client timeout"`, `total_duration_ms`) and returns the
      `{"type": "error", "status": 408, "message": _chat_error_message(408)}` event, and
      call it from both branches. The issue requires the stall path to close the trace
      *identically*; sharing the code makes that true by construction.
- [x] 4.2 Confirm the existing
      `test_timeout_without_a_timestamp_still_ends_the_stream_with_408` still passes — it
      covers the helper through the original path, so it is the regression test for the
      extraction.
- [x] 4.3 Keep `total_duration_ms` measured the way the existing branch measures it, so
      traces from the two paths stay comparable. If the wall-clock and monotonic baselines
      make that awkward, prefer matching the existing field's meaning over internal
      tidiness, and say why in a comment.

## 5. Documentation and the stale comment

- [x] 5.1 In the `client_timeout` row of the request-body table in
      `docs/docs/api_reference.md` (line 36), delete the clause saying the check "bounds a
      slow stream but **not** a provider that stalls without emitting anything", the "do not
      rely on it as a hard ceiling" advice, and the `issue #191` link. Replace with what is
      now true: a declared deadline ends the client's wait whether the stream is slow or the
      provider is silent.
- [x] 5.2 Do not overclaim in that replacement. The bound is on client-visible latency; the
      server may still be occupied by the abandoned provider call after the 408. State that,
      briefly — an integrator reading "hard ceiling" as "the server stopped working" would
      be wrong.
- [x] 5.3 Update the code comment at `app.py:2166-2173`: the sentence saying the check is
      "reached only when the upstream generator yields, so this bounds a slow stream, not a
      provider that stalls" and the `Issue #191 tracks…` pointer are both obsolete. **Keep**
      the cross-reference to the twin check in `_prepare_chat_context` and the note that the
      differing baselines are deliberate — an existing `chat-api-request-contract`
      requirement pins that, and deleting it would regress #175.
- [x] 5.4 `grep -rn "issues/191\|#191" docs/ src/ tests/` and confirm the only surviving
      hits are unrelated (CSS colour literals in
      `src/interfaces/chat_app/static/style.css`) or historical records in other changes'
      `tasks.md`. Do **not** edit `fix-issue-195-timing-bool-nonfinite/tasks.md`, whose
      task 5.2 says to keep the #191 reference: that instruction was correct when written,
      and this change is what retires it. Note the reconciliation in the PR body.
- [x] 5.5 Verify no doc-anchor drift: `git diff origin/dev -- docs/docs/api_reference.md |
      grep -c '#L[0-9]'` must return `0`. Do not renumber any `…/app.py#Lnnn` link even
      though this change moves lines in `app.py` — issue #190 owns anchor remapping, and
      hand-remapping here has already halted one run.

## 6. Verify and land

- [ ] 6.1 Run `black` over the touched files and `git status` before staging — the
      pre-commit hook *writes* formatting while CI *asserts* it, so a file formatted after
      `git add` commits misformatted and fails CI.
- [ ] 6.2 Run `bash scripts/gate.sh` **bare** — no pipe, no redirect (the harness guard
      rejects a redirected run and it reads as a failure that is not one) — from the
      repository root of this branch. Confirm format, lint and tests pass.
- [ ] 6.3 Confirm diff coverage is **≥80%** on the changed lines. The new lines are all in
      the deadline path, and §2/§3's tests execute them; if coverage lands short, the
      likeliest cause is an accidental reflow of the loop body (§2.2) rather than a missing
      test — check `git diff --stat` before writing more tests.
- [ ] 6.4 Re-check each acceptance criterion in issue #191 one at a time against the working
      tree. Criterion 7 (end-to-end) was removed by the operator's 2026-08-10 decision — see
      §7.
- [ ] 6.5 Run `openspec validate fix-issue-191-stream-stall-deadline --strict` and confirm
      it passes. If the CLI is absent in the execution environment, say so explicitly rather
      than marking the task done — it was validated on the operator host during Loop 1.
- [ ] 6.6 Commit only green, short lowercase subject, no `Co-Authored-By` or AI-attribution
      trailer. Push with `git push -u origin fix/issue-191-stream-stall-deadline` — the
      branch was created from `origin/dev` and so tracks the trunk until `-u` fixes it.
- [ ] 6.7 Open a PR into `fasrc/archi:dev` whose **body** contains `closes #191` (the
      keyword only links from the body; a title mention leaves the issue unlinked). The body
      must also carry: the abandoned-worker caveat and its interpreter-shutdown consequence
      (§2.4), the deferred-e2e note (§7), and the #195 cross-change note (§5.4).
      **Do not merge** — a human merges in daylight.

## 7. Live deployment validation — deferred by operator decision

The `AGENTS.md` deployment policy normally requires an end-to-end check against the running
deployment. The operator explicitly deferred it for this change on 2026-08-10 (issue #191,
"Auto-ok with deferred e2e"): acceptance criterion 7 was struck and the code/tests/gate PR
lands without it.

- [ ] 7.1 State the deferral in the PR body verbatim enough to be unambiguous: "E2e
      validation deferred — operator verifies after deploy." Do not silently omit it, and do
      not attempt a live check from the unattended run.
- [ ] 7.2 Leave a note for the post-merge redeploy naming what to exercise: a streaming
      request against a provider that stalls, confirming the in-band 408 arrives at the
      declared deadline, and — because §3 is the risky half — one authenticated streaming
      request whose tool call must still be refused by the RBAC gate.
