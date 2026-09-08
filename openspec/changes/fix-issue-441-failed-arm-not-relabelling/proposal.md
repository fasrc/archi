# Stop counting a failed question as a bank relabelling in compare_runs

## Why

`slice_block` (`scripts/benchmarking/compare_runs.py:1355`) reports a per-field counter
`excluded_mismatched` (`compare_runs.py:1425`). Its docstring (`compare_runs.py:1368-1373`)
and the prose the renderer prints from it (`compare_runs.py:1897-1903`) both say one thing:

> `N` question(s) carry a different `difficulty` in different arms and were dropped from
> every slice of that field — the arms were scored against different labels for the same
> question.

The counter is not scoped to that cause. The membership test is
`compare_runs.py:1382-1388`, at `origin/dev` `3170498c`:

```python
value = baseline.rows.get(question, {}).get(field)
if not (isinstance(value, str) and value):
    continue
if any(arm.rows.get(question, {}).get(field) != value for arm in arms):
    mismatched += 1
    continue
groups.setdefault(value, []).append(question)
```

A row for a question that raised carries no `difficulty` key, so `None != "hard"` is true and
the question is counted. The field is not disqualified before that point, because
`Arm.has_metric` (`compare_runs.py:209`) is `any(metric in row for row in self.rows.values())`
— one row missing the field still passes the `all(arm.has_metric(field) for arm in arms)`
guard at `compare_runs.py:1377`.

Measured on this branch (`origin/dev` `3170498c`, 2026-09-07). Two arms, three questions, the
treatment arm's third row written as a failure with no `difficulty` and no `anchor_type` — the
shape every artifact written before PR #440 has:

    -- has_metric on the treatment arm --
      anchor_type: True
      difficulty: True
      is_scorable(failed, faithfulness): False

    -- slice_block output --
      field=anchor_type value=reasoning metric=faithfulness n=2 excluded_mismatched=1
      field=difficulty  value=easy      metric=faithfulness n=2 excluded_mismatched=1

So the report tells its reader that one question was re-labelled in the bank between the two
runs, for **both** slice fields, when no bank edit happened. That reader diffs a bank that
never moved. Under the release plan's benchmark-integrity gate (`v2026.09.0`) this is a
dishonest number, not a cosmetic one.

