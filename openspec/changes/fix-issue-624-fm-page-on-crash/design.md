## Context

`fm_page` and `fm_die_paged` (`scripts/benchmarking/feature_matrix/lib.sh:43-61`, from
#504) page the operator by mail. Paging is off when `FM_PAGE_MAIL_TO` is empty. The
wrappers `qa_arm.sh` and `archive_run.sh` run under `set -euo pipefail` (`qa_arm.sh:27`,
`archive_run.sh:36`). Line numbers below are on `origin/dev` `6156b760`.

## Goals / Non-Goals

**Goals:** page once on any non-zero exit after long work that did not page already; keep
every exit code; never page for a precondition refusal; never leave a pin without its
ledger row.

**Non-Goals:** making `fm_ledger_append` itself atomic (it truncates and rewrites
`ledger.json`); paging from `run_arm.sh`, `reseed_arm.sh`, `lock_campaign.sh` or
`qa_prepare.sh`; recovery when the pin write fails after the ledger row is written (the
trap pages, and the operator writes the pin by hand from the ledger row).

## Decisions

### D1. One guard, set in `fm_page`

`fm_page` sets `FM_PAGED=1` as its first statement, before the `FM_PAGE_MAIL_TO` check.
As a result, every page path marks the run: `fm_die_paged`, and the validator refusal page
in `archive_run.sh:263` that calls `fm_page` and then `exit "$FM_RC"`. A guard only in
`fm_die_paged` misses the validator path and double-pages there. `lib.sh` initializes
`FM_PAGED=0`.

### D2. The trap: `fm_arm_page_on_failure <subject-context>`

```bash
fm_arm_page_on_failure() { # $1 = subject context, e.g. "stack fm-00 arm 00"
  FM_PAGE_CONTEXT="$1"
  set -E   # errtrace: the ERR trap also fires inside functions such as fm_ledger_append
  trap 'fm__failed_cmd="$BASH_COMMAND"; fm__failed_line="$LINENO"' ERR
  trap 'fm__page_on_exit $?' EXIT
  trap 'fm__page_on_signal 129' HUP
  trap 'fm__page_on_signal 130' INT
  trap 'fm__page_on_signal 143' TERM
}
fm__page_on_signal() { # $1 = 128 + the signal number
  fm__page_on_exit "$1"
  trap - EXIT
  exit "$1"
}
fm__page_on_exit() { # $1 = the exit status
  [ "$1" != 0 ] && [ "$FM_PAGED" != 1 ] || return 0
  fm_page "$FM_PAGE_CONTEXT: failed after long work (exit $1)" \
    "$(basename "$0") exited $1 at line ${fm__failed_line:-?}: ${fm__failed_cmd:-unknown}"
}
```

- The `EXIT` trap does not call `exit`, so bash keeps the original exit status. `fm_page`
  always returns 0 (#504), so the trap cannot change the status.
- The `ERR` trap records the failed command and its line for the page body. Without
  `set -E`, a command that fails inside a function exits the shell before the ERR trap
  fires in the main shell (measured with bash 5 on 2026-10-11: `f() { false; }; f` gave
  no record). `set -E` makes the trap fire inside the function, so the body names the
  inner command (`false`, line 3). A failed command substitution in an assignment
  (`X="$(false)"`) records the assignment in both modes. A missing record gives `unknown`
  and the page is still sent.
- The `EXIT` trap does not run in a `$(...)` subshell that calls `fm_die`: the same
  experiment gave exactly one EXIT line, from the main shell, with status 2.
- The issue plan says "the last stderr lines". This design does not capture stderr.
  A capture needs `exec 2> >(tee ...)`. That process substitution writes asynchronously,
  so the self-test greps of `$T/stderr` can race the exit. The exit code, the line and the
  failed command identify the failure. The operator reads stderr in the terminal log.
- An unpaged `fm_die` after the arm point also pages. This matches the requirement:
  "any non-zero exit it did not page itself".
- A signal to the wrapper itself needs its own trap. When bash dies from SIGHUP, SIGINT
  or SIGTERM, it runs the EXIT trap with `$?` = 0, so the EXIT trap alone sends no page
  (proved on 2026-10-11 with the real `lib.sh`: 0 mail lines for each of the three
  signals). `fm__page_on_signal` pages with the conventional status (129, 130, 143),
  clears the EXIT trap so it cannot page again, and exits with that status. A dropped
  ssh session (HUP) or a systemd or `timeout` kill (TERM) during the QA run then pages.
  An operator who presses Ctrl-C also gets one mail; this is accepted. A child that a
  signal kills (for example, `archi` killed by the OOM killer, status 137) already pages
  through `set -e` and the EXIT trap.

### D3. Arm points

- `qa_arm.sh` sweep mode: directly before the `eval qa run` call (after the `export`
  lines, before line 87). Context `stack $STACK arm $STEM`.
- `qa_arm.sh` arm mode: directly before the `eval qa` call at line 155. Context
  `stack $STACK arm $ARM`.
- `archive_run.sh` sweep mode: directly after the "no artifact" check at line 63.
  Context `stack $STACK sweep`.
- `archive_run.sh` arm mode: directly after the "no artifact" check at line 98. Context
  `stack $STACK arm $ARM`.

Every refusal before these points is a precondition and never pages (#504 spec).

### D4. Arm-mode pin after ledger

The validator stops writing the pin. It still decides the re-pin and refuses as before,
and it still emits `repinned_from` in `ENTRY`. After `fm_ledger_append "$ENTRY"`
succeeds, bash writes the pin when the pin file is absent or `repinned_from` is not null.
The pin value is `ENTRY.corpus_fingerprint`. The write goes to `<pin>.tmp` and then `mv`
to the pin, so a crash never leaves a half-written pin. A Python helper that reads `ENTRY`
for this step must not set `FM_LEDGER` (see D6). This rule gives the same pin
writes as the validator today: no pin → write (run 1 only, the preliminary check refuses
other runs); pin equal → no write; pin different → refused, or `--new-corpus` re-pin →
write.

### D5. Sweep-mode pins after ledger

In `sweep_tools.py archive`, move the `if run == 1:` block (lines 494-496) to after
`os.replace(tmp, ledger)`. The issue names only arm mode, but the sweep path has the same
defect and is inside the issue's allowed scope (`scripts/benchmarking/feature_matrix/`).

### D6. Root-safe ledger failure in the self-test

The self-test archive checks for this change use a new stack, `fm-pin624`. They do not
reuse `fm-tags`: check 74 already archives run 1 on `fm-tags` and writes its pin, so a
reuse refuses as "already archived" and a "no pin" assertion can never pass.


The loop container can run as root, and root ignores `chmod 0500`. Also, a read-only
directory does not stop a write to an existing `ledger.json`. The self-test therefore
fails the ledger write with a `FM_PYTHON` wrapper under `$T/bin`. The wrapper exits 1 when
`FM_LEDGER` and `FM_ENTRY` are both set (only `fm_ledger_append` sets both) and otherwise
runs `exec python3 "$@"`. For qa_arm the same wrapper fails the ledger append after a
clean QA run.

## Risks / Trade-offs

- A failure in the pin write after the ledger row (D4) leaves a row with no pin. The trap
  pages, and a retry is refused as "already archived". This is safer than the current
  order, where the retry silently trusts an unrecorded pin.
- `trap ... EXIT` (and the HUP, INT and TERM traps) replace any earlier trap. Today no
  wrapper and not `lib.sh` sets a trap: `grep -n trap
  scripts/benchmarking/feature_matrix/{lib,qa_arm,archive_run}.sh` is empty on
  `6156b760`. Only the self-test sets traps, in its own shell and in a stub.
- `set -E` (D2) only widens where the ERR trap fires. The trap only sets two variables,
  and ERR never fires in an `if`, `||` or `&&` context, so no exit status changes.
