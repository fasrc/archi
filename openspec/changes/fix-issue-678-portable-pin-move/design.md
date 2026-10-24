## Context

PR #661 (issue #574) made the arm-mode archive write the corpus pin only after the ledger
row. Its review round added two guards against a directory at the pin path:

- `archive_run.sh:213-214`: the validator refuses a directory pin path by name
  (`REFUSED: the corpus pin path ... is a directory`) before `fm_ledger_append`.
  Self-test 81 covers it.
- `archive_run.sh:286`: `mv -fT` refuses a directory that appears after the append
  (a plain `mv -f` moves the pin inside the directory and exits 0). Self-test 82 covers it
  with an `FM_PYTHON` shim that creates the directory after the ledger append.

The run hit its review-round limit with two open findings (issue #678).

## Goals / Non-Goals

**Goals:**
- Remove the GNU-only `mv -T` from the pin move, with the same directory refusal.
- Make self-test 81 fail when a refusal happens after the ledger append.

**Non-Goals:**
- Remove other GNU coreutils uses (`sha256sum` in `lib.sh:166`). The issue scopes only the
  pin move.
- Change sweep mode. It already uses `os.replace` (`sweep_tools.py:503`).
- Clean up the temp file when the move fails. Self-test 82 already removes it in its
  teardown; the issue does not ask for it.

## Decisions

### Move the pin with `os.replace` through `$FM_PYTHON`

Replace

```bash
mv -fT "$PIN_FILE.tmp.$$" "$PIN_FILE"
```

with

```bash
"$FM_PYTHON" -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$PIN_FILE.tmp.$$" "$PIN_FILE"
```

and change the comment above it to say why: `os.replace` is an atomic rename on POSIX and
raises `IsADirectoryError` when the target is a directory, where a plain `mv -f` moves the
pin inside the directory and exits 0. `set -e` makes the failed call fail the archive
before `fm_log "archived arm ..."`.

Why `-c` and not a `- <<'EOF'` heredoc: the shims in self-tests 80 and 82 wrap only the
`-` (stdin) form and `exec` the real interpreter for any other arguments. The `-c` form
passes through them unchanged, so the shims keep their meaning.

Alternative rejected: keep `mv -fT` and state the GNU coreutils requirement in the header
(Option B). The issue recommends Option A because it removes the platform question at the
same line count, and sweep mode already uses `os.replace`.

### Self-test 81 checks the ledger

Before the `archive_run.sh` call, read the ledger row count:

```bash
ROWS_BEFORE="$("$FM_PYTHON" -c "import json; print(len(json.load(open('$FM_OUT/ledger.json'))))")"
```

After the call, read it again into `ROWS_AFTER` and require equality. `run_arm.sh` writes
the stack's `ragas-start` row before this point, so `ledger.json` exists. Also require
that no `$FM_OUT/corpus-pin-fm-pin81.tmp.*` file exists (for example
`! compgen -G "$FM_OUT/corpus-pin-fm-pin81.tmp.*" >/dev/null`).

### Proof that the assertions can fail (not committed)

- Self-test 81: temporarily delete the `os.path.isdir` refusal from the validator and add a
  shell refusal with the same message after `fm_ledger_append`:
  `if [ -d "$PIN_FILE" ]; then echo "REFUSED: the corpus pin path $PIN_FILE is a directory" >&2; exit 2; fi`.
  The old assertions of 81 still pass; the new ledger-count assertion fails. Restore
  `archive_run.sh` exactly before the commit.
- Self-test 82: temporarily replace the `os.replace` line with
  `mv -f "$PIN_FILE.tmp.$$" "$PIN_FILE"` and watch 82 fail. Restore.

Record both observations in the commit message bodies (and later in the PR body).

## Risks / Trade-offs

- [`$FM_PYTHON` is required for the move] → It is already required: the validator and the
  ledger append run through it on the same path.
- [A failed move leaves `corpus-pin-<stack>.tmp.<pid>`] → Unchanged from today's `mv -fT`
  behaviour; out of scope.