PR #440 (issue #431, open) fixes the **producer**: `build_failure_entry` carries
`BANK_SLICE_FIELDS`, so newly written artifacts put `difficulty` on a failure row. It cannot
reach artifacts already on disk — the committed `bench_out` files (PR #438 is migrating their
JSON, issue #426) and any run an operator captured before #440 merges. Comparing one of those
against a post-#440 run reproduces the defect exactly. This change is the **consumer** side.

## What Changes

- A named predicate `Arm.has_clean_row(question) -> bool` in `compare_runs.py`, holding the
  status half of the rule `Arm.is_scorable` already encodes (`compare_runs.py:203-207`):
  a row exists and `row.get("status", "ok") == "ok"`. `is_scorable` is rewritten to call it,
  so the two share **one** definition of "failed" rather than gaining a second one.
- One guard in `slice_block`'s membership loop, above the mismatch test. It skips the **arm**
  that did not run the question to completion, rather than the whole question: that arm is
  left out of the comparison and **not** counted as `excluded_mismatched`, because a failed
  row's own `status` says it is not evidence of a bank edit. The skip must be per arm and not
  per question, because a sweep expands into three or more arms and pairing joins the baseline
  with one arm at a time — gating the question on every arm would hide a re-labelling another
  arm genuinely carries and shrink that arm's slice `n` (both measured; see `design.md` D3).
- One per-question rule, for the baseline only: a question whose baseline row did not run to
  completion is dropped from every slice of that field, because the baseline's value is the
  group key.
- The `slice_block` docstring records the membership rule, the per-arm reason, and the
  baseline rule.
- New tests in `tests/unit/test_compare_runs.py`, appended at the end of the file: the
  pre-#440 failure shape, a `degraded` row, the genuine-relabelling case that must still
  count, both `SLICE_FIELDS`, the predicate itself, and two three-arm sweep cases — a
  re-labelling that survives a third arm failing the same question, and a third arm's failure
  not shrinking another arm's slice.
- A paragraph in `docs/docs/interpreting_benchmark_results.md` §3.2 stating the
  `excluded_mismatched` contract for the operator who reads the report: what the counter
  counts, that an arm which did not run the question is skipped rather than counted, that the
  skip is per arm, and the separate baseline rule. `AGENTS.md:54` asks for the docs update in
  the same change as the user-visible behaviour. §3.2 is ~350 lines from the two regions open
  PR #440 edits, so the two changes do not conflict.

Genuine relabelling is untouched: a question that **is** a clean success in both arms and
carries different values still increments the counter. The existing test
`test_a_slice_drops_questions_whose_field_value_disagrees_between_arms`
(`tests/unit/test_compare_runs.py:1572`) is the guard for that and passes unchanged.

Not in this change, each with a reason in `design.md`:

- `Arm.has_metric` keeps `any(...)`. Switching it to `all(...)` would disqualify the whole
  field the moment one row failed, deleting the honest slices with the false counter
  (measured: `('easy', n=2)` and `('hard', n=1)` both vanish).
- `anchor_block`'s own inline status test (`compare_runs.py:1310`) stays as it is.
- No producer-side change. `src/utils/benchmark_resilience.py` is #440's file.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `retrieval-benchmarking`: one requirement is ADDED. `openspec/specs/retrieval-benchmarking/spec.md`
  has four requirements and none of them mentions `compare_runs`, slice membership, or
  `excluded_mismatched`, so no existing requirement text is contradicted. Issue #431's
  requirement (change `fix-issue-431-propagate-bank-difficulty`, open as PR #440, not merged
  and not archived) is not in `openspec/specs/` yet, so this delta uses ADDED and never
  MODIFIED.

## Impact

- `scripts/benchmarking/compare_runs.py` — one new method, one rewritten method body, one
  guard, one docstring paragraph.
- `tests/unit/test_compare_runs.py` — tests appended; no existing test changed. The file has
  83 tests today and is 1894 lines.
- `docs/docs/interpreting_benchmark_results.md` — one paragraph and a four-item list added to
  §3.2. Prose only; no example, procedure, or number elsewhere on the page changes.
- **In a two-arm comparison the fix changes exactly one number.** Measured across five arm
  shapes on 2026-09-07 by loading the current file and a patched copy side by side and
  comparing `slice_block` output field by field: every key except `excluded_mismatched` is
  identical in all five, including every slice's `value`, `n`, `mean`, `se`, `verdict`, and
  `directional`.

  | shape | `excluded_mismatched` before → after | `difficulty` slices before → after |
  |---|---|---|
  | pre-#440: failure row has no `difficulty` | 1 → **0** | `(easy,2) (hard,1)` → unchanged |
  | post-#440: failure row carries `difficulty` | 0 → 0 | `(easy,2)` → unchanged |
  | genuine relabelling, clean in both arms | 1 → **1** | `(easy,1)` → unchanged |
  | `status: degraded` row | 1 → **0** | `(easy,2)` → unchanged |
  | the baseline arm is the one that failed | 0 → 0 | `(easy,2)` → unchanged |

  In a two-arm comparison the slice numbers cannot move, because `paired_deltas`
  (`compare_runs.py:594-605`) already requires both arms scorable and `summarize_deltas` drops
  an empty group at `compare_runs.py:1399`.

  **That argument does not extend to a sweep**, and the first version of this change assumed
  it did. With three or more arms, pairing joins the baseline with one arm at a time, so
  dropping the whole question does move another arm's slice `n`. That is why the shipped
  guard filters per arm; the two three-arm tests in `d8155543` hold the line.
- **One file is now shared with an open PR, and it does not conflict.** Checked by file list
  and by hunk range on 2026-09-08: PR #440 (head `239f1bbb`) edits
  `docs/docs/interpreting_benchmark_results.md` in two hunks, `:575-588` and `:683-688`; this
  change inserts after `:227`, about 348 lines above the nearer of the two, so the hunks share
  no line and no context. PR #440 otherwise touches `src/utils/benchmark_resilience.py`,
  `src/bin/service_benchmark.py`, `tests/unit/test_benchmark_resilience.py`,
  `docs/docs/benchmarking.md`, and its own `openspec/changes/` directory. PR #438 (head
  `63786023`) touches `bench_out/**`, `tests/unit/test_bench_out_artifacts.py`, and its own
  `openspec/changes/` directory. Neither touches `scripts/benchmarking/compare_runs.py` or
  `tests/unit/test_compare_runs.py`.
- **Patch coverage does not protect this change.** The gate measures `--cov=src`
  (`scripts/gate.sh`), and neither changed path is under `src/`, so `diff-cover` reports no
  lines with coverage information and the 80% bar passes on an empty measurement. The named
  tests are the only protection; `tasks.md` requires running them by name and reading the
  count.
- Both changed Python files are black 24.10.0 and isort 6.0.1 clean today (checked
  2026-09-07), and `scripts/*.py` is inside the gate's enforced format scope. The docs page is
  markdown and the gate does not lint it.
- No behaviour change for any artifact whose failure rows carry the bank fields, so a fully
  post-#440 pair of runs compares byte-identically before and after this change.
