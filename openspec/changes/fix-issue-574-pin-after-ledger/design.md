## Context

The feature-matrix ledger (`$FM_OUT/ledger.json`) is the record that authorizes each corpus
pin (`$FM_OUT/corpus-pin-<stack>`). A pin with no ledger row is an unrecorded pin. Two
writers move pins:

1. Arm mode: the `ENTRY="$(... "$FM_PYTHON" - <<'EOF' ...)"` validator in `archive_run.sh`
   (lines 156-261 at `6156b760`). It writes the pin at lines 231 (`--new-corpus` re-pin) and
   233 (first pin), then prints the entry JSON. The shell calls `fm_ledger_append "$ENTRY"`
   at line 267. `fm_ledger_append` (`lib.sh:359`) rewrites the ledger in place.
2. Sweep mode: `sweep_tools.py` `archive()` writes `corpus_pin` and `map_pin` on run 1
   (lines 494-496), then writes the ledger through a temp file and `os.replace` (lines
   497-500).

## Goals / Non-Goals

**Goals:**
- In both modes, a pin file changes only after the ledger write that records it succeeded.
- A pin write is atomic: a reader sees the old value or the new value, never a partial one.

**Non-Goals:**
- Paging on a crash after long work (#624 owns it).
- Making `fm_ledger_append` itself atomic.
- A rollback of the ledger row when the pin write fails after it. A ledger row with no
  pin is safe: the next run fails the pin check loudly ("archive run 1 first" or a
  mismatch); it does not pass silently.

## Decisions

### Arm mode: the validator reports the pin, the shell writes it

The validator keeps every check and decision it has now. In place of the two
`open(pin_file, "w").write(fp + "\n")` calls it sets `new_pin = fp` (and leaves
`new_pin = None` when the pin stays). It prints the entry JSON on line 1 and the new pin
(empty string when none) on line 2. The shell splits the captured output, for example:

```bash
ENTRY="${FM_VALIDATED%%$'\n'*}"; NEW_PIN="${FM_VALIDATED#*$'\n'}"
```

Any equivalent split is fine. Do not add a field to the ledger row; the row schema stays
the same. After `fm_ledger_append "$ENTRY"` returns 0:

```bash
if [ -n "$NEW_PIN" ]; then
  printf '%s\n' "$NEW_PIN" > "$PIN_FILE.tmp.$$" && mv -f "$PIN_FILE.tmp.$$" "$PIN_FILE"
fi
```

The script runs under `set -euo pipefail`, so a failed `fm_ledger_append` exits before the
pin write. The existing refusal path (`|| { FM_RC=$?; ... fm_page ...; exit "$FM_RC"; }`)
does not change. The summary printer after the append reads `FM_ENTRY="$ENTRY"`; it must
still get only the JSON line.

Alternative considered: write a temp pin from Python and `mv` it in bash. Rejected: a
failed append leaves a stray temp file, and the "does the pin move" decision is split
between two places.

### Sweep mode: move the two pin writes after `os.replace`

In `archive()`, move the `if run == 1:` block below `os.replace(tmp, ledger)`. Write each
pin through a sibling temp file and `os.replace`, the same idiom the ledger already uses.

### Forcing a ledger-append failure in the wrapper self-test

Do not use `chmod` on the ledger: the loop container can run as root, and root ignores the
mode bits. Use an `FM_PYTHON` shim for the two new cases: a script under `$T/bin` that
reads its stdin into a temp file, exits 1 if the script text contains `rows.append(entry)`
(only the `fm_ledger_append` heredoc has that text), and otherwise runs the real python on
the saved script with the same arguments. When the shim is called with a script path in
place of `-` (for example `fm_sweep_tools`), it passes the call straight to the real python.
Restore `FM_PYTHON` after the cases. Confirm with `grep` that `rows.append(entry)` does not
occur in the arm-mode validator heredoc.

## Risks / Trade-offs

- [The pin write fails after the ledger row lands] → the stack has a recorded row with no
  pin. The next run refuses loudly. This is the safe failure direction.
- [The output split breaks if the entry JSON has a newline] → `json.dumps` with no
  `indent` never emits a raw newline. Keep `print(json.dumps(entry))`.
