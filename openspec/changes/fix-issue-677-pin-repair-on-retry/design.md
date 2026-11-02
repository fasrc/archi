## Context

The feature-matrix ledger (`$FM_OUT/ledger.json`) authorizes each corpus pin. Since #574 (PR
#661) both writers write the ledger first and the pin second:

1. Arm mode, `scripts/benchmarking/feature_matrix/archive_run.sh` on `origin/dev`
   `a18b979c`. The first validator heredoc (`:110-150`) refuses duplicates: the same artifact
   path (`:115-117`) and the same `(arm, stack, run)` (`:118-120`). The second validator
   (`:163-292`) builds the row (`entry = {...}` at `:266`) and prints the new pin (empty when
   the pin does not move). Then `fm_ledger_append "$ENTRY"` (`:301`) and the pin write
   (`:308-309`: temp file, then `os.replace`). The validator sets a new pin in two cases only:
   no pin yet (run 1, `repinned_from: null`), and a `--new-corpus` re-pin (arm 00 after a
   fresh deploy, `repinned_from: <old pin>`).
2. Sweep mode, `scripts/benchmarking/feature_matrix/sweep_tools.py` `archive()` (`:323`). The
   duplicate checks are by `artifact_sha256` (`:380-381`) and by `(stack, run)` over the
   locked arms (`:382-393`). On run 1 the rows carry `census_map_digest`. After
   `os.replace(tmp, ledger)` (`:526`) it writes `corpus-pin-<stack>` (value: the rows'
   `corpus_fingerprint`) and then `category-map-pin-<stack>` (value: `census_map_digest`),
   each through `<pin>.tmp` and `os.replace` (`:528-532`). Later runs write no pins.

An invariant follows. After every successful arm-mode archive, the stack's pin equals the
`corpus_fingerprint` of the stack's newest `kind: ragas` row. After a successful sweep run 1,
the two pins equal that run's `corpus_fingerprint` and `census_map_digest`.

## Goals / Non-Goals

**Goals:**
- A retry of an archive whose ledger write succeeded and whose pin write failed finishes the
  pin write and exits 0, in arm mode and in sweep mode (both pins).
- Every other duplicate is refused exactly as today, with the same message.

**Non-Goals:**
- Repairing rows written before this change in arm mode (they have no `artifact_sha256`).
- A pin that someone changed by hand. The repair never overwrites a pin that a failed write
  did not leave.
- Rolling back a ledger row. Paging changes (#624 owns paging).

## Decisions

### D1. Arm-mode rows record `artifact_sha256`

The decision matches a row by "same path and `artifact_sha256`". Arm-mode rows record only
the path today, so the second validator adds one key to `entry`, next to `"artifact": p`:
`"artifact_sha256": hashlib.sha256(open(p, "rb").read()).hexdigest()` (bare hex, the same
format as the sweep-mode key, which comes from `sweep_tools._sha`). A row with no key never
matches, so a row written before this change keeps today's refusal.

The #574 delta (`openspec/changes/fix-issue-574-pin-after-ledger/specs/feature-matrix-corpus-pin/spec.md`)
says "The ledger row schema SHALL not change." That sentence describes the #574 fix. This
change scopes it to that fix, so the two deltas do not disagree when they are archived.

### D2. What "the pin that row implies" means: the state a failed write leaves

The repair runs only when ALL of these are true:

1. The stack's newest `kind: ragas` row records this artifact: the same `artifact` path, the
   same `artifact_sha256` (computed again from the file now), the same `arm` and `run`.
   "Newest" is by ledger position. Any later ragas row on the stack means that a later
   archive could have checked or moved the pin, so the old row no longer implies it.
2. The pin path is not a directory.
3. The pin is in the state that a failed write after that row leaves:
   - the row has `repinned_from: null` (it set the first pin): the pin file is absent;
   - the row has `repinned_from: <old>` (a `--new-corpus` re-pin): the pin holds `<old>`,
     and the retry passes `--new-corpus`.

Then the validator writes the row's `corpus_fingerprint` to the pin (temp file
`<pin>.tmp.<pid>`, then `os.replace`; the temp file is removed if the write fails). It prints
`repaired the corpus pin for <stack>: <fingerprint> (arm <arm> run <run> was already in the
ledger; its pin write had failed)` to its stderr file and exits 3. Any other state falls
through to the existing refusals.

Why not "repair whenever the pin differs from the row"? It overwrites any pin that differs,
including a hand-written one. The fixture has this case: check 59 re-pins `fm-00` to
`sha256/v2:def` with `--new-corpus`; line 818 writes `sha256:def` back by hand; check 60 then
re-archives the same artifact (`00 11`, no flag) and expects a refusal. With the plain
reading, check 60 gets exit 0 and the pin moves (measured: `108 passed, 1 failed`). Rule 3
keeps the existing rule that only an archive with `--new-corpus` moves an existing pin. The
operator retries with the same command, so a re-pin retry carries `--new-corpus` anyway.

