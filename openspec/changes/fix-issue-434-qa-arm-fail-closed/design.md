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
(Since #621 the drift check calls `fm_die_paged`, at `qa_arm.sh:92` and `:171` after the
2026-10-23 merge; the append is the next line in each path.) After the change, `grep -c fm_require_scored scripts/benchmarking/feature_matrix/qa_arm.sh`
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

The highest check number in the file is 89 (measured after the 2026-10-23 merge of
`origin/dev` `e2978a1d`; the plan first said 60–64, which other changes have since used).
Add the checks after the check 82 block, which is the last block in the file, before the
final `printf ... passed`. Number them 90–94 and list them in the header contract block,
after its last line (`86.`). Each check prints exactly one `ok` or `not ok` line, so the
self-test goes from `96 passed` to `101 passed`. Every refusal check asserts all three of:
`RC = 2`, the expected text in `$T/stderr`, and `ledger_rows` unchanged from a
`BEFORE="$(ledger_rows)"` taken just before the run.

- 90 single-arm, `scored: 0` → refused; stderr contains `summary.json` and `NOT recorded`.
- 91 single-arm, `$T/qa-no-summary` → refused; stderr contains `summary.json` and `NOT recorded`.
- 92 single-arm, `$T/qa-summary` holds `not json{` → refused; stderr contains `summary.json`
  and `NOT recorded`.
- 93 single-arm, `scored: 3` with `execution_failed: 2` → `RC = 0` and `ledger_rows` is
  `BEFORE + 1`.
- 94 sweep, `scored: 0` → refused with no ledger row; then the same sweep arm with the
  default summary and the next `--run` → `RC = 0` and one more row.

Use the invocation shapes that the existing checks use. For single-arm, copy check 58:
`run env FM_AGENT_SPEC="$T/cfg/spec.md" bash "$HERE/qa_arm.sh" 00 "$T/arms/00-baseline.yaml" --profile "$T/cfg/qa/profile.yaml"`
(the run number auto-advances). For sweep, copy check 52:
`run bash "$HERE/qa_arm.sh" --sweep "$SW/configs" --stack r0 --arm fasrc-docs-r0b-icl --run 7`
for the refused run, and `--run 8` for the recorded run (a refused run keeps its output
directory, so do not reuse its number). On 2026-10-23 Loop 1 ran both shapes at that
position in a scratch copy of the file: each exited 0 and added one ledger row. If a
check's stack state is not right (pin, arm, lock), read the checks just above it and
reuse their setup; do not change any earlier check.

### D5 — The header comment

The header contract block lists the checks. Its lines for 57–59 are out of order: the
continuation line of 58 ("row, and writes nulls when the manifest recorded none") sits
below 59. Move that line back under 58 when you add 90–94. Do not add, renumber, or
reword the header lines of any other check (87–89 have no header line; that is out of
scope).

### D6 — The refusal does not page

`fm_require_scored` calls `fm_die`, not `fm_die_paged`, even though the drift check just
above it pages. The #504 paging spec
(`openspec/changes/fix-issue-504-fm-page-mail/specs/feature-matrix-operator-paging/spec.md`)
says `qa_arm.sh` pages at the two "corpus changed during the QA run" exits, and that
`fm_require_*` checks never page. Paging on other failures after long work is the scope of
#624, which arms an `EXIT` trap after long work starts; that trap will page this refusal
too, once and with no change here. A reviewer who asks for `fm_die_paged` gets this answer.

## Risks

- A real `summary.json` with a different shape (for example an older writer) is refused.
  That is the fail-closed intent; the operator sees the path and the value in the message.
- Merging changes the campaign's code sha; a running campaign needs `--relock`.
