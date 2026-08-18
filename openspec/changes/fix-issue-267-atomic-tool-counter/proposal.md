## Why

`RunMemory.bump_tool_call_count` (`src/archi/pipelines/agents/utils/run_memory.py:219-225`)
performs an unsynchronized read-modify-write on a plain dict — `new_count =
self._tool_call_counts.get(tool_name, 0) + 1` then `self._tool_call_counts[tool_name] =
new_count`, with `_tool_call_counts` created as a bare `Dict[str, int]` at line 22 and no lock
anywhere on the object. That counter is the **sole gate** for the per-turn tool budget:
`_consume_tool_budget` (`src/archi/pipelines/agents/base_react.py:1874-1899`) admits a call
when the returned count is `<= cap` (line 1890) and otherwise returns the synthetic
"Search budget exhausted" refusal.

**The concurrency is real**, verified against the pinned dependencies (langgraph 1.0.2,
langchain-core 1.2.13, CPython 3.11.15 in the `archi` env): `ToolNode._func` runs every tool
call of one AI message through `with get_executor_for_config(config) as executor: outputs =
list(executor.map(self._run_one, ...))`, and `get_executor_for_config` yields a
`ContextThreadPoolExecutor` that copies the calling context into each worker thread. Because
`active_memory` is a ContextVar holding a *reference* to one `RunMemory`, every worker bumps
the same dict. The async path (`ToolNode._afunc`) is not exposed — the read-modify-write
contains no `await`, so the event loop cannot interleave it.

**The defect is latent, not live — measured, not assumed.** Baseline taken on this machine
tonight against `origin/dev` @ `9e899848`, 20 000 bumps per thread:

| `sys.getswitchinterval()` | Threads | Bumps   | Lost updates |
|---|---|---|---|
| default 0.005 | 8  | 160 000 | **0** |
| default 0.005 | 16 | 320 000 | **0** |
| forced 1e-6   | 8  | 160 000 | 108 254 |
| forced 1e-6   | 16 | 320 000 | 210 740 |

Today's CPython bytecode granularity hides it. It is worth fixing anyway because that
granularity is an implementation detail, not a guarantee: a free-threaded (no-GIL) build, or
any change to how those two dict operations compile, removes the accidental protection with no
other warning. This is hardening a budget-enforcement point, not chasing an observed bug.

The property `_consume_tool_budget` actually relies on is stronger than the final total — it
needs the returned counts to be **pairwise distinct**, so no two callers are ever both admitted
on the same count. Measured pre-fix, per single trial of N synchronized concurrent bumps:

| Interval | Threads | Trials | Trials with a duplicate count |
|---|---|---|---|
| default 0.005 | 3 / 8 / 32 | 2000 each | 0 / 0 / 0 |
| forced 1e-6 | 3  | 2000 | 0 |
| forced 1e-6 | 8  | 2000 | 5 |
| forced 1e-6 | 32 | 2000 | 128 |

So the three-concurrent-calls-against-`cap=2` shape the issue tried **cannot** be made to fail
(0 / 2000), and an 8-thread single trial fails 0.25% of the time — a test built on either would
be green-on-broken-code. 32 threads repeated over 200 trials detected duplicates on 3 / 3 reps
(91–149 duplicates, 0.54 s), which is the shape this change prescribes.

The step-5 audit of the object's other aggregates turned up **a second, harder defect** on the
same object. `record_tool_input` (`run_memory.py:67-86`) iterates `self._tool_runs.items()` in
Python while assigning into that dict; a concurrent insert from any `_tool_runs`-inserting path
raises `RuntimeError: dictionary changed size during iteration` — measured 4 occurrences in 300
trials at the 1e-6 interval. Worse, its only caller `_store_tool_input`
(`base_react.py:1426-1434`) wraps the call in `except Exception` and logs at **debug** level, so
the failure is silent: the tool input is simply dropped from the `tool_inputs_by_id` metadata
that `base_react.py:214` attaches to `PipelineOutput`, with no error surface at all.

