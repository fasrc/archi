## Context

The `drift` subcommand (PR #144, group 4 of `maintain-ragas-goldenset`) re-fetches every `locked` golden-set row's sources, hashes the extracted text, and compares against the row's stored `source_hashes`. It never writes the bank file: `--print-hashes` emits a paste-ready block and a human pastes it in. That is the only path by which a baseline is ever recorded.

The `find_drift` function in `src/utils/goldenset_maintenance.py` (~:1572) skips any row where `row_status(record) != "locked"`, so a `draft` row's sources are never fetched or hashed. `_print_hashes` in `scripts/benchmarking/goldenset_maintenance.py` (~:855) iterates `report.rows`, which by the gate above contains only locked rows. When nothing is emitted, the CLI (~:898-903) instructs the operator to set `status: locked` and re-run — teaching the unsafe order.

Consequence: to baseline a row an operator must lock it *first*, creating a window where the row is authoritative but unbaselined. All 105 rows in the live bank are `draft`, so confirming the bank means 105 such windows.

**Load-bearing constraint** (task 4.4 of `maintain-ragas-goldenset`; design D3): the tool MUST NOT write the bank file. Any design where the tool edits `status` or `source_hashes` is out of scope. So the fix cannot be an atomic "lock" write operation — it must instead make the *hash* available earlier so the human's single paste sets both fields at once.

## Goals / Non-Goals

**Goals:**
- Let an operator obtain a `draft` row's `source_hashes` block before locking, via the existing read-only `--print-hashes` path.
- Keep the "draft rows are never drift-checked" invariant intact and make it structural, not a scatter of conditionals.
- Preserve the read-only guarantee: the bank file is byte-unchanged by any `drift` run.
- Ship the docs update in the same change; every documented `drift` example carries `--allowed-hosts`.

**Non-Goals:**
- The tool writing the bank file, or any auto-lock operation (explicitly rejected).
- Changing fetch behavior, hashing, or drift comparison for `locked` rows.
- Changing how a baseline is actually recorded (still a human paste).

## Decisions

### D1: Opt-in flag, default-off
Add a boolean parameter to `find_drift` (e.g. `baseline_drafts`, default `False`) and a matching CLI flag (`--baseline-drafts`, intended for use with `--print-hashes`). Default-off means the existing drift path — and every existing test and invocation — is byte-for-byte unaffected. *Alternative considered:* always hash drafts and let the printer filter. Rejected: it does wasted fetches on the common drift-check path and blurs the invariant.

### D2: Baseline-only rows on a separate report field (structural invariant)
Carry baselined draft rows in a **dedicated field** on `DriftReport` (e.g. `baseline_only: list[...]`), NOT mixed into `report.rows`/`drifted`/`unbaselined`. This is the crux of the change: because the aggregates (`drifted`, `unbaselined`, `checked_rows`, abstention) are computed only from the drift-checked rows, a draft row that only ever lands in `baseline_only` is *structurally* incapable of appearing in a drift outcome or triggering an LLM call. The invariant holds by construction rather than by a conditional a later edit could forget. *Alternative considered:* a `is_baseline_only` flag on each row plus guards at every aggregation site. Rejected: it re-introduces exactly the "conditional a later change drifts from" risk the issue calls out.

### D3: Printer reads the new field
`_print_hashes` emits blocks for `report.rows` (locked) as today, and additionally for `report.baseline_only` when present. The emitted block format is identical, so the paste target is unchanged.

### D4: Replace the lock-first guidance
The "set `status: locked` on the row, then re-run this to get its block" message (~:898-903) is replaced with guidance pointing at `--baseline-drafts` and the single-edit workflow (compute the hash, then paste `status: locked` + `source_hashes` together). Acceptance grep `set .status: locked. on the row, then re-run` must return nothing.

### D5: Docs
Update the "Recording a baseline" section of `docs/docs/benchmarking.md` to the single-edit workflow, and audit every `drift` example in that file for the required `--allowed-hosts` flag (this drift has bitten the file before).

## Risks / Trade-offs

- **Risk:** a draft row leaks into a drift aggregate → **Mitigation:** D2 makes that structurally impossible; tests assert absence from `drifted`, `unbaselined`, `checked_rows`, LLM calls, and abstention.
- **Risk:** the tool accidentally mutates the bank → **Mitigation:** a CLI test asserts the bank file is byte-identical before/after a `--print-hashes` run that includes drafts.
- **Risk:** default drift behavior changes → **Mitigation:** flag defaults off; the existing suite must stay green with no edits to its expectations.
- **Trade-off:** a new report field widens the `DriftReport` surface slightly; justified because it is what makes the invariant structural.

## Migration Plan

Pure additive code + docs change. No data migration, no config change, no deploy dependency. Rollback = revert the branch. The change is verifiable entirely by the local gate (`scripts/gate.sh`) and `mkdocs build --strict`.

## Open Questions

- Exact parameter/flag name (`baseline_drafts` / `--baseline-drafts`) and report field name (`baseline_only`) are suggestions; the implementer may pick clearer names as long as the spec scenarios and acceptance greps hold.
