# Migrate the committed benchmark artifacts to strict JSON with honest scored counts

## Why

PR #421 (`fix(#279)`, merged `dc6772a2`) taught the harness to WRITE valid JSON. `json_safe`
(`src/utils/benchmark_schema.py:603`) copies every non-finite float to `null`, `ResultHandler.dump`
(`src/bin/service_benchmark.py:555`) dumps with `allow_nan=False`, and
`score_metrics_per_eligibility` (`src/utils/benchmark_schema.py:594`) counts only the finite scores
that actually reached an aggregate. None of that reached the artifacts already committed under
`bench_out/`. This change is the data half of #279 that #421 deliberately left out.

Measured on `origin/dev` @ `3170498c` (2026-09-05):

- **10 of the 18** committed JSON artifacts carry a bare `NaN`. `NaN` is not JSON (RFC 8259).
  `compare_runs.py` (#419, merged `3170498c`), `jq`, a browser's `JSON.parse`, and any
  `json.loads` with a raising `parse_constant` all refuse to open those files. The repository's
  own loaders tolerate both spellings, so this migration changes bytes, not readers.
- **5 `<metric>_scored` strings across 4 artifacts overstate the scored count.** Each one claims
  every scorable row was scored while some rows hold a non-finite cell that never entered the
  aggregate. `benchmarking-ragas-205-20260817_040939.json` reports `context_precision_scored:
  "109 of 109"` over 108 finite cells; the worst,
  `benchmarking-ragas-kbingest-20260709_052330.json`, reports `answer_relevancy_scored: "26 of 26"`
  over 17. §3.4 of the interpreting guide tells a reader to check exactly this denominator before
  trusting an aggregate, so an inflated one misstates the run's coverage at the point of the
  reader's only defence.
- **9 committed `_report.html` files render the unscored cells as a literal `nan`**, between 6 and
  36 occurrences each.

All 4 drifted artifacts sit inside the NaN-bearing 10, so no otherwise-clean file is touched.

The campaign plan (`docs/docs/proposals/feature-matrix-campaign-2026.md`) treats pre-campaign
artifacts as non-comparable, so this is hygiene rather than a campaign blocker. It carries no
milestone.

## What Changes

- The 10 NaN-bearing artifacts under `bench_out/` are rewritten through the merged helpers:
  `json.dump(json_safe(obj), f, indent=4, allow_nan=False)`, the exact call
  `ResultHandler.dump` makes. Every non-finite cell becomes `null`.
- Each arm's `<metric>_scored` is recomputed from the artifact itself: the count of finite
  per-question cells for that metric over the arm's scorable rows (`status` absent or `"ok"`,
  per `is_scorable` at `src/utils/benchmark_resilience.py:54`), over the count of scorable rows.
  5 strings change. `source_scored_count` is not a `<metric>_scored` string and is left alone.
- The 9 existing `_report.html` siblings are re-rendered and 10 `_report.md` siblings are created,
  both through `scripts/benchmarking/backfill_report_provenance.py`. After the re-render the word
  `nan` appears **0 times** across all 20 reports.
- A new `tests/unit/test_bench_out_artifacts.py` asserts both invariants over every committed
  artifact, plus the absence of a literal `nan` in every committed report. Without it the migration
  is a one-time byte edit that the next hand-edited artifact silently undoes.

Deliberately NOT changed:

- **No `src/` change.** The helpers this migration uses are already merged and correct; the defect
  is in the data. Mixing a behaviour change into a data PR would make the diff unreviewable.
- **The 8 clean artifacts.** They round-trip byte-identical through the same call, verified, so
  they are not rewritten and do not appear in the diff.
- **The trailing newline.** The harness writes no trailing newline after the closing brace. The
  migration must not add one; adding one would churn all 18 files and bury the 10 real changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None.

### Added Requirements

- `retrieval-benchmarking`: what a committed artifact guarantees to a reader. The nearest existing
  statement — the per-metric eligibility requirement that defines `n_scored / n_total` — lives only
  in the unarchived change `openspec/changes/align-ragas-benchmark-dialect/specs/retrieval-benchmarking/spec.md:78`
  and is **not** in `openspec/specs/retrieval-benchmarking/spec.md`. There is therefore no
  requirement to modify, and this change ADDs its own. That delta also states the contract for the
  harness at write time; this one states it for the artifacts at rest.

## Impact

- `bench_out/` — 10 JSON artifacts rewritten, 9 `_report.html` re-rendered, 10 `_report.md` created.
- `tests/unit/test_bench_out_artifacts.py` — new file.
- **Repository size: about +1.6 MB**, all of it the 10 newly created `_report.md` files. No
  `_report.md` exists in `bench_out/` today, so `--regenerate-md` creates rather than re-renders
  them. `regenerate_md` (`scripts/benchmarking/backfill_report_provenance.py:175`) documents this
  create path as the intended recovery for an artifact whose report write never landed, and
  acceptance criterion 3 of issue #426 asks for the md siblings, so they are created here. A
  reviewer who would rather not carry 1.6 MB of derived markdown can drop those 10 files and the
  report half of the test; the JSON migration stands on its own.
- Unblocks `compare_runs.py` and any strict parser against the pre-campaign artifacts.
- Coverage: the gate measures `--cov=src` only, and this diff contains no `src/` line, so
  `diff-cover` reports no lines with coverage information and passes. The new unit test and the
  before/after table in the PR body are the acceptance evidence, not a percentage.
