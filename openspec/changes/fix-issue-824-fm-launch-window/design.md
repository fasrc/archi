## Context

Line numbers are on `origin/dev` `f457d3d1` (PR #823 merged 2026-11-08). `lib.sh:107-115`
holds `fm_arm_page_on_failure` with the three signal traps (`:112-114`); `:116-126`
`fm__page_on_signal`; `:151-167` `fm_run_interruptible`, whose launch is `:153` and whose
PID record is `:154`; `:172-199` `fm__stop_child`, which returns at once on an empty
`FM_CHILD_PID` (`:173`). The self-test is at `136 passed, 0 failed` (76 s on the host). Loop 1
built every stage of D5 in scratch worktrees (`git worktree add --detach`) and ran each one,
one after another: the totals in `tasks.md` are measured, not predicted. The two patches in
`patches/` are the exact diffs of the change; they apply in order to a fresh checkout of
`origin/dev` and give the final text byte for byte.

## Goals / Non-Goals

**Goals:** a HUP, INT or TERM sent to the wrapper's PID at any point of the QA command's
launch (after the fork, before the PID is recorded) is processed with the PID known, so the
command is forwarded the signal, killed after the grace period if it ignores it, and never
outlives the wrapper; the #817 behavior for a signal during the run, and the #624 behavior
for a group signal, do not change; `lib.sh` is the only production file in the diff.

**Non-Goals:** `qa_arm.sh`, `archive_run.sh` and the other wrappers; `fm__stop_child`,
`fm__page_on_signal`, `fm__page_on_exit`, `fm_page`, `fm_die`, `fm_die_paged`; a page on
SIGKILL; a signal to a command that has descendants of its own (round-4 pushback on PR #823:
`archi qa` is one process); the runbook; edits to the #504, #624, #629 or #817 change folders
other than the one paragraph of task 2.1.

## Decisions

### D1. Defer the three signals across the launch, then re-arm and process

```bash
fm_run_interruptible() { # $@ = the command and its arguments
  FM_CHILD_CMD="$*"; FM_CHILD_FAILED=""; fm__deferred_signal=""
  trap 'fm__deferred_signal=HUP' HUP
  trap 'fm__deferred_signal=INT' INT
  trap 'fm__deferred_signal=TERM' TERM
  "$FM_PYTHON" -c '...SIGINT to SIG_DFL; os.execvp(...)' "$@" &
  FM_CHILD_PID=$!
  fm__arm_signal_traps
  case "$fm__deferred_signal" in
    HUP) fm__page_on_signal 129 HUP ;;
    INT) fm__page_on_signal 130 INT ;;
    TERM) fm__page_on_signal 143 TERM ;;
  esac
  local rc=0
  ...
```

- Bash runs a pending trap between two simple commands. With the deferring traps in place,
  a signal that arrives during the launch runs a one-line assignment after the fork, and the
  next simple command records the PID. The re-arm restores the handler of
  `fm_arm_page_on_failure`; the `case` then runs that same handler for a recorded signal,
  with `FM_CHILD_PID` set, so `fm__stop_child` forwards it and the wrapper ends as the #817
  requirement says. `fm__page_on_signal` never returns (it ends the wrapper by the signal),
  so the `wait` loop below it is reached only when nothing was recorded.
- There is no gap. A signal between the first deferring `trap` and the third runs the armed
  handler with no command started yet: that is the pre-launch case, in which there is nothing
  to orphan, unchanged. A signal between `FM_CHILD_PID=$!` and `fm__arm_signal_traps` is
  recorded and processed by the `case`. A signal between `fm__arm_signal_traps` and the
  `case` runs the armed handler with the PID known. Two different signals before the re-arm:
  the last one wins (one variable), and the wrapper dies of that one.
- `fm__arm_signal_traps` holds the three `trap` lines that `fm_arm_page_on_failure` had
  (`:112-114`), called from there and from here (the issue's plan step 1). Not a
  save-and-restore through `trap -p`: for a caller that had no trap on a signal, `trap -p`
  prints nothing, so an `eval` of its output would leave the deferring trap in place. The
  function form makes `fm_run_interruptible` require an armed caller, which all three call
  sites are (`qa_arm.sh:87-90`, `:158-164`); the comment above the function says so.
- The deferring trap bodies are one assignment each and use nothing that the launch sets:
  they run between the fork and the PID record, and `$!` is not read there.
- The variable is `fm__deferred_signal` (the issue's name; the `fm__` prefix is the file's
  private namespace, as `fm__failed_cmd` and `fm__failed_line`), declared with the other
  state at the top of `lib.sh` so `set -u` is satisfied, and reset at every call.

### D2. The comment paragraph

The comment above `fm_run_interruptible` (`:140-150`) gets one paragraph that names the
window, the deferral, the re-arm, and the armed-caller requirement (issue plan step 4). The
comment above `fm_arm_page_on_failure` does not change; `fm__arm_signal_traps` gets a
two-line comment of its own.

### D3. Check 130: the QA stub signals the wrapper itself

- The window is microseconds wide and once per launch, so no launcher can hit it on the
  real code. The deterministic observable the issue asks for is the processing path: a
  signal that arrives as early as a real one can is processed with the PID known. The stub
  mode `qa-slow-selfterm` does `trap '' TERM; kill -TERM $PPID; touch <ready>; exec sleep 30`:
  `$PPID` is the wrapper (the command is a direct child of the wrapper shell; `qa_arm.sh`
  calls `fm_run_interruptible` in its main shell, not in a subshell or a pipeline).
- The stub ignores TERM before it signals, not after as the issue's text says: the wrapper's
  forwarded TERM would otherwise race the stub's `trap`, and the body would read `stopped by
  SIGTERM` on some runs and `killed after 1s (SIGTERM ignored)` on others. With the ignore
  first, the fate is always the grace-period KILL, and the check pins that body.
- A new launcher `signal-launch-self.py`: `signal-launch-pid.py` without its `os.kill`
  (the stub sends the signal), with the same ready-file wait, 5 s bound and group check.
  The existing launchers are the fixtures of checks 104-129 and are not edited.
- The check runs `qa_arm.sh` arm mode with `FM_CHILD_GRACE=1` and expects `-15 clean`, one
  page with `exit 143` and `killed after 1s (SIGTERM ignored)`, and no ledger row: the
  assertions of check 127, with the signal from the stub instead of the launcher. It passes
  on the pre-fix code without the mutant (the signal lands after the window), which is what
  the issue expects; it is the regression check for the deferred path.

### D4. The launch mutant: `sleep 0.3 || :`, never committed

- `sed -i '/^  FM_CHILD_PID=\$!$/i\  sleep 0.3 || :' lib.sh` widens the window to 0.3 s. The
  stub's self-signal (130) and every launcher that signals on the ready file (121-123,
  126-129) then land inside it: the ready file is touched tens of milliseconds after the
  fork. So the mutant turns eight checks red on the pre-fix code, not the one the issue
  expects; the extra seven are the #817 checks, which are also tests of the window once it
  is wide. Checks 104, 105 and 112 stay green: a group signal reaches the stub directly.
- The issue's form, `sleep 0.3`, is wrong on the fixed code for a group signal: the signal
  kills the mutant's own `sleep` (exit 143), the deferring trap runs, and `set -e` then ends
  the wrapper through the EXIT trap with status 143 instead of by the signal. Measured
  (g1m-plain in D5): 104, 105, 112 fail with `launcher=143 clean`, `129 clean`, `130 clean`
  and 128 with `143 leftover`. `|| :` keeps the window semantics (a group signal ends it
  early, which is still "inside the window") and removes the artifact; the pre-fix code fails
  the same eight checks under either form.
- The mutant runs only in a scratch worktree (`git worktree add --detach /tmp/fm-mutant-824
  HEAD`): the self-test reaches sibling repository files through `HERE/..` paths, so a copy
  of the directory alone is not enough, and the mutant must never reach the real tree.

### D5. Measured stages (Loop 1, 2026-11-09, scratch worktrees of `origin/dev` `f457d3d1`)

| stage | content | self-test |
|---|---|---|
| base | `origin/dev` | `136 passed, 0 failed` |
| g1 | + patch A (test and code), no mutant | `137 passed, 0 failed` |
| r1 | pre-fix code + the test half of patch A + mutant `sleep 0.3 \|\| :` | `129 passed, 8 failed`: 121 (`-15 leftover`), 122 (`-1 leftover`), 123 (`-2 leftover`), 126, 127, 128 (`-15 leftover`), 129 (`-15 leftover caught`), 130 (`-15 leftover`) |
| r1-plain | the same with the issue's `sleep 0.3` | `129 passed, 8 failed`: the same eight |
| g1m | + patch A + mutant `sleep 0.3 \|\| :` | `137 passed, 0 failed` |
| g1m-plain | + patch A + `sleep 0.3` | `133 passed, 4 failed`: 104 (`143 clean`), 105 (`129 clean`), 112 (`130 clean`), 128 (`143 leftover`); the mutant's artifact, D4 |
| m1 | g1m with the three deferring `trap` lines deleted | `129 passed, 8 failed`: the r1 eight (`leftover`) |
| m2 | g1m with the `case ... esac` processing site deleted | `127 passed, 10 failed`: 104, 105, 112 (`143/129/130 clean`), 121, 122, 123, 126, 127, 129, 130 (`hung leftover`); 128 passes because its second group TERM, 200 ms later, lands after the first one ended the mutant's `sleep` and the traps were re-armed |
| m3 | g1 (no mutant) with the `fm__arm_signal_traps` call after the launch deleted | `126 passed, 11 failed`: 104, 105, 112 (`143/129/130 clean`), 121, 122, 123, 126, 127, 128, 129, 130 (`hung leftover`) |

Each stage took 76-116 s on the host. In m2 and m3 the group checks fail with `143 clean`
because the wrapper never runs `fm__page_on_signal`: the group signal ends the stub, `wait`
returns 143, and `set -e` ends the wrapper through the EXIT trap, not by the signal. Patch A
adds 7 lines that name `fm__deferred_signal` and 4 that name `fm__arm_signal_traps`; neither
name exists on `origin/dev`.

## Risks / Trade-offs

- `fm_run_interruptible` now installs the page traps itself after its launch. A caller that
  never armed paging would get them too, and a recorded signal in such a caller would reach
  `fm__page_on_exit` with `FM_PAGE_CONTEXT` unset (`set -u`). No such caller exists; the
  comment states the requirement. The alternative (`trap -p` save and restore) has the
  unset-trap hole of D1.
- Two signals before the re-arm: the last one is processed, the first is dropped. The
  wrapper was being stopped either way; the difference is which signal it dies of.
- A signal during the launch is now processed a few microseconds later than an armed
  handler would have run it, after the PID record and the re-arm. No observable change.
- The deferring traps are installed on every call, including the two sweep-mode calls that
  run back to back (`qa_arm.sh:89-90`); between the first call's end and the second call's
  first `trap`, the armed handler is in place and `FM_CHILD_PID` is empty, which is the
  pre-launch case (nothing to orphan), unchanged.
- Check 130 adds about 2 s to the self-test (a 1 s grace period plus the page).
- `scripts/` has no coverage gate: check 130 and the mutant runs in D5 are the only proof,
  and the PR body must say so.
