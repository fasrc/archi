Scope: ONE source file (`src/archi/pipelines/agents/utils/run_memory.py`) plus ONE new test
module (`tests/unit/test_run_memory_thread_safety.py`). Do not widen it. Closes fasrc/archi#267.

Gate before every commit: `bash scripts/gate.sh`, run **bare** — no pipe, no redirect (the gate
refuses a piped invocation, and a redirect makes it refuse too; it persists its own output). Never
`--no-verify`. No `Co-Authored-By` or other AI-attribution trailers; short lowercase commit
subjects. Run `black` and `isort` **before** `git add`, not after: the pre-commit hook formats in
place while CI only asserts, so a file formatted after staging is committed unformatted. `git
status` must be empty after each commit.

Each numbered task below is one loop turn and ends **green and committable** — the failing test
and the change that makes it pass are deliberately in the same task, because a turn that ends
with the suite red can never clear the gate and would deadlock the loop.

**Do not modify** `src/archi/pipelines/agents/base_react.py`. The invariant belongs to
`RunMemory`; a caller-side lock in `_consume_tool_budget` would protect one call site and leave
every other caller of the counter unprotected (design D5). Keeping that file out of the diff is
also what makes the four must-stay-green test modules verifiably untouched.

**Do not modify** these four test modules — a zero-line diff in them is an acceptance criterion,
and if one seems to need editing the change went too far:
`tests/unit/test_react_agent_tool_budget.py`, `tests/unit/test_retriever_tool_budget.py`,
`tests/unit/test_active_memory_contextvar.py`, `tests/unit/test_active_memory_lifecycle.py`.

**Also forbidden the whole way through:** the deployment tree, the CI workflow tree, the gate
script, the commit hooks, the rendered runtime config tree, and the loop harness's own
configuration, prompt, Makefile and container definition. This automation must not modify any of
them; if the work seems to require it, stop and say so instead.

**All measurements quoted below were taken on `origin/dev` @ `9e899848`** in the `archi` env
(CPython 3.11.15, langgraph 1.0.2, langchain-core 1.2.13). They are the pre-fix baseline the tests
have to encode. If a reproduction step prints numbers that contradict them, stop and re-read the
issue rather than adjusting the assertion to match.

## 1. Make the per-tool counter atomic (red, then green, one commit)

