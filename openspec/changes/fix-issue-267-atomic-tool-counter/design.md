## Context

`RunMemory` is a per-run aggregator for documents, notes, and tool-call records. One instance is
reached by many threads: `active_memory` is a ContextVar holding a *reference* to the instance,
and LangGraph's `ToolNode._func` executes every tool call of one AI message through a
`ContextThreadPoolExecutor` that copies the calling context into each worker. Verified against
the pinned dependencies in the `archi` env — langgraph 1.0.2, langchain-core 1.2.13, CPython
3.11.15.

Nothing on the object is synchronized. Five of its methods perform composite (multi-step)
mutations, and one of those — `bump_tool_call_count` (`run_memory.py:219-225`) — is the sole gate
for the per-turn tool budget in `_consume_tool_budget` (`base_react.py:1874-1899`), which admits a
call when the returned count is `<= cap`.

The measurements this design rests on were taken tonight on `origin/dev` @ `9e899848` and are
tabulated in `proposal.md`. Three of them drive decisions:

1. At the default `sys.getswitchinterval()` of 0.005 the unfixed counter loses **zero** updates
   over 800 000 bumps; at a forced 1e-6 it loses ~65%.
2. Duplicate returned counts, the property the budget actually relies on, are **unreachable** at
   3 threads (0 / 2000 trials), rare at 8 (5 / 2000), and common at 32 (128 / 2000) — all only at
   the 1e-6 interval.
3. `resolve_tool_input` calls `record_tool_call` (`run_memory.py:101`), so guarding both with a
   non-reentrant `threading.Lock` deadlocks. Measured: the thread hung for a full 3 s timeout,
   and the identical construction with `threading.RLock` completed.

## Goals / Non-Goals

**Goals**

- The per-tool counter never loses an update and never hands the same count to two callers, so
  `_consume_tool_budget` admits at most `cap` callers regardless of thread interleaving.
- The other composite mutations on the same object become atomic, so a concurrent insert cannot
  make one of them raise or silently drop data.
- The guarantee is encoded by tests that are red on the unfixed code — not merely green on the
  fixed code.

**Non-Goals**

- Changing any budget semantics: which call is refused, the cap lookup, or the wording of the
  synthetic refusal.
- Restructuring the async `ToolNode` path. The read-modify-write contains no `await`, so the
  event loop cannot interleave it; only threads are in scope.
- Making `RunMemory` safe for concurrent *use across runs*. It stays a per-run object; this
  change makes its own invariants hold under the concurrency it already faces.
- Fixing the debug-level `except Exception` in `_store_tool_input` (`base_react.py:1426-1434`)
  that swallows the `record_tool_input` failure. That silent-failure surface is a real problem
  but it is a `base_react.py` change with its own blast radius; see Risks.

## Decisions

### Decision 1: One private `threading.RLock`, not `threading.Lock`

The issue body prescribes `threading.Lock`. That is correct only while locking stops at the
counter. This change also locks `record_tool_call` and `resolve_tool_input`, and
`resolve_tool_input` calls `record_tool_call` at `run_memory.py:101` — a non-reentrant lock
self-deadlocks there, measured, not theorized (3 s hang; `RLock` completed and produced the
expected single `_tool_runs` entry).

The alternative that keeps a plain `Lock` is to split each locked method into a public locked
wrapper plus a private unlocked `_..._unlocked` body and have the nested caller invoke the
unlocked form. That is the more defensive design in general — a plain `Lock` cannot hide an
accidental nested critical section — but here it doubles the method count of the touched surface
for no behavioral gain, and the nesting in question is not accidental: `resolve_tool_input`'s
`pop(0)`-then-record *should* be one atomic unit, which is exactly what re-entrancy preserves. A
plain `Lock` with an unlocked inner body would give the same atomicity; `RLock` gets there
without the churn.

One lock for the whole object rather than one per aggregate: the aggregates are not independent
(`resolve_tool_input` touches `_pending_tool_inputs_by_name` and `_tool_runs` in one logical
operation), per-aggregate locks would need a lock-ordering rule to stay deadlock-free, and
contention is bounded by the number of tool calls in a single AI message — single digits, on a
path that already performs network I/O per call.

### Decision 2: Force `sys.setswitchinterval(1e-6)` in a fixture, and restore it on teardown

At the default 0.005 interval the *unfixed* code passes: 0 lost updates over 800 000 bumps
(proposal table). A test written at the default interval would therefore be green on broken
code — the single worst outcome available here, because it would certify the bug as fixed.

Forcing the interval down to 1e-6 makes CPython release the GIL between the two dict operations
often enough to expose the window. The value is process-global, so the fixture must restore the
previous value with `sys.getswitchinterval()` captured before the change and restored in
teardown, not hardcoded back to 0.005.

Verified safe to mutate process-globally here: `pytest-xdist` is **not** installed and
`pyproject.toml:75` sets `addopts = "-v --tb=short"` with no parallel runner, so tests execute
serially and no other test observes the lowered interval. If xdist is ever adopted, this fixture
becomes a cross-test hazard — recorded in Risks.

### Decision 3: Assert distinctness with 32 threads over 200 trials, behind a `Barrier`

The issue suggests N concurrent calls against a cap of K. Measured, that shape does not work at
the operational size: 3 concurrent calls produced **0 duplicate counts in 2000 trials**, and 8
threads produced 5 / 2000 — a 0.25% per-trial failure rate, i.e. a test that passes on broken
code 99.75% of the time.

