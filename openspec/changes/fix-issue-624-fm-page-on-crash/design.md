## Context

`fm_page` and `fm_die_paged` (`scripts/benchmarking/feature_matrix/lib.sh:45` and `:86`,
from #504) page the operator by mail. Paging is off when `FM_PAGE_MAIL_TO` is empty. The
wrappers `qa_arm.sh` and `archive_run.sh` run under `set -euo pipefail` (`qa_arm.sh:27`,
`archive_run.sh:41`). Line numbers below are on the branch after the merge of `origin/dev`
`1ca97e24`.

Tasks 1.1 and 1.2 are done: D1, the first form of D2, and the two `qa_arm.sh` arm points
of D3 are on the branch (`lib.sh:29`, `:46`, `:96-114`; `qa_arm.sh:87`, `:157`). Loop 1
for this run had three independent refuters review the plan; their verified findings
changed D2 (the page body), added D7 (signal death), and added checks 102-107. The
pin-after-ledger work that this design first had (old D4 and D5) is gone from the scope:
#661 and #696 did it on `dev` (`archive_run.sh:297-310`, `sweep_tools.py archive`).

## Goals / Non-Goals

**Goals:** page once on any non-zero exit after long work that did not page already, and
on a SIGHUP, SIGINT or SIGTERM to the wrapper after that point; keep every exit status,
including a death by signal; never page for a precondition refusal; make the page say
why the run stopped.

**Non-Goals:** the corpus pin and its ordering (done by #661); making `fm_ledger_append`
itself atomic; paging from `run_arm.sh`, `reseed_arm.sh`, `lock_campaign.sh` or
`qa_prepare.sh`; a page when the wrapper gets SIGKILL (no trap can run); edits to the
#504 or #434 change folders (see "Other documents").

## Decisions

### D1. One guard, set in `fm_page` (done, task 1.1)

`fm_page` sets `FM_PAGED=1` as its first statement (`lib.sh:46`), before the
`FM_PAGE_MAIL_TO` check. As a result, every page path marks the run: `fm_die_paged`, and
the validator refusal page in `archive_run.sh:288` that calls `fm_page` and then
`exit "$FM_RC"`. A guard only in `fm_die_paged` misses the validator path and double-pages
there. `lib.sh:29` initializes `FM_PAGED=0`.

### D2. The trap: `fm_arm_page_on_failure <subject-context>`

Task 1.1 added it (`lib.sh:96-114`). Tasks 1.3 and 1.4 change three lines of it. The target
form:

```bash
FM_PAGED=0
FM_DIE_MSG=""
fm_die() { FM_DIE_MSG="$*"; printf 'feature_matrix: %s\n' "$*" >&2; exit 2; }

fm_arm_page_on_failure() { # $1 = subject context, e.g. "stack fm-00 arm 00"
  FM_PAGE_CONTEXT="$1"
  set -E
  trap 'fm__failed_cmd="$BASH_COMMAND"; fm__failed_line="${BASH_SOURCE[0]##*/}:$LINENO"' ERR
  trap 'fm__page_on_exit $?' EXIT
  trap 'fm__page_on_signal 129 HUP' HUP
  trap 'fm__page_on_signal 130 INT' INT
  trap 'fm__page_on_signal 143 TERM' TERM
}
fm__page_on_signal() { # $1 = 128 + the signal number, $2 = the signal name
  fm__page_on_exit "$1"
  trap - EXIT "$2"
  kill -s "$2" "$$"
  exit "$1"
}
fm__page_on_exit() { # $1 = the exit status
  [ "$1" != 0 ] && [ "$FM_PAGED" != 1 ] || return 0
  local body
  if [ -n "$FM_DIE_MSG" ]; then
    body="$(basename "$0") exited $1: feature_matrix: $FM_DIE_MSG"
  else
    body="$(basename "$0") exited $1 at ${fm__failed_line:-?}: ${fm__failed_cmd:-unknown}"
  fi
  fm_page "$FM_PAGE_CONTEXT: failed after long work (exit $1)" "$body"
}
```

- The `EXIT` trap does not call `exit`, so bash keeps the original exit status. `fm_page`
  always returns 0 (#504), so the trap cannot change the status.
- **The body says why (task 1.3).** `fm_die` ends with a plain `exit 2`, and the `ERR`
  trap never fires on `exit`. Without `FM_DIE_MSG`, the page for the "scored nothing"
  refusal (D5) had the body `qa_arm.sh exited 2 at line ?: unknown` (measured by a
  refuter). With it, the body carries the same line that `fm_die` prints to stderr.
  `fm_die` in a `$(...)` subshell sets the variable only in the subshell; the parent then
  fails on the assignment, and the `ERR` record names that assignment.
- **The line names its file (task 1.3).** `$LINENO` in the `ERR` trap is the line in the
  file of the failed command. For a failure inside a `lib.sh` function such as
  `fm_ledger_append`, the old body said `archive_run.sh exited 1 at line 415`, and
  `archive_run.sh` has no line 415. `${BASH_SOURCE[0]##*/}` gives `lib.sh:415` (measured).
- The issue plan says "the last stderr lines". This design does not capture stderr: a
  capture needs `exec 2> >(tee ...)`, which writes asynchronously, so the self-test greps
  of `$T/stderr` can race the exit. `FM_DIE_MSG` carries the refusal line without a
  capture, and the `ERR` record names any other failed command.
- `set -E` makes the `ERR` trap fire inside functions. The trap only sets two variables,
  and `ERR` never fires in an `if`, `||` or `&&` context, so no exit status changes.
- An unpaged `fm_die` after the arm point also pages. This matches the requirement ("any
  non-zero exit it did not page itself"). See D5 for the one such refusal on `dev`.

### D3. Arm points

- `qa_arm.sh` sweep mode: directly before the `qa run` call (`qa_arm.sh:87`, done).
  Context `stack $STACK arm $STEM`.
- `qa_arm.sh` arm mode: directly before the `qa` call (`qa_arm.sh:157`, done). Context
  `stack $STACK arm $ARM`.
- `archive_run.sh` sweep mode: directly after the "no artifact" line
  (`archive_run.sh:67`) and the `ARTIFACT=` line after it (`:68`). Context
  `stack $STACK sweep`. New in task 1.5.
- `archive_run.sh` arm mode: directly after the "no artifact" line
  (`archive_run.sh:104`) and the `ARTIFACT=` line after it (`:105`). Context
  `stack $STACK arm $ARM`. New in task 1.5.

Every refusal before these points is a precondition and never pages (#504 spec: "usage
errors, `fm_require_*` checks, 'still running', 'no artifact'"). Check 107 guards the
arm-mode boundary: with paging on, "nothing archived" sends no mail. A refuter showed
that an arm point placed one statement too early made that refusal page while all other
checks stayed green. In sweep mode, every failure after the `archive_run.sh` arm point
already pages through `fm_die_paged`, so that arm point is a backstop (for example, a
failed `mktemp`) with no self-test check.

### D4. Self-test fixtures

- **Ledger failure.** Reuse dev's `$T/bin/python-ledger-fail` shim (defined above check 78,
  #661/#696). It exits 1 for the one Python script that contains `rows.append(entry)`
  (the `fm_ledger_append` heredoc) and runs every other script with the real Python. It
  also works when the loop container runs as root, where `chmod 0500` does not stop a
  write. Do NOT write a second shim with the same name: it would replace dev's shim and
  change checks 78 and 79. The checks assert `FM_LEDGER=` in the page body, so they prove
  that the ledger append is the command that failed.
- **Archive stacks.** Check 106 uses a new stack, `fm-pin624`, set up the way check 78
  sets up `fm-pin78`. A stack with an archived run 1 (for example `fm-tags` or
  `fm-pin80`) refuses as "already archived" before the ledger write. Check 107 uses
  another new stack, `fm-noart`, with a `run_arm.sh` start row and no artifact.
- **QA runs.** The `qa_arm.sh` arm-mode checks reuse the setup of checks 90 and 98
  (`FM_AGENT_SPEC="$T/cfg/spec.md"`, `00 "$T/arms/00-baseline.yaml" --profile
  "$T/cfg/qa/profile.yaml"`). A refused run keeps its output directory, and the next run
  without `--run` takes the next number (#434), so the checks can run one after another.

### D5. The "scored nothing" refusal pages through the trap

`fm_require_scored` (`lib.sh:425`, #434) refuses with `fm_die` after the QA run, at
`qa_arm.sh:94` (sweep) and `:175` (arm). Both lines are after the arm point, so the trap
pages this refusal once, with the exit status 2 unchanged, and (D2) with the refusal line
in the body. #434 design D6
(`openspec/changes/fix-issue-434-qa-arm-fail-closed/design.md:108-116`) chose this: "that
trap will page this refusal too, once and with no change here". The runbook still says
that this refusal "does **not** page", in two places. Task 3.1 changes both.

### D6. The signal checks (104, 105)

A small Python launcher, written by the self-test to `$T/bin/signal-launch.py`, runs
`qa_arm.sh` arm mode with paging on:

1. It starts the wrapper with `subprocess.Popen(..., start_new_session=True)`, so the
   wrapper leads its own process group, whatever the job control of the calling shell.
2. It waits for `$T/qa-started` (bounded: 30 s, and it stops early if the wrapper exits).
   The `archi` stub touches that file and then runs `sleep 30` when `$T/qa-slow` exists
   and `$1` is `qa`. A ready file, not a fixed sleep, decides when to send the signal,
   so a slow host cannot send it before the arm point.
3. It sends the signal to the whole group (`os.killpg`), the way systemd or a closed
   terminal does. The stub and its `sleep` end at once.
4. It waits for the wrapper (bounded: 30 s), then sends SIGKILL to the group, and prints
   the wait status and `clean` (no process left) or `leftover`. A negative status means
   that a signal killed the wrapper.

The check passes when the launcher prints `-15 clean` (TERM) or `-1 clean` (HUP), the mail
stub got exactly one message with `failed after long work (exit 143)` (or `129`), and no
ledger row was written. Measured in Loop 1: 15 of 15 runs each gave `143 clean` and 1
page with the task 1.1 code (the red state) and `-15 clean` and 1 page with D7.

Why not the issue's "`timeout` around the background process": the check must tell a
death by signal from a normal exit with the same number. A shell `wait` reports 143 for
both, so a launcher that reads the real wait status is necessary anyway. The launcher
also gives the bound and the group cleanup that the issue asked `timeout` for. Why not
`setsid` from bash: when the calling shell has job control, `setsid` forks, `$!` names a
process that already exited, and the wrapper runs on as an orphan (measured in Loop 1).
`ps` is not in the `archi-loop` image, so no check may use it. Check 112 sends SIGINT the
same way. A background job of a non-interactive shell starts with SIGINT ignored, and bash
cannot trap a signal that was ignored at entry, so the launcher sets SIGINT to the default
in the child (`preexec_fn`). Measured: 119 passed with the self-test run under
`trap '' INT`.

### D7. A signal ends the wrapper as that signal (task 1.4)

Task 1.1's `fm__page_on_signal` ended with `exit 143`. Then a caller saw a normal exit, not
a death by signal. Before this change, the wrapper had no signal trap and died of the
signal. This matters for the runbook's chains: a calling bash that waits on a child after
Ctrl-C stops only if the child died of SIGINT; if the child exits normally, bash assumes
the child handled the signal and goes on to the next step (a refuter measured a chain that
went on to arms 2 and 3). The new form pages, clears the `EXIT` trap and the signal's own
trap, and sends the same signal to itself (`kill -s "$2" "$$"`), so the wrapper dies of it.
The `exit "$1"` after it is a fallback that does not run when the signal is delivered.
Exactly one page is sent in both orders that bash can choose (the signal trap first, or
`set -e` and the `EXIT` trap first), because of the D1 guard.

## Other documents

- `openspec/changes/fix-issue-504-fm-page-mail/specs/feature-matrix-operator-paging/spec.md`
  says "Precondition refusals before long work (usage errors, `fm_require_*` checks, ...)
  MUST NOT page". `fm_require_scored` runs after long work, so this sentence stays true and
  this change does not edit the #504 folder. The #504 proposal's "when it lands it uses
  `fm_die_paged`" and #434 design D6 are history; they are not edited either.
- The self-test section comment above check 60 ("page by mail on the four refusals that
  follow long unattended work") becomes incomplete. Task 1.5 adds the failure trap to it.
- The runbook paragraph "**Paging (#504).**" and the `qa_arm.sh` step that reads
  `summary.json` change in task 3.1.

## Risks / Trade-offs

- `trap ... EXIT` (and the HUP, INT and TERM traps) replace any earlier trap. No wrapper
  and not `lib.sh` set another trap: `grep -n trap
  scripts/benchmarking/feature_matrix/{qa_arm,archive_run}.sh` is empty after the merge.
  In `lib.sh`, the only other `trap` is inside the `fm_page` watchdog subshell, which has
  its own trap table.
- A TERM that reaches only the wrapper (a `kill <pid>` by hand) pages when the current
  child ends, because bash runs a signal trap only after the foreground child exits. A
  systemd stop or a closed terminal signals the whole group, so the page is sent at once.
- The "scored nothing" refusal now pages (D5). This is a behavior change that an operator
  can see: one more mail for a void QA run.
- An operator who presses Ctrl-C during a QA run gets one mail. This is accepted.
