## Context

`fm_page` (`scripts/benchmarking/feature_matrix/lib.sh:42-53` on `origin/dev` `0ed50a23`):

```bash
local bound=() secs="$FM_MAIL_TIMEOUT"
[[ "$secs" =~ ^([0-9]+\.?[0-9]*|\.[0-9]+)$ && "$secs" =~ [1-9] ]] || secs=60
command -v timeout >/dev/null 2>&1 && bound=(timeout -k 5 "$secs")
printf '%s\n' "$2" | ${bound[@]+"${bound[@]}"} "$FM_MAIL" -s "feature_matrix: $1" "$FM_PAGE_MAIL_TO" \
  || printf 'feature_matrix: page failed\n' >&2
return 0
```

Self-test facts (`test_feature_matrix_wrappers.sh`):
- `$T/bin/mail` appends its args and stdin to `$T/mail.calls` and returns at once.
- `$T/bin/mail-hang` is `exec sleep 30`; `$T/bin/mail-hang-noterm` is `trap '' TERM; exec sleep 30`.
  Both need `env`, `bash` and `sleep` on `PATH` (shebang `#!/usr/bin/env bash`).
- Case 70 runs `fm_page` directly with `PATH="$T/notimeout"` (only `env`, `bash`, `cat`)
  and asserts the page is sent and no `page failed`. It has no `sleep` on `PATH`.

## Goals / Non-Goals

Goals: bound the mail command on a host without `timeout`; keep a page that the mail
command can deliver; leave no process behind; keep `return 0`.

Non-Goals: change the `timeout` path; change `FM_MAIL_TIMEOUT` validation; bound the mail
command when `sleep` is also absent (no external sleep means no portable deadline; the
page is sent unbounded, as today).

## Decisions

### D1. Choose the path once

After the `secs` validation:
1. `timeout` on `PATH` → today's code, unchanged.
2. else `sleep` on `PATH` → watchdog path (D2).
3. else → today's unbounded call (case 70 keeps this).

### D2. Watchdog path

```bash
printf '%s\n' "$2" | "$FM_MAIL" -s "feature_matrix: $1" "$FM_PAGE_MAIL_TO" &
local mpid=$!
( trap 'kill "$spid" 2>/dev/null; exit 0' TERM
  sleep "$secs" & spid=$!; wait "$spid"
  kill -TERM "$mpid" 2>/dev/null
  sleep 5 & spid=$!; wait "$spid"
  kill -KILL "$mpid" 2>/dev/null ) &
local wpid=$!
local rc=0; wait "$mpid" || rc=$?
kill -TERM "$wpid" 2>/dev/null; wait "$wpid" 2>/dev/null
[ "$rc" = 0 ] || printf 'feature_matrix: page failed\n' >&2
return 0
```

- `$!` of a background pipeline is the pid of its last command, the mail command.
- The watchdog runs its `sleep` as a background child and waits on it, so its TERM trap
  runs at once and kills that `sleep`. Without this, a stopped watchdog leaves a `sleep`
  that holds the caller's stderr open (a `$( … 2>&1 )` caller then blocks for the full
  `FM_MAIL_TIMEOUT`).
- `fm_page` stops the watchdog only after `wait "$mpid"` returns, so the watchdog never
  signals a reused pid while fm_page is alive; after the mail pid is reaped, the watchdog
  is already stopped.
- A killed mail command gives `wait` a status above 128 → `page failed`.
- `wait` and `kill` are bash builtins. Use local variable names that do not collide with
  the caller (`local` for all new names).
- `set -e` in the caller: every command that can fail is guarded (`|| rc=$?`,
  `2>/dev/null`, explicit `return 0`). Verify by sourcing `lib.sh` under `set -euo pipefail`
  in the tests.

A prototype of this code on the host (bash, `PATH` with only `env`, `bash`, `cat`,
`sleep`) measured: hang 1 s, hang-noterm 6 s, fast mail 0 s through a pipe capture, rc 0
each time. When the mail command is killed, bash prints a job notice (`Killed …`) on stderr
next to `page failed`. That is acceptable; tests must assert with `grep -q`, not on the full
stderr text.

The exact code is a guide; the spec scenarios are the contract.

### D3. Tests (after case 70, before case 63)

Build a `PATH` dir `$T/notimeout-sleep` with
`env`, `bash`, `cat`, `sleep` (symlinks, like case 70). Call `fm_page` directly via
`bash -c 'set -euo pipefail; source "$1"; fm_page …'`, like case 70, because
`archive_run.sh` needs many more tools than a minimal `PATH` gives.

- 83: `FM_MAIL="$T/bin/mail-hang"`, `FM_MAIL_TIMEOUT=1` → rc 0, `page failed` on stderr,
  elapsed under 15 s.
- 84: `FM_MAIL="$T/bin/mail-hang-noterm"`, `FM_MAIL_TIMEOUT=1` → rc 0, `page failed`,
  elapsed under 15 s (SIGKILL after the 5-second grace).
- 85: `FM_MAIL="$T/bin/mail"`, `FM_MAIL_TIMEOUT=20`, stderr captured through a pipe
  (`out="$(… 2>&1)"`) → rc 0, the page is in `$T/mail.calls`, no `page failed`, elapsed
  under 10 s (a leaked watchdog `sleep 20` would hold the pipe for 20 s).
- 86: `FM_MAIL="$T/bin/mail-fail"` → rc 0 and `page failed`.

Run 83 and 84 before the fix: 83 and 84 fail (about 30 s each, no `page failed`). 85 and
86 are guards and can pass before the fix.

## Risks / Trade-offs

- Timing-based tests: bounds of 15 s and 10 s leave wide margins over 1 s, 6 s and 0 s.
- A mail command that forks a child which keeps running after SIGKILL of the parent is out
  of scope (the `timeout` path has the same limit).
