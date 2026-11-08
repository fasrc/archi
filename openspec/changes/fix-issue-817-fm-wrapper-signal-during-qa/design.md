## Context

Line numbers are on `origin/dev` `54ce1eef` (PR #782 merged 2026-11-07). `lib.sh:98-124`
holds `fm_arm_page_on_failure`, `fm__page_on_signal` and `fm__page_on_exit` (#624).
`qa_arm.sh` runs under `set -euo pipefail` (`:27`) and calls `archi qa` three times:
`:88` (`qa run`, sweep), `:89` (`qa score`, sweep), `:161` (arm mode, under the env prefix
`PG_PASSWORD_FILE=... HOST_MODE=1 \` on `:158-160`). The self-test is at
`127 passed, 0 failed` (64 s on the host). Loop 1 built every stage of D6 in scratch
copies of the tree and ran each one: the totals in `tasks.md` are measured, not predicted.
The three patches in `patches/` are the exact diffs of those stages; they apply in order
to a fresh copy of `origin/dev` and give the final stage byte for byte.

## Goals / Non-Goals

**Goals:** a HUP, INT or TERM sent only to the wrapper's PID during the QA run stops the
run and pages within seconds; the wrapper still dies of that signal; the QA command's
exit status still ends the wrapper with that status and pages once; the page body names
the command and its fate; no process is left behind; `qa_arm.sh --sweep` gets the same.

**Non-Goals:** `archive_run.sh` (no long step after its arm points; see the proposal);
`run_arm.sh`, `reseed_arm.sh`, `lock_campaign.sh`, `qa_prepare.sh`; changes to `fm_page`,
`fm_die`, `fm_die_paged`; a page on SIGKILL; stderr capture; edits to the #504, #624 or
#629 change folders.

## Decisions

### D1. `fm_run_interruptible`: a background command and an interruptible wait

```bash
fm_run_interruptible() { # $@ = the command and its arguments
  FM_CHILD_CMD="$*"; FM_CHILD_FAILED=""
  "$FM_PYTHON" -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' "$@" &
  FM_CHILD_PID=$!
  local rc=0
  while :; do
    wait "$FM_CHILD_PID" && rc=0 || rc=$?
    [ "$rc" -gt 128 ] && kill -0 "$FM_CHILD_PID" 2>/dev/null || break
  done
  FM_CHILD_PID=""
  [ "$rc" = 0 ] || FM_CHILD_FAILED="$FM_CHILD_CMD exited $rc"
  return "$rc"
}
```

- `wait` is the one bash builtin a trapped signal interrupts at once. The traps that
  `fm_arm_page_on_failure` sets end the wrapper, so a `wait` that returns has reaped the
  command. The loop guards the other case (a trap that returns): `wait` then gives 128+N
  while the command still runs. A status of 128 or less is a real exit, and a reaped PID
  fails `kill -0`, so the loop cannot spin on a reused PID.
- The return value is the command's status. Under `set -e` the wrapper exits with it and
  the `EXIT` trap pages (check 124: exit 1, one page). A probe showed that the ERR trap
  then records `return "$rc"` in `lib.sh`, not the call site: `BASH_COMMAND` is the last
  simple command, even with `return "$rc" || true` (which moves the line to the call site
  but keeps the text). So the ERR record is useless for this page body; D3 carries the
  command and its status in `FM_CHILD_FAILED` instead. The issue offered either route.
- An env prefix on the call (`HOST_MODE=1 fm_run_interruptible ...`) reaches the
  background command: bash exports a temporary assignment on a function call for the
  call's duration (probe: `X=1 f sh -c 'echo $X'` printed 1 from a `&` child of `f`).

### D2. SIGINT: the command starts through `FM_PYTHON`

A background command of a script without job control starts with SIGINT ignored, and an
ignored signal cannot be reset by the command itself (bash: "signals ignored upon entry
to the shell cannot be trapped or reset"; Python leaves an inherited SIG_IGN alone). A
forwarded INT would then never reach `archi qa`, and Ctrl-C would no longer interrupt it
the way a foreground command is interrupted. So the command starts through
`"$FM_PYTHON" -c '... signal.signal(SIGINT, SIG_DFL); os.execvp(...)'`: the same PID,
SIGINT at its default, no extra process after the exec. Probe: a plain `&` child printed
`SIGINT: 1` (SIG_IGN); the trampolined child printed Python's default INT handler. Check
123 (INT to the PID) fails without it (mutation m2 in D6: `hung leftover`). Not `set -m`:
job control would put the command in its own process group, so a group signal (Ctrl-C,
a closed terminal) would no longer reach it directly. The self-test's
`python-ledger-fail` shim (check 102) passes this `-c` script through to the real Python.

### D3. `fm__stop_child`, the no-op traps, and the page body

```bash
fm__page_on_signal() { # $1 = 128 + the signal number, $2 = the signal name
  trap ':' HUP INT TERM
  fm__stop_child "$2"
  fm__page_on_exit "$1"
  trap - EXIT HUP INT TERM
  kill -s "$2" "$$"
  exit "$1"
}
fm__stop_child() { # $1 = the signal name
  [ -n "$FM_CHILD_PID" ] || return 0
  local pid="$FM_CHILD_PID" grace="$FM_CHILD_GRACE" fate="stopped by SIG$1" wd wrc=0 rc=0
  [[ "$grace" =~ ^[1-9][0-9]*$ ]] || grace=10
  kill -s "$1" "$pid" 2>/dev/null || true
  ( trap 'kill $(jobs -p) 2>/dev/null || true; exit 0' TERM
    sleep "$grace" & wait $! || true
    kill -KILL "$pid" 2>/dev/null || true; exit 3 ) &
  wd=$!
  while :; do
    wait "$pid" 2>/dev/null && rc=0 || rc=$?
    [ "$rc" -gt 128 ] && kill -0 "$pid" 2>/dev/null || break
  done
  kill -TERM "$wd" 2>/dev/null || true; wait "$wd" 2>/dev/null || wrc=$?
  [ "$wrc" != 3 ] || fate="killed after ${grace}s (SIG$1 ignored)"
  FM_CHILD_PID=""
  FM_CHILD_FAILED="$FM_CHILD_CMD $fate"
}
```

- Forward the same signal, so `archi qa` stops the way the operator asked (INT gives
  Python's KeyboardInterrupt; TERM ends it at once). Wait for it, bounded by a KILL
  watchdog after `FM_CHILD_GRACE` seconds (default 10; a value that is not a positive
  whole number gives 10, as `FM_MAIL_TIMEOUT` falls back to 60). Task 1.1 lands the form
  without the watchdog (forward, then wait); task 1.2 adds the watchdog, the knob and its
  header line, with check 127 red first.
- The watchdog is the `fm_page` watchdog pattern: it exits 3 once it has sent KILL, and
  its TERM trap kills its own `sleep`, so a command that ended in time leaves no process
  in the wrapper's group (the self-test launcher proves `clean`).
- **`trap ':'`, not `trap ''`.** Loop 1's first form ignored the three signals during the
  stop. A subshell cannot trap a signal that was ignored when it started, so the
  watchdog's TERM trap never ran, `wait "$wd"` blocked for the whole grace period, and
  checks 121-123 and 126 failed with `hung leftover` while check 127 passed (measured).
  With the no-op trap a second signal only interrupts a wait; both waits (here and in
  `fm_run_interruptible`) repeat while the command runs. A second signal does not
  shorten the grace period.
- `FM_CHILD_FAILED` is the page-body record: `<command> exited N`, `<command> stopped by
  SIGTERM`, or `<command> killed after 10s (SIGTERM ignored)`. `fm__page_on_exit` reads it
  after `FM_DIE_MSG` and before the `file:line` record, so the bodies that checks 102, 103
  and 106 pin do not change. The command is the expanded `$*` (paths included), one line.
- `fm__page_on_signal` without a running command (a signal after the QA run, during
  `fm_fingerprint`) is the #624 behavior: `fm__stop_child` returns at once.

### D4. Call sites

Three in `qa_arm.sh`: `:88` and `:89` (sweep) and `:161` (arm mode, under the env
prefix). Each gets the `fm_run_interruptible ` prefix, plus a one- or two-line comment
that names #817; nothing else in `qa_arm.sh` changes, and the arm points stay where #624
put them (`:87`, `:157`). `archive_run.sh` is unchanged (proposal, deviation 1).

### D5. Self-test fixtures

- The `archi` stub's slow mode (`qa-slow`) becomes `exec sleep 30`: one process, as
  `archi qa` is one process, so a signal forwarded to its PID ends it. Before, the stub
  was a bash process plus a `sleep` child; a TERM to the bash PID would leave the `sleep`
  behind and the launcher would print `leftover`. Checks 104, 105 and 112 (group signals)
  still pass with it (every stage of D6 ran them).
- New stub modes: `qa-fail` (exit 1 from `qa`, for checks 124-125) and `qa-slow-noterm`
  (`trap '' TERM; exec sleep 30`; an ignored signal survives exec, as `mail-hang-noterm`
  relies on; check 127).
- A PID-only launcher `signal-launch-pid.py`: the #624 launcher with `os.kill` instead of
  `os.killpg` and a 5 s wait bound, so an unfixed wrapper prints `hung leftover` and a
  fixed one `-15 clean` (`-1 clean` for HUP, `-2 clean` for INT). The existing launcher
  is not edited. "Within 5 s" in the checks is this bound.
- Check 126 runs sweep mode (`--sweep "$SW/configs" --stack r0 --arm fasrc-docs-r0b-icl`,
  the setup of checks 99-101); the slow mode fires on `qa run`, and the body names
  `archi qa run`.
- Check 125 asserts exit 1 itself, not only "the same status as 124": with the helper's
  status dropped (mutation m3) both runs exit 2 ("no summary.json"), so an equality
  check alone would pass.

### D6. Measured stages (Loop 1, 2026-11-08, scratch copies of `origin/dev` `54ce1eef`)

| stage | content | self-test |
|---|---|---|
| base | `origin/dev` | `127 passed, 0 failed` |
| r1 | + checks 121-126 (patch A, test half) | `128 passed, 5 failed`: 121, 122, 123, 126 (`launcher=hung leftover`), 124 (the body says `at qa_arm.sh:158: PG_PASSWORD_FILE=...`, not the command's status) |
| g1 | + task 1.1 code (patch A, rest) | `133 passed, 0 failed` |
| r2 | + check 127 (patch B, test half) | `133 passed, 1 failed`: 127 (`hung leftover`) |
| g2 | + task 1.2 code (patch B, rest) + runbook (patch C) | `134 passed, 0 failed` |
| m1 | g2 with the forward line `kill -s "$1" "$pid" 2>/dev/null \|\| true` replaced by `:` | `130 passed, 4 failed`: 121, 122, 123, 126 |
| m2 | g2 with the `"$FM_PYTHON" -c '...' "$@" &` line replaced by `"$@" &` | `133 passed, 1 failed`: 123 |
| m3 | g2 with the helper's last two lines replaced by `return 0` | `132 passed, 2 failed`: 124, 125 (both exit 2) |
| m4 | g2 with `kill -KILL "$pid" 2>/dev/null \|\| true; exit 3 ) &` replaced by `exit 3 ) &` | `133 passed, 1 failed`: 127 |

Each stage took 64-80 s on the host. The runbook greps on g2: `own PID` 0, `takes
effect when the current command` 0, `#817` 1 (on base: 1, 1, 0).

## Risks / Trade-offs

- The QA command now runs as a background job of the wrapper. Its stdout and stderr are
  the wrapper's (no redirection), and it is in the wrapper's process group, so group
  signals reach it as before. A wrapper killed by SIGKILL leaves the command running, as
  before this change.
- Ctrl-C to a chain reaches `archi qa` directly (SIGINT at its default, D2) and the
  wrapper forwards INT too, so the run can get INT twice within milliseconds. Python
  raises one KeyboardInterrupt and, if a handler is still running, a second one. Accepted:
  the run was being stopped, and its ledger row is never written.
- `FM_PYTHON` is now on the start path of the QA run. Every wrapper already requires it
  (YAML and JSON edits).
- A second signal during the stop does not shorten the grace period; `FM_CHILD_GRACE=1`
  does. The default of 10 s is a judgment: `archi qa` ends on TERM at once and on INT
  within a second, so the watchdog is a backstop.
- The ERR trap now also records `return "$rc"` in `lib.sh` when the QA command fails;
  the page body does not use that record (D3), and no check pins it.
