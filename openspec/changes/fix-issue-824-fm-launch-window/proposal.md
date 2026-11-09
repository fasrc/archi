## Why

Issue #824. Since #817 (PR #823, merged 2026-11-08), `fm_run_interruptible`
(`scripts/benchmarking/feature_matrix/lib.sh:151-167` on `origin/dev` `f457d3d1`) starts the
QA command in the background (`:153`) and records its PID on the next line (`:154`). Bash runs
a pending trap between two simple commands, so a HUP, INT or TERM sent to the wrapper's own
PID while bash executes the launch runs `fm__page_on_signal` after `:153` and before `:154`.
`FM_CHILD_PID` is still empty (`:35`, `:164`), `fm__stop_child` returns at once (`:173`), the
wrapper pages and dies of the signal, and the already-forked `archi qa` keeps running with no
wrapper, no page and no KILL watchdog. The window is microseconds wide, once per launch, and
only a signal to the wrapper's PID alone can land in it (a group signal reaches the command
too). Source: sync adversarial review round 4 of PR #823 (`e63771cf`), finding "[high] A
signal can arrive before the child PID is recorded", deferred to #824 because the window
cannot be hit by a timing test and the fix changes the wrapper's trap architecture.

## What Changes

- `lib.sh`: the three signal traps of `fm_arm_page_on_failure` move into one function,
  `fm__arm_signal_traps`, called from there and from `fm_run_interruptible`.
- `fm_run_interruptible` defers the three signals across the launch: before the launch it
  installs `trap 'fm__deferred_signal=HUP' HUP` (and INT, TERM); after `FM_CHILD_PID=$!` it
  calls `fm__arm_signal_traps` and, when `fm__deferred_signal` is set, `fm__page_on_signal`
  with the matching code (129 HUP, 130 INT, 143 TERM). The deferred call runs with the PID
  known, so `fm__stop_child` forwards the signal, waits the grace period, and the wrapper
  pages once and dies of that signal, as the #817 requirement says. The comment above the
  function gets one paragraph on the window and the deferral.
- New self-test check 130: the QA stub itself sends SIGTERM to the wrapper's PID as its
  first action (after it starts to ignore TERM), through a new launcher
  `signal-launch-self.py` that sends no signal of its own. With `FM_CHILD_GRACE=1` the run is
  killed after the grace period, the wrapper pages once and dies of SIGTERM, and no process
  is left. The check is green on the current code too (the window is microseconds); the red
  run uses the launch mutant below in a scratch worktree, never committed.
- The #817 design (`openspec/changes/fix-issue-817-fm-wrapper-signal-during-qa/design.md`,
  not archived) gets one paragraph on the deferral (issue plan step 4).

**Deviations from the issue text.** (1) The issue expects the mutant run on the pre-fix code
to print exactly one `not ok` (check 130). Measured: eight. The mutant widens the window to
0.3 s, and every PID-only or repeated-group launcher (checks 121-123 and 126-129) sends its
signal as soon as the stub touches the ready file, which is inside a 0.3 s window; those
checks fail with `launcher=-15 leftover` (`-1`, `-2` for HUP, INT) on the pre-fix code
exactly as 130 does. Checks 104, 105 and 112 pass under the mutant: a group signal reaches
the stub directly. (2) The mutant is `sleep 0.3 || :`, not `sleep 0.3`. A group signal
(checks 104, 105, 112, 128) kills the mutant's own `sleep`, and under `set -e` that exit
status ends the wrapper through the EXIT trap, not by the signal (measured on the fixed
code: `133 passed, 4 failed`, launcher `143 clean`, `129 clean`, `130 clean`, `143
leftover`); with `|| :` the fixed code passes all 137 under the mutant. The pre-fix code
fails the same eight checks under either form. (3) The stub ignores TERM before it signals
the wrapper, not after: the wrapper's forwarded TERM would otherwise race the stub's
`trap '' TERM`, and the page body would say `stopped by SIGTERM` on some runs and `killed
after 1s (SIGTERM ignored)` on others. With the ignore first the body is always the second.
(4) The mutant proof runs in a scratch worktree (`git worktree add --detach`), as the issue's
"Commands" section shows, because the self-test reaches sibling repository files through
`HERE/..` paths and a copy of the directory alone fails about 25 unrelated checks.

## Capabilities

### New Capabilities

### Modified Capabilities

- `feature-matrix-operator-paging`: adds a requirement that a signal which reaches the
  wrapper during the launch of the QA command is processed once the command's PID is known.
  The #504, #624, #629 and #817 changes that define this capability are not archived yet, so
  `openspec/specs/` has no `feature-matrix-operator-paging` spec and this change uses ADDED
  requirements only.

## Impact

- Code: `scripts/benchmarking/feature_matrix/lib.sh` only (the issue's constraint; `src/`
  and `qa_arm.sh` do not change).
- Tests: `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` (the gate
  runs it: `136 passed` on `origin/dev`, `137 passed` at the end). `scripts/` has no
  coverage gate, so check 130 and the mutant runs are the only proof; the PR body must say so.
- Docs: one paragraph in the #817 design. No runbook change: the operator-visible behavior
  of #817 (`kill <wrapper-pid>` stops the run and pages at once) is now true from the first
  microsecond of the launch too, and the runbook does not describe the window.
- No change to `fm_page`, `fm_die`, `fm_die_paged`, `fm__stop_child`, `fm__page_on_signal`,
  `fm__page_on_exit`, exit codes, page bodies, or precondition refusals. A wrapper that
  calls `fm_run_interruptible` must have armed paging first (`fm_arm_page_on_failure`), as
  all three call sites in `qa_arm.sh` do (`:87-90`, `:158-164`).
