## Context

`ChatWrapper.stream` (`src/interfaces/chat_app/app.py:1991`) opens a trace, records
`stream_start_time = time.time()` (`:2010`), then consumes the pipeline with a plain
`for output in self.archi.stream(...)` (`:2161-2165`). The client deadline is checked as
the first statement of the loop body (`:2174`), which means it is evaluated **once per
yielded event**. A provider that accepts the connection and then produces nothing leaves
the interpreter parked inside `next()` on the loop header, so the check is unreachable and
`client_timeout` — a value the caller explicitly declared — is not enforced. The trace
stays open and the client waits on a socket that will never carry a 408.

The pipeline side is a generator all the way down: `archi.stream`
(`src/archi/archi.py:108`) delegates to `BaseReActAgent.stream`
(`src/archi/pipelines/agents/base_react.py:422`), whose body — including
`_prepare_agent_inputs` at `:425` — does not execute until the first `next()`. Nothing
below it imposes a timeout: `grep -rn "request_timeout\|timeout=" src/archi/providers/
src/archi/pipelines/` is empty.

The operator resolved the approach on 2026-08-10 (issue #191): **Option A, a cancellable
worker**, with provider-level timeouts (Option B) deferred to a separate change, and
end-to-end validation deferred to a post-merge redeploy.

## Goals / Non-Goals

**Goals**
- A declared `client_timeout` bounds the client-visible latency of a streaming request even
  when the provider never yields.
- The stall path is indistinguishable, from the client's and the trace's point of view,
  from the existing in-loop timeout path.
- A request that declares no deadline behaves exactly as it does today, down to not
  creating a thread.
- The existing suite passes unmodified — in particular
  `TestTheInStreamCheckNeedsOnlyTheTimeout`, which is the test that pins the two guards'
  deliberate asymmetry.

**Non-Goals**
- Cancelling the provider call itself. Nothing in this design frees the abandoned worker;
  it bounds the wait, not the work.
- Any provider-level `request_timeout` (Option B).
- Changing the in-loop check, the pre-pipeline check, or `client_sent_msg_ts` handling.

## Decisions

### Decision 1 — One executor per stream, advanced through a sentinel

Create a single `ThreadPoolExecutor(max_workers=1)` for the whole stream rather than one
per advance, and only when `client_timeout` is truthy. One worker is what makes the design
safe: advances are strictly sequential, so exactly one thread is ever inside the generator,
and "generator already executing" cannot arise from our own code.

Advance with `executor.submit(next, gen, _STREAM_EXHAUSTED)` — `next()`'s two-argument form
— **not** bare `next(gen)`. A bare `next` raises `StopIteration` inside the future;
re-raised by `future.result()` inside `stream`, which is itself a generator, PEP 479
converts it to `RuntimeError: generator raised StopIteration` and the stream dies with a
500 at end-of-stream instead of finishing. The sentinel keeps exhaustion an ordinary value.

`max_workers=1` also means the worker thread is reused across advances, which Decision 3
depends on.

### Decision 2 — `from time import monotonic`, not `time.monotonic()`

The issue requires monotonic deadline arithmetic (a wall-clock step during a long stream
must not extend or collapse the deadline) **and** requires
`TestTheInStreamCheckNeedsOnlyTheTimeout` to pass unmodified. Those two criteria collide on
the attribute spelling: that test installs
`monkeypatch.setattr(app_module, "time", SimpleNamespace(time=lambda: next(readings)))`
(`tests/unit/test_chat_timeout_guard.py:209-211`). A `SimpleNamespace` carrying only `time`
has no `monotonic`, so any `time.monotonic()` call reached during that test raises
`AttributeError` and the test fails — while nominally "unmodified", it would have to be
edited to add the attribute, which is precisely what criterion 4 forbids.

Importing the function directly (`from time import monotonic`, called as `monotonic()`)
binds it at module import, out of reach of a patch that rebinds the module-global name
`time`. The existing test then keeps driving the in-loop check with its fake `time.time()`
while the new stall deadline runs on the real clock — which is harmless there, because its
generator yields immediately and its 600-second budget never expires.

The repo's other monotonic users (`src/bin/service_benchmark.py:1666`,
`src/interfaces/uploader_app/app.py:560`) use the attribute spelling; this module is the
exception for the reason above, and the import needs a comment saying so, or a future
tidy-up will "restore consistency" and break the test.

### Decision 3 — Advance inside a snapshot of the caller's context

Capture `contextvars.copy_context()` **once**, in the request thread, at stream start, and
submit `ctx.run(next, gen, _STREAM_EXHAUSTED)` rather than `next` directly.