- [ ] 1.1 Reproduce the race, write both counter tests red, then make them green — **in this one
      task**, because the gate runs before every commit and a turn that ends red can never be
      committed.

      **Reproduce first.** Run this and keep the output; the PR body must quote it (task 3.1):
      ```bash
      python - <<'PY'
      import sys, threading
      sys.setswitchinterval(1e-6)
      from src.archi.pipelines.agents.utils.run_memory import RunMemory
      m = RunMemory()
      def work():
          for _ in range(20000):
              m.bump_tool_call_count("retrieve")
      ts = [threading.Thread(target=work) for _ in range(8)]
      [t.start() for t in ts]; [t.join() for t in ts]
      print("expected", 8*20000, "actual", m.tool_call_count("retrieve"))
      PY
      ```
      Expect a large shortfall — measured `expected 160000 actual 60392` on one run and
      `actual 51746` on another (the exact number varies run to run; only "much less than
      160000" is stable). Then re-run it with the `sys.setswitchinterval(1e-6)` line removed and
      confirm it prints `expected 160000 actual 160000`: at the default 0.005 interval the
      unfixed code passes, which is precisely why the forcing is load-bearing (design D2).

      **Red.** Create `tests/unit/test_run_memory_thread_safety.py`.

      Add a fixture that forces the low switch interval and restores it:
      capture `sys.getswitchinterval()`, call `sys.setswitchinterval(1e-6)`, `yield`, then
      restore the **captured** value — never a hardcoded `0.005`. Autouse it within this module
      only. Give it a docstring saying the forcing is load-bearing, not tuning: without it both
      tests below pass on the unfixed code. (Verified safe to mutate process-globally: no
      `pytest-xdist` is installed and `pyproject.toml:75` sets no parallel runner, so tests run
      serially.)

      Test A — **no lost updates**: 8 threads, 20 000 `bump_tool_call_count("retrieve")` calls
      each; assert `tool_call_count("retrieve") == 8 * 20_000`. Measured pre-fix: fails, actual
      ~52 000–60 000 of 160 000. Runtime 0.02 s.

      Test B — **pairwise-distinct returned counts**: this is the property `_consume_tool_budget`
      relies on, and it is stronger than the total (design D3). Use **32 threads released
      together on a `threading.Barrier`**, each performing exactly one bump and appending the
      returned value to a shared list under a plain `threading.Lock`; repeat for **200 trials**
      with a fresh `RunMemory` per trial; assert that across every trial no value was returned
      twice. Measured pre-fix over 3 reps of that exact shape: 91, 112 and 149 duplicate counts —
      red on 3/3. Runtime 0.54 s.

      Do **not** size test B to the operational shape (3 concurrent calls against `cap=2`):
      measured 0 duplicates in 2000 trials, so it would be green on broken code. 8 threads gives
      5/2000 per single trial — also unusable without the trial loop. Comment both numbers in the
      test so a future reader does not "simplify" the thread or trial count downward. If the test
      ever proves flaky on slower hardware, raise the trial count; never lower the assertion.

      Watch both fail: `python -m pytest tests/unit/test_run_memory_thread_safety.py -q`.

      **Green.** In `run_memory.py`: `import threading` (stdlib group, isort will place it), add
      `self._lock = threading.RLock()` in `__init__`, and hold it across the read-modify-write in
      `bump_tool_call_count` and across the read in `tool_call_count`.

      Use `RLock`, not `Lock`, from the start — task 2 locks `resolve_tool_input`, which calls
      `record_tool_call` at `run_memory.py:101`, and a non-reentrant lock deadlocks there
      (measured: 3 s hang; design D1). Choosing `RLock` now avoids churning the primitive in the
      next commit.

      Verify the reproduction snippet now prints `expected 160000 actual 160000` **with** the
      1e-6 forcing in place. Then confirm the four protected test modules still pass and are
      untouched:
      ```bash
      python -m pytest tests/unit/test_react_agent_tool_budget.py \
        tests/unit/test_retriever_tool_budget.py \
        tests/unit/test_active_memory_contextvar.py \
        tests/unit/test_active_memory_lifecycle.py -q
      git diff --stat origin/dev...HEAD -- tests/unit/test_react_agent_tool_budget.py \
        tests/unit/test_retriever_tool_budget.py tests/unit/test_active_memory_contextvar.py \
        tests/unit/test_active_memory_lifecycle.py
      ```
      The second command must print nothing.

      Format, gate, commit. Suggested subject: `fix(#267): make the per-tool call counter atomic`.

## 2. Extend atomicity to the other composite aggregates (red, then green, one commit)

- [ ] 2.1 This is the issue's step-5 audit, and it found a **harder defect than the counter**:
      `record_tool_input` (`run_memory.py:67-86`) iterates `self._tool_runs.items()` in Python
      while assigning into that dict, so a concurrent insert raises `RuntimeError: dictionary
      changed size during iteration`. Its only caller, `_store_tool_input`
      (`base_react.py:1426-1434`), catches `Exception` and logs at **debug** level — so the tool
      input is silently dropped from the `tool_inputs_by_id` metadata with no error surface.

      **Reproduce first**, and keep the output for the PR body:
      ```bash
      python - <<'PY'
      import sys, threading
      sys.setswitchinterval(1e-6)
      from src.archi.pipelines.agents.utils.run_memory import RunMemory
      def trial():
          m = RunMemory()
          for i in range(2000):
              m.record_tool_call(f"pre-{i}", "other", {"q": i})
          errs, start = [], threading.Barrier(2)
          def reader():
              start.wait()
              try: m.record_tool_input("retrieve", {"q": "z"})
              except RuntimeError as e: errs.append(str(e))
          def writer():
              start.wait()
              for i in range(2000): m.record_tool_call(f"new-{i}", "other", {"q": i})
          ts = [threading.Thread(target=reader), threading.Thread(target=writer)]
          [t.start() for t in ts]; [t.join() for t in ts]
          return len(errs)
      print("trials raising RuntimeError:", sum(1 for _ in range(5) if trial() > 0), "/ 5")
      PY
      ```
      Expect `5 / 5` — deterministic, 0.01 s. The `threading.Barrier` and the 2 000 pre-existing
      entries are both load-bearing: without the barrier, and with only 200 entries, the same
      shape raised in just 4 of 300 trials, which would be a flaky test rather than a proof.

      **Red.** In the same test module add:

      Test C — **concurrent insert during iteration**: the trial above, 5 trials, asserting no
      `RuntimeError` was raised and that the pending input was recorded rather than dropped.
      Measured pre-fix 5/5 red; with the fix 0/5.

      Test D — **the nested call chain does not deadlock**: seed a pending input with
      `record_tool_input("retrieve", {"query": "x"})`, then call
      `resolve_tool_input("call-1", "retrieve", {})` on a worker thread and `join(timeout=…)`;
      assert the thread finished, that the returned value is `{"query": "x"}`, and that
      `tool_runs` has exactly one entry (popped exactly once). This test is **green** on the
      unfixed code — it exists to pin the deadlock that a non-reentrant lock would introduce, so
      state in the PR body that it is a regression guard rather than a reproduction. Measured
      against a plain-`Lock` prototype: 3 s hang. Against the `RLock` prototype: completes,
      `tool_runs=1`.

      **Green.** Hold `self._lock` across the whole body of `record_tool_call`,
      `record_tool_input`, `resolve_tool_input` and `record_tool_documents`.

      Leave `record` and `note` **unlocked** and say so in the module docstring or a comment with
      the reason: each is a single `list.append`, which is atomic under the GIL and under
      free-threaded CPython's per-object locking, so locking them would be symmetry rather than
      correctness (design D4). Likewise leave the snapshot readers (`notes`, `events`,
      `tool_runs`, `unique_documents`, `intermediate_steps`, `tool_inputs_by_id`) unlocked; note
      that `tool_inputs_by_id` shares the Python-level `.items()` iteration shape but its only
      call site (`base_react.py:214`) runs after the tool node returns and already degrades
      safely, so it is audited-and-deferred rather than overlooked. Do not blanket-lock every
      method.

      Re-run the reproduction (expect `0 / 5`), re-run the four protected modules, re-check that
      their diff is empty. Format, gate, commit. Suggested subject:
      `fix(#267): make the run-memory tool-run aggregates atomic`.

## 3. Open the PR

- [ ] 3.1 Push the branch with `git push -u origin fix/issue-267-atomic-tool-counter` — the `-u`
      matters: the branch was created from `origin/dev` and therefore tracks the trunk, so a bare
      `git push` would target the wrong ref.

      Open the PR against `dev`: `gh pr create --repo fasrc/archi --base dev`. Put `Closes #267`
      **in the body**, not the title — a closing keyword in the title leaves the issue unlinked.
      Do not merge, and do not pass `--admin`; a human reviews and merges in daylight.

      The PR body MUST state, because the acceptance criteria require it:
      - The reproduction output from tasks 1.1 and 2.1, before and after, including the
        default-interval run that shows the unfixed code passing at 0.005.
      - **Which of the two counter tests actually failed pre-fix** — both do, in the shapes
        prescribed here: test A always, test B on 3/3 reps at 32 threads × 200 trials. Also state
        that test B could **not** be made to fail at the operational 3-thread shape (0 duplicates
        in 2000 trials), which is why it is sized up.
      - That test D is a green-before-and-after regression guard against the deadlock a
        non-reentrant lock would introduce, and why `RLock` is therefore required.
      - **The step-5 audit result**: the six methods that took the lock and why; that `record`
        and `note` stayed unlocked because each is one atomic `list.append`; and that
        `tool_inputs_by_id` shares the iteration shape but was deferred as a reader whose only
        call site runs after tool execution and already degrades safely.
      - That `base_react.py` is not in the diff, and that the four protected test modules have a
        zero-line diff.
      - A follow-up worth filing: `_store_tool_input` (`base_react.py:1426-1434`) swallows
        `record_tool_input` failures in a debug-level `except Exception`. This change removes one
        trigger for that swallow but not the swallow itself.

      Then request review as a **PR comment**, never in the body: `@codex review`.