What does work: **32 threads, synchronized on a `threading.Barrier` so they contend at the bump
rather than at thread start, repeated over 200 trials.** Measured on unfixed code across 3 reps:
91, 112 and 149 duplicate counts — detected every rep, never 0. The prototype fix produced 0
duplicates on 3 / 3 reps of the same 200-trial loop. Runtime 0.54 s.

The trial loop is what converts a 6.4%-per-trial probability into a deterministic assertion
(1 − 0.936²⁰⁰ ≈ 1). Trial count, not thread count, is the knob to turn if this ever proves flaky
on slower hardware — and it must be turned *up*, never down.

Assert on **pairwise distinctness of the returned values**, not on the final total. Distinctness
is the property `_consume_tool_budget` relies on and it is strictly stronger: a run can end with
the correct total while two callers were both handed count 2 and both admitted under `cap=2`.

### Decision 4: Lock composite operations only; leave the two single-`append` methods alone

Locked, because each reads then writes and each is reachable from tool execution:

| Method | Lines | Composite shape |
|---|---|---|
| `bump_tool_call_count` | 219-225 | `get` → `+1` → `setitem` |
| `tool_call_count` | 227-231 | read, locked so no torn intermediate is observed |
| `record_tool_call` | 49-66 | `get` → build → `setitem` |
| `record_tool_input` | 67-86 | iterate `.items()` → `setitem`; `setdefault` → `append` |
| `resolve_tool_input` | 88-102 | `get` → `pop(0)` → `record_tool_call` |
| `record_tool_documents` | 154-174 | `get` → mutate list → `setitem` |

Left unlocked, deliberately: `record` (`run_memory.py:29`) and `note` (`:47`). Each is a single
`list.append` — one operation, no read-modify-write, and no gate reads its result. That is the
principled line this change draws: **lock composite operations, not individually atomic ones.**
`list.append` is atomic under the GIL and remains atomic under free-threaded CPython's
per-object locking, so locking these two would be symmetry rather than correctness, which the
issue's constraints explicitly forbid.

`record_tool_input` earns its lock for a stronger reason than the counter's. It iterates
`self._tool_runs.items()` in Python while assigning into the same dict, so a concurrent insert
raises `RuntimeError: dictionary changed size during iteration` — measured 4 times in 300 trials
at the 1e-6 interval. Unlike a lost counter increment, that is a live exception on a tool path,
and its only caller swallows it at debug level.

The snapshot readers (`notes`, `events`, `tool_runs`, `unique_documents`, `intermediate_steps`,
`tool_inputs_by_id`) are **not** locked by this change. `tool_inputs_by_id` (`:208-217`) does
iterate `.items()` in Python and shares the hazard class, but its single call site
(`base_react.py:214`) runs while composing `PipelineOutput`, after the tool node has returned,
and it is already wrapped in a `try/except` that degrades to omitting the metadata key. Locking
it is defensible; doing so in this change would widen a hardening PR into the reader surface
without a demonstrated interleaving. The PR body must state this explicitly as an audited-and-
deferred item rather than leaving it unmentioned.

### Decision 5: The lock stays inside `RunMemory`; `base_react.py` is not touched

A lock in `_consume_tool_budget` around its `bump_tool_call_count` call would make that one
caller safe and leave every other caller of the counter unprotected — the invariant belongs to
the counter, not to one of its readers. Keeping the lock private also means the tests can assert
the property directly against `RunMemory` with no agent scaffolding, and `base_react.py` stays
out of the diff, which keeps the four budget/lifecycle test modules verifiably untouched.

### Decision 6: Declare a new capability rather than modifying `agent-tool-budgets`

The per-turn budget's capability is declared by `openspec/changes/agent-search-budget-cap/`,
which is **unarchived** — `openspec/specs/` has no `agent-tool-budgets` directory, so there is
nothing on the trunk to modify. Declaring `run-memory-thread-safety` as a new capability keeps
this delta independent of whether that change is ever archived, and the two do not overlap in
substance: one decides *what* the budget is and when a call is refused, this one guarantees the
counter it reads is atomic. Same reasoning as PR #237's `design.md` Decision 3 and the
`generated-requirements-sync` Decision 5.

## Risks / Trade-offs

- **The distinctness test is probabilistic by nature.** Mitigated by 32 threads × 200 trials
  (detected on 3 / 3 reps of unfixed code, 91–149 duplicates each) plus the forced switch
  interval. Residual risk: on much slower or single-core hardware the window could narrow. The
  response is to raise the trial count, never to lower the assertion. The task list requires
  watching the test fail before the fix lands, which catches this at authoring time.
- **`sys.setswitchinterval` is process-global.** Safe today (no xdist, serial execution,
  verified) and the fixture restores the captured prior value. If parallel test execution is
  adopted later, this fixture would lower the interval for whatever runs alongside it — slowing
  those tests, not corrupting them.
- **Six `with self._lock:` lines add a failure mode the object did not have: deadlock.** The one
  real nesting is handled by `RLock` (Decision 1). The residual hazard is a *future* method that
  calls a locked method while holding a different lock. Bounded by keeping exactly one lock on
  the object.
- **`_store_tool_input`'s debug-level `except Exception` stays.** After this change the
  `RuntimeError` it swallows can no longer be raised by concurrent inserts, so the silent-failure
  surface has one fewer trigger — but the swallow itself remains, and any future exception from
  `record_tool_input` will still vanish into a debug log. Out of scope here (a `base_react.py`
  change); worth a follow-up issue, which the PR body should name.
- **Latent-defect PRs are hard to review**, because nothing observably misbehaves before or
  after. The measured tables in `proposal.md` and the requirement that the PR body state which
  of the two tests actually failed pre-fix exist to give the reviewer something falsifiable
  rather than an appeal to principle.