Rule 3 also covers the complete run: its pin equals the row's fingerprint, which is neither
"absent" nor the different `repinned_from` value, so it is refused (the decision's "a second
archive of a complete run must still be refused").

### D3. Arm mode: where the repair runs, and how the shell sees it

The repair runs at the top of the first validator heredoc, before `dup = [...]`. The heredoc
gets one more variable, `FM_NEW_CORPUS="$NEW_CORPUS"`. The exit code tells the shell what
happened: 0 = continue, 3 = repaired, any other = refuse. The `if ! ...; then` wrapper
becomes `... <<'EOF' || FM_RC=$?` with `FM_RC=0` set first, then:

- `FM_RC=3`: `fm_log "$(cat "$FM_ERRF")"`, remove the file, `exit 0`. No ledger append and
  no `archived arm` line. The live document and chunk counts are not read, because the stack
  can already be gone.
- any other non-zero code: the existing `cat` + `fm_die_paged` block, unchanged.

The repair runs before the age and lock checks of the same heredoc: the row passed those
checks when it was written, and a later `--relock` must not block the pin of a row that is
already durable.

### D4. Sweep mode: `_finish_pins()` at the duplicate check

New private helper `_finish_pins(rows, *, stack, run, archived, digest, pins_dir)`, placed
before `archive()`. It returns `(rows of this run, pins written)`. The rule is D2 adapted to
sweep mode:

1. `mine` = ragas rows for the stack with this `run`, `artifact == str(Path(dest) /
   artifact.name)` (the archived path that the rows record) and this `artifact_sha256`. The
   stack's newest ragas row must be `mine[-1]` (the run's rows are written in one ledger
   write, so they are the newest rows together).
2. The values come from `mine[-1]`: `corpus_fingerprint` for `corpus-pin-<stack>`,
   `census_map_digest` for `category-map-pin-<stack>`. If either value is missing, there is
   no repair. Only run-1 rows carry `census_map_digest`, so this rule also limits the repair
   to run 1, the only run that writes pins.
3. A pin that exists must be a file that already holds its value. Otherwise there is no
   repair: a failed write leaves each pin absent or new, never a third value.
4. Each absent pin is written in the same order as `archive()` writes them, through the same
   `<pin>.tmp` + `os.replace`.

In `archive()`, the existing `if any(row.get("artifact_sha256") == digest ...)` branch calls
the helper. If no pin was written, it raises the same `SweepError(f"{artifact} is already
archived")`. Otherwise it prints `repaired <pin names> for run <run> on <stack>; its ledger
rows were already written` to stdout and returns `mine`. The CLI then prints its usual
`archived run <run>: <n> arm rows`, which is still true. `archive()` keeps its signature
and return type.

A retry whose artifact bytes changed has another digest. It does not enter this branch, so
the `(stack, run)` check refuses it as today.

### D5. Test plan (staged in scratch copies before the handoff)

The self-test and the unit tests were run red and green in scratch copies of `scripts/` and
`tests/unit/` (no repository writes). The exact bodies are in the tasks.md Appendix.

| Stage | Self-test (`test_feature_matrix_wrappers.sh`) | `pytest tests/unit/test_sweep_tools.py` |
|---|---|---|
| before any change | `104 passed, 0 failed` | `40 passed` |
| tests only (red) | `109 passed, 3 failed` (113, 114, 120) | `2 failed, 44 passed` (both retry params) |
| tests + code (green) | `112 passed, 0 failed` | `46 passed` |

Mutation checks on the green code. Each one turned its target check red: no sha check (115
red), match any row and not the newest (116 red), repair any pin state (118, 119 and the
existing 60-62 red), no `--new-corpus` condition (119 red), sweep newest-row guard removed
(`later-row` red), sweep value guard removed (`no-map-digest` red), sweep present-pin guard
removed (`other-corpus-pin` red).

Black 24.10.0 and isort 6.0.1 report both Python files clean. `bash -n` passes on both shell
files.

## Risks / Trade-offs

- [The age, lock and judge checks do not run on a repair] → They ran when the row was
  written; the repair writes only the value that row recorded.
- [A pin that a human changed after a failed write is not repaired] → On purpose. The retry
  is refused, as today, and the operator decides.
- [Rows from before this change cannot be repaired in arm mode] → The failure window is one
  small file write. Those rows keep today's behavior (a hand-written pin).
- [Merge with open PR #782] → It adds self-test cases 98-112 to the same file. This change
  uses 113-120, appended above the final summary line, so a rebase keeps both blocks.
