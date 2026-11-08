## Why

Issue #817. Since #624 (PR #782, merged 2026-11-07), `fm_arm_page_on_failure`
(`scripts/benchmarking/feature_matrix/lib.sh:98-106` on `origin/dev` `54ce1eef`) sets HUP,
INT and TERM traps in `qa_arm.sh` before the QA run, and `fm__page_on_signal`
(`lib.sh:107-112`) pages once and ends the wrapper by the same signal. But bash runs a
trap only when the foreground command returns. The QA run (`qa_arm.sh:161` in arm mode,
`:88-89` in sweep mode) is a foreground command that takes an hour or more, so a signal
sent only to the wrapper's own PID (`kill <pid>` by hand) pages and stops the wrapper when
the run ends, not when the signal arrives. Measured on the host (issue body, 2026-10-25): a
trapped TERM ran 3 s later, at the end of a 4 s `sleep`. A signal to the whole process
group (a systemd stop, a closed terminal, Ctrl-C) reaches the run too, so that case pages
at once; self-test checks 104, 105 and 112 cover only that case. The runbook documents the
delay (`docs/docs/proposals/feature-matrix-campaign-2026.md:189-191`).

## What Changes

- `lib.sh` gets `fm_run_interruptible <cmd...>`: it starts the command in the background
  and waits for it with the interruptible `wait` builtin, so the signal traps run when the
  signal arrives; it returns the command's exit status, so `set -e` stops the wrapper on a
  failure as before. The command starts through `FM_PYTHON`, which resets SIGINT to its
  default and execs it (a background command of a script starts with SIGINT ignored).
- `fm__page_on_signal` first stops that command through a new `fm__stop_child`: it
  forwards the same signal, waits `FM_CHILD_GRACE` seconds (default 10, a new knob), sends
  KILL if the command is still there, then pages once and ends the wrapper by the signal,
  as before.
- The page body names the command and its fate (`... stopped by SIGTERM`, `... killed
  after 10s (SIGTERM ignored)`, `... exited 1`) through a new `FM_CHILD_FAILED` branch in
  `fm__page_on_exit`; the `fm_die` branch and the `file:line` branch do not change.
- `qa_arm.sh` runs its three `archi qa` calls (arm mode `:161`, sweep mode `:88` and `:89`)
  through `fm_run_interruptible`. Nothing else in `qa_arm.sh` changes.
- New self-test checks 121-127: TERM, HUP and INT sent only to the wrapper's PID during
  the QA run (one page within 5 s, death by that signal, no process left), a command that
  exits 1 (status kept, the body names it), paging off, the sweep call, and a command that
  ignores SIGTERM (killed after the grace period).
- The runbook sentence about the delay is replaced by the new behavior.

**Deviations from the issue text.** (1) The issue names "the long step after the arm point
in `archive_run.sh`". There is none: after `archive_run.sh:114` (arm mode) the next step is
a short Python heredoc, and after `:76` (sweep mode) the `fm_sweep_tools archive` call;
neither runs for minutes. `archive_run.sh` does not change. (2) The issue's criterion
`grep -n "takes effect when the current command ends" <runbook>` prints nothing on
`origin/dev` already, because the sentence wraps after "current command" (`:190-191`).
The real checks are `grep -c "own PID"` and `grep -c "takes effect when the current
command"` on the runbook: both print 1 on `origin/dev` and 0 after task 2.1. (3) The
branch is `fix/issue-817-fm-wrapper-signal-during-qa` (the nightly naming), not the
issue's `fix/fm-wrapper-signal-during-qa`. (4) The page body carries the command and its
fate in a dedicated variable, not in the ERR trap's record: a probe showed that the ERR
trap records the helper's own `return` line, not the call site (design D1).

## Capabilities

### New Capabilities

### Modified Capabilities

- `feature-matrix-operator-paging`: adds a requirement that a signal to the wrapper's own
  PID during the QA run stops the run and pages at once. The #504, #624 and #629 changes
  that define this capability are not archived yet, so `openspec/specs/` has no
  `feature-matrix-operator-paging` spec and this change uses ADDED requirements only.

## Impact

- Code: `scripts/benchmarking/feature_matrix/{lib.sh,qa_arm.sh}`.
- Tests: `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` (the gate
  runs it: `127 passed` on `origin/dev`, `134 passed` at the end). `scripts/` has no
  coverage gate, so the named checks are the only proof; the PR body must say so.
- Docs: `docs/docs/proposals/feature-matrix-campaign-2026.md` ("Paging (#504)" paragraph).
- Operator-visible: `kill <wrapper-pid>` during a QA run now stops the run within
  `FM_CHILD_GRACE` seconds and pages at once; the page body names the QA command and its
  fate. Ctrl-C still reaches the QA run directly (the command starts with SIGINT at its
  default, not ignored as a plain background command would be).
- No change to `archive_run.sh`, `fm_page`, `fm_die`, `fm_die_paged`, exit codes, or
  precondition refusals.