This is not defensive garnish; without it the change is a silent security and behaviour
regression, because the pipeline body reads two thread-scoped things and both **fail open**:

- `src/archi/pipelines/agents/tools/base.py:36-42` — the RBAC tool gate does
  `if not has_request_context(): return True, None`, i.e. *"no request context → allow the
  tool"*. Run bare on a worker thread, `has_request_context()` is `False`, and every
  permission check on every tool call in every streaming request silently passes. No test
  catches it: unit tests already run without a request context, so they see the fail-open
  branch either way.
- `src/archi/pipelines/agents/utils/prompt_utils.py:14-18` — `get_role_context()` returns
  `""` with no request context, quietly dropping the user's role context from the prompt.

Flask 3.0.3 stores the app and request contexts in `ContextVar`s, so `copy_context()`
captures them along with `_ACTIVE_MEMORY`
(`src/archi/pipelines/agents/base_react.py:69-71`) and anything else request-local.

**The same `Context` object must be reused for every advance of a stream.** A fresh
`copy_context()` per advance would discard mutations made by earlier advances — and
`start_run_memory()` runs on the *first* advance (`base_react.py:1418`, via
`_prepare_agent_inputs` at `:425`), so advance 2 onward would read `_ACTIVE_MEMORY` as
`None`. `if self.active_memory:` at `base_react.py:466` then fails open too: tool-call
recording stops, quietly, mid-stream. Sequential re-entry of one `Context` is supported and
carries its mutations forward; only *re-entrant* use raises.

### Decision 4 — One emission path for both timeout branches

Factor the 408 emission — trace closure with `status="error"`, `cancelled_by="system"`,
`cancellation_reason="Client timeout"`, `total_duration_ms`, then the
`{"type": "error", "status": 408, "message": _chat_error_message(408)}` event — out of the
in-loop branch at `:2174-2192` into one local helper used by both branches. The issue asks
the stall path to close the trace "identically" to the in-loop path; sharing the code makes
that true by construction instead of by inspection, and the existing test
`test_timeout_without_a_timestamp_still_ends_the_stream_with_408` already covers the
helper through the old path.

### Decision 5 — Rewrite the loop header, leave the loop body alone

`for output in self.archi.stream(...)` becomes an explicit `gen = self.archi.stream(...)`
plus a `while True:` that binds `output` and `break`s on the sentinel. The ~325-line body
(`:2193-2487`) stays at its current indentation and is not otherwise edited.

That constraint is about the gate, not aesthetics: `app.py` is 7,099 lines and sits at low
line coverage, and a reflow that re-touches unrelated lines drags them into the diff as
uncovered — the failure mode recorded in `fix-issue-175`'s proposal, where a one-line edit
produced ~17% diff coverage. `app.py` is `black`-clean at `origin/dev@0a157cdc` (verified),
so a minimal, correctly-indented edit will not churn.

## Risks / Trade-offs

- **The abandoned worker outlives the response.** After a stall timeout the thread is still
  blocked inside `next()`, holding the provider socket and whatever the pipeline allocated,
  until the provider finally returns. Accepted by the operator; must appear in a code
  comment and in the PR body.
- **Interpreter shutdown can block on it.** `concurrent.futures.thread` registers an
  `atexit` hook that joins non-daemon worker threads, so a process asked to exit while a
  worker is still parked in a stalled provider call waits for it. `shutdown(wait=False)`
  does not change this, and executor threads cannot be made daemonic on supported Pythons.
  This is inherent to Option A rather than to this implementation; document it, do not
  redesign around it here.
- **The generator is left un-closed on the stall path.** `gen.close()` would raise
  `ValueError: generator already executing` while the worker sits inside it, so the stall
  path must not attempt it. Closing is what the abandoned thread's eventual return does.
- **Context propagation is invisible when it breaks.** Both regressions in Decision 3 are
  silent fail-opens, so each needs its own test asserting the *positive* — that the
  advance sees a request context, and that run memory survives to the second advance —
  rather than a test that merely fails to observe a crash.
- **Overhead on the deadline path.** One thread and one future per streaming request that
  declares a timeout. Requests with no deadline keep the direct iteration and pay nothing.

## Migration Plan

None. No schema, config, or API-shape change; the only observable difference is that a
request which used to hang now ends with the 408 it already promised. Rollback is reverting
the commit.

## Open Questions

None blocking. Option B (provider-level `request_timeout`) stays open as a separate issue,
and the abandoned-worker resource cost is the argument for eventually doing it.
