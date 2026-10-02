## Context

`qa_arm.sh` has two ledger paths. The sweep path (`--sweep`, added by #551) runs
`archi eval qa run <dir>` then `archi eval qa score <dir>` on a copy of a prepared
workspace. The single-arm path runs one `archi eval qa ... --output-dir <dir>`. Both end
with the same three steps: corpus-drift check, `fm_ledger_append`, `fm_log "done; ..."`.

`summary.json` is written only by the scoring phase (`src/evaluation/qa/workflow.py:879`).
`qa_prepare.sh` runs `archi eval qa prepare`, which writes no `summary.json`, so a sweep
copy of a prepared workspace does not carry a stale one.

## Decisions

### D1 — One helper in `lib.sh`, `fm_require_scored <out_dir>`

Put it next to the other ledger helpers in `lib.sh` (near `fm_ledger_append`). Use the
same mechanism the other helpers use: `$FM_PYTHON` with the path passed through the
environment (not interpolated into the program text). The Python program prints one
reason word and the shell maps it to a message:

| Condition | Result |
|---|---|
| `<out_dir>/summary.json` does not exist | `fm_die "no <path>; output kept at <out_dir> but NOT recorded — the run is void"` |
| file cannot be opened or is not valid JSON, or the top level is not an object | `fm_die "<path> is not readable JSON; output kept at <out_dir> but NOT recorded — the run is void"` |
| `attempt_lifecycle_counts` is not an object, or `scored` is absent, `null`, a bool, not an `int`, or `<= 0` | `fm_die "QA run scored nothing (<path> attempt_lifecycle_counts.scored = <value>); output kept at <out_dir> but NOT recorded — the run is void"` |
| `scored` is an `int` `>= 1` | return 0, print nothing |

`<path>` is the full `summary.json` path, so every refusal names `summary.json` and every
refusal contains the text `NOT recorded`. The wording copies the drift check's
"output kept at $OUT_DIR but NOT recorded — the run is void".

A bool is refused because JSON `true` loads as Python `True`, which is an `int` subclass
equal to 1. A float such as `3.0` is refused: the writer emits integer counts, and any
other type means the file is not the scorer's output.

The helper must not depend on `set -e` to stop: call `fm_die` explicitly on every
refusal branch. If `$FM_PYTHON` itself fails to start, treat that as unreadable (refuse).

### D2 — Call site

In both paths, insert `fm_require_scored "$OUT_DIR"` on the line immediately after the
corpus-drift check and before `fm_ledger_append`. Do not move or reword the drift check.
After the change, `grep -c fm_require_scored scripts/benchmarking/feature_matrix/qa_arm.sh`
prints `2`.

### D3 — Test seam: the stub `archi` writes `summary.json`

The stub `archi` in `test_feature_matrix_wrappers.sh` (heredoc at about `:98-107`) writes
no `summary.json` today. Without a change, every existing passing `qa_arm` check turns red
when D2 lands. Extend the stub so that it writes `<dir>/summary.json` after a scoring call:

- the single-arm call `eval qa ... --output-dir <dir>` (the stub already finds `<dir>` by
  the `--output-dir` loop), and
- the sweep call `eval qa score <dir> ...` (`<dir>` is `$4`).

Do NOT write it on `eval qa run <dir>` or `eval qa prepare`.

Content is chosen by two control files, in this order:

1. `$T/qa-no-summary` exists → write nothing (the "missing" case).
2. `$T/qa-summary` exists → copy it verbatim to `<dir>/summary.json` (crafted cases,
   including non-JSON text).
3. Otherwise → write the default
   `{"attempt_lifecycle_counts": {"scored": 3, "execution_failed": 0, "evaluation_failed": 0}}`.

Each new check creates its control file before the run and removes it after, so later
checks see the default again.

### D4 — New checks

Add the checks after check 59, before the final `printf ... passed`. Number them 60–64 and
list them in the header contract block. Every refusal check asserts all three of:
`RC = 2`, the expected text in `$T/stderr`, and `ledger_rows` unchanged from a
`BEFORE="$(ledger_rows)"` taken just before the run.

- 60 single-arm, `scored: 0` → refused; stderr contains `summary.json` and `NOT recorded`.
- 61 single-arm, `$T/qa-no-summary` → refused; stderr contains `summary.json` and `NOT recorded`.
- 62 single-arm, `$T/qa-summary` holds `not json{` → refused; stderr contains `summary.json`
  and `NOT recorded`.
- 63 single-arm, `scored: 3` with `execution_failed: 2` → `RC = 0` and `ledger_rows` is
  `BEFORE + 1`.
- 64 sweep, `scored: 0` → refused with no ledger row; then the same sweep arm with the
  default summary and the next `--run` → `RC = 0` and one more row.

Use the invocation shapes that the existing checks use. For single-arm, copy check 58:
`run env FM_AGENT_SPEC="$T/cfg/spec.md" bash "$HERE/qa_arm.sh" 00 "$T/arms/00-baseline.yaml" --profile "$T/cfg/qa/profile.yaml"`
(the run number auto-advances). For sweep, copy check 52 and pass `--run N` with a number
not used before. If a check's stack state is not right (pin, arm, lock), read the checks
just above it and reuse their setup; do not change any earlier check.

### D5 — The header comment

The header contract block lists the checks. Its last lines are out of order: the
continuation line of 58 ("row, and writes nulls when the manifest recorded none") sits
below 59. Move that line back under 58 when you add 60–64.

## Risks

- A real `summary.json` with a different shape (for example an older writer) is refused.
  That is the fail-closed intent; the operator sees the path and the value in the message.
- Merging changes the campaign's code sha; a running campaign needs `--relock`.