Finally, one measured constraint dictates the implementation: `resolve_tool_input` calls
`self.record_tool_call(...)` at `run_memory.py:101`. Guarding both with a non-reentrant
`threading.Lock` **deadlocks** — verified, the thread hung for the full 3 s timeout, while the
same test with `threading.RLock` completed. The issue body's suggestion of `threading.Lock` is
therefore correct only if locking stops at the counter; extending it as step 5 requires makes a
re-entrant lock mandatory.

## What Changes

- **Add one private re-entrant lock to `RunMemory`** — `self._lock = threading.RLock()` in
  `__init__`, held across each composite (multi-step) mutation and across the counter read.
  Re-entrant rather than plain because of the measured `resolve_tool_input` → `record_tool_call`
  deadlock above.
- **Lock the counter** — `bump_tool_call_count` holds the lock across its read-modify-write, and
  `tool_call_count` holds it across its read so a reader cannot observe a torn intermediate.
- **Lock the other composite mutations on the same object**, which share the identical
  read-then-write shape and are reachable from tool execution: `record_tool_call`
  (get-then-replace, `run_memory.py:55-56`), `record_tool_input` (iterate-then-assign, plus
  `setdefault`-then-`append`, lines 74-86), `resolve_tool_input` (`get`-then-`pop(0)`-then-record,
  lines 96-102), and `record_tool_documents` (get-then-mutate-then-set, lines 163-174).
- **Leave `record` and `note` unlocked, deliberately.** Each is a single `list.append`
  (`run_memory.py:29`, `:47`) — one atomic operation, no read-modify-write, and no gate reading
  its result. Locking them would be symmetry, not correctness, and the issue's constraints
  forbid blanket-locking.
- **Two new tests** in `tests/unit/test_run_memory_thread_safety.py`: no lost updates under
  concurrent bumps, and pairwise-distinct returned counts. Both force
  `sys.setswitchinterval(1e-6)` through a fixture that restores the previous value on teardown,
  because at the default interval the unfixed code passes.
- **No semantic change to the budget.** Which call is refused and the wording of the refusal are
  untouched; `_consume_tool_budget` is not modified and no caller-side lock is added.

## Capabilities

### New Capabilities
- `run-memory-thread-safety`: `RunMemory`'s counters and tool-run aggregates are mutated
  concurrently by LangGraph's tool-executor threads, so each composite mutation MUST be atomic
  and the per-tool counter MUST hand out pairwise-distinct counts.

### Modified Capabilities
<!-- None. The per-turn budget's own capability (`agent-tool-budgets`) is declared by the
     unarchived change `openspec/changes/agent-search-budget-cap/` and is not yet in
     `openspec/specs/`, so this change declares its own capability rather than modifying one
     that does not exist on the trunk. This change alters no budget semantics, only the
     atomicity of the counter the budget reads, so the two deltas are independent and may land
     in either order. -->

## Impact

- `src/archi/pipelines/agents/utils/run_memory.py` — one import, one `__init__` line, and
  `with self._lock:` around six methods. No signature, return type, or behavior change.
- `tests/unit/test_run_memory_thread_safety.py` — new.
- **Must stay green with a zero-line diff** (an acceptance criterion, not a hope):
  `tests/unit/test_react_agent_tool_budget.py`, `tests/unit/test_retriever_tool_budget.py`,
  `tests/unit/test_active_memory_contextvar.py`, `tests/unit/test_active_memory_lifecycle.py`.
  If one needs editing, the change went too far.
- **Not touched:** `base_react.py` (no caller-side lock — the invariant is the counter's, and a
  caller-side lock would leave every other caller of `bump_tool_call_count` unprotected), the
  async `ToolNode` path, and every control-plane path this automation must not modify (the
  deployment tree, the CI workflow tree, the gate script, the commit hooks, the rendered runtime
  config tree, and the loop harness's own configuration and container definition).
- **Performance:** an uncontended `RLock` acquire/release per tool call, on a path that already
  does network I/O per call. Contention is bounded by the number of tool calls in one AI
  message — single digits.
- **Diff coverage:** the `src/` side is six one-line `with` statements inside already-covered
  methods plus one `__init__` line, all executed by the existing suite, so patch coverage is not
  at risk from this shape.
