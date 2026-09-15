# Proposal: add-markdown-benchmark-report

## Why

Benchmark runs write their human-readable report only as a hand-built HTML file
(`format_html_output`, `src/utils/generate_benchmark_report.py:279`). HTML is heavy to
diff, to paste into GitHub issues and PRs, and to read in a terminal. The RAGAS library
itself emits no report at all — the format is fully ours to choose. Markdown gives the
same content in a form that renders on GitHub, diffs cleanly, and reads as plain text.

## What Changes

- Add `format_markdown_output` to `src/utils/generate_benchmark_report.py`. It renders
  the same parsed data as the HTML report: run header, provenance, versions, RAGAS
  totals, and per-question results, as GitHub-flavored markdown.
- **BREAKING (output default):** `ResultHandler` in `src/bin/service_benchmark.py`
  writes `<name>-<timestamp>_report.md` by default at the end of a run, instead of
  `_report.html`. The JSON dump is unchanged.
- **BREAKING (CLI default):** the `generate_benchmark_report.py` CLI writes markdown by
  default (`<stem>_report.md`). A new `--markdown_output` flag names the markdown path.
  The existing `--html_output` flag still produces the HTML report as an opt-in.
- `scripts/benchmarking/backfill_report_provenance.py` gains a `--regenerate-md` flag
  that re-renders an existing `_report.md` sibling and creates a missing one for a
  valid artifact, so the default artifact has a bulk-maintenance AND recovery path.
  Its `--regenerate-html` path is unchanged.
- `format_html_output` is unchanged. HTML stays available on demand, re-rendered from
  the saved JSON.

No automation consumes the `_report.html` files (verified 2026-08-28: no script, cron,
or service reads them; only humans do), so the default flip breaks no pipeline. One
historical contract is superseded knowingly: the archived change
`2026-06-03-adopt-argilla-benchmark-platform` pinned evaluate-without-`--argilla` to
"JSON + HTML output only" as a non-regression clause *for the Argilla adoption*. That
clause froze the then-current behavior; it was never synced into a live spec under
`openspec/specs/`. This change supersedes that historical default deliberately, and the
new `benchmark-report-rendering` spec becomes the live contract for report formats.

## Capabilities

### New Capabilities
- `benchmark-report-rendering`: the formats of the human-readable benchmark report, the
  default format at the end of a run and in the CLI, and the opt-in HTML path.

### Modified Capabilities

(none — `retrieval-benchmarking` does not constrain the report file format)

## Impact

- `src/utils/generate_benchmark_report.py`: new markdown formatter, CLI default flip.
- `src/bin/service_benchmark.py`: the end-of-run report dump writes markdown.
- `tests/unit/`: new tests for the markdown formatter, the two default flips, the
  shared context-text helper, and the backfill markdown path; the four existing HTML
  report test files stay valid because `format_html_output`'s output is unchanged.
- `scripts/benchmarking/backfill_report_provenance.py`: new `--regenerate-md` flag.
- Operators: new runs produce `_report.md`; old `_report.html` artifacts stay readable
  and re-renderable.
