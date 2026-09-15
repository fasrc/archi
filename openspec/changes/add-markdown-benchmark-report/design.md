# Design: add-markdown-benchmark-report

## Context

`src/utils/generate_benchmark_report.py` renders a benchmark JSON artifact as HTML:
`format_html_output` (line 279) plus two helpers, `format_provenance_html` (line 86) and
`format_version_html` (line 160). Three callers exist:

- `ResultHandler.dump_html` (`src/bin/service_benchmark.py:474`, called at line 2013)
  writes `<name>-<utc-timestamp>_report.html` at the end of a run.
- The module CLI `main()` (line 770) re-renders a report from a saved JSON file and
  defaults to `<stem>.html`.
- `regenerate_html` in `scripts/benchmarking/backfill_report_provenance.py` re-renders
  existing `_report.html` files behind an explicit `--regenerate-html` flag.

No automation reads the HTML files. The JSON artifact next to each report holds all of
the data, so any old run can be re-rendered in a new format.

## Goals / Non-Goals

**Goals:**
- A markdown report with the same content as the HTML report, correct on GitHub.
- Markdown is the default: the end-of-run dump and the CLI both write `_report.md`.
- HTML stays available through the CLI `--html_output` flag and the backfill script.

**Non-Goals:**
- No change to `format_html_output`'s rendered output or to the JSON dump.
- No re-render of existing `bench_out/` artifacts in this change.
- No new dependency (no `tabulate`, no template engine — plain string assembly, like
  the HTML formatter).

## Decisions

1. **New functions, same module.** Add `format_markdown_output`,
   `format_provenance_markdown`, and `format_version_markdown` to
   `generate_benchmark_report.py`, mirroring the HTML trio section for section: header,
   run provenance, versions, retrieval accuracy (SOURCES mode), aggregate RAGAS metrics
   (RAGAS mode), then one section per question (question text, retrieval check,
   archi's answer, expected answer, expected sources, retrieved documents, agent
   messages, per-question RAGAS scores). Rationale: one module owns report rendering;
   the parsed-data contract (`parse_benchmark_results`) is shared unchanged.

2. **Every artifact-sourced string is neutralized.** Long-form fields (answers,
   contexts, message bodies) go in fenced code blocks: a `fence` helper wraps text in a
   backtick fence one backtick longer than the longest backtick run inside the text
   (minimum three), so content can never break out of its block. Every OTHER
   interpolated field that comes from the artifact or config (configuration name,
   question text, source and document names, metric labels derived from data) is
   markdown-escaped by an `md_escape` helper (backslash-escape markdown structure
   characters and neutralize raw HTML) before interpolation. Rationale: the report is
   pasted into GitHub, where unescaped markup in a data field could restructure or
   hide report content — the report is benchmark evidence and must not be forgeable by
   its own inputs. The HTML formatter escapes for the same reason.

3. **Scores carry a text badge, not color.** The HTML report encodes the 0.5 / 0.7
   thresholds as red / yellow / green CSS classes. Markdown gets the same thresholds as
   🔴 / 🟡 / 🟢 next to each score, so the at-a-glance read survives the format change.

4. **`dump_html` becomes `dump_report` and writes markdown.** Only the definition and
   one call site reference it (verified). The filename becomes
   `<name>-<utc-timestamp>_report.md`. Alternative considered: write both HTML and
   markdown per run — rejected, it doubles report clutter in `bench_out/` and HTML is
   one CLI invocation away from the JSON.

5. **CLI semantics.** `--markdown_output <path>` is added; `--html_output <path>` stays.
   No flag → markdown to `<stem>_report.md`, where `<stem>` is the input JSON's stem
   (the old default was `<stem>.html`; the `_report.md` suffix matches what a run
   writes). `--html_output` alone → HTML only (old behavior, opt-in). Both flags → both
   files. Note: today a run stamps the JSON and the report filenames with separate
   `datetime.now()` calls (`ResultHandler.dump` and `dump_html`, called back to back at
   `src/bin/service_benchmark.py:2012-2013`), so their stems can differ across a second
   boundary — and the backfill script locates reports strictly as the JSON's `_report`
   sibling. Because this change promises a bulk re-render path for the default
   artifact, the sibling invariant is enforced by the API shape, not by caller
   discipline: a new `ResultHandler.dump_artifacts(benchmark_name)` captures the
   timestamp once and performs both writes; the run tail calls only it. `dump()` and
   `dump_report()` take a **required** `timestamp` parameter, so no caller can stamp
   them independently by accident. The markdown report is therefore always the JSON's
   sibling, and `--regenerate-md` can honestly claim coverage.

6. **The `page_content=` context parsing is extracted, not duplicated.** A module-level
   `extract_context_text(ctx)` helper takes over the string-slicing that
   `format_html_output` does inline (lines 635–657, including the failure-swallowing
   `except`), and both formatters call it. Rationale: the parsing is fragile (embedded
   quotes, a LangChain `Document.__repr__` shape change), and a copy in each renderer
   doubles the drift surface for benchmark evidence. The helper gets direct tests for
   quoted content and non-`page_content=` shapes; the HTML formatter's rendered output
   stays byte-identical.

7. **The backfill script gains a markdown path that can also recover.**
   `--regenerate-md` on `scripts/benchmarking/backfill_report_provenance.py` renders
   each valid artifact's `_report.md` sibling via `format_markdown_output` — it
   re-renders an existing sibling AND creates a missing one. This deliberately differs
   from `--regenerate-html` (which only re-renders what exists): markdown is now the
   default artifact, so a valid JSON without its report is a recoverable gap, not an
   operator's choice. Because creation removes the existing-sibling guard that
   implicitly filtered foreign files, the script's `NOT_AN_ARTIFACT` detection (today
   just "a dict with a `metadata` key") is not enough for the create path: before
   creating a missing report, the script must validate the artifact by parsing it the
   way the renderer will (`benchmarking_results` present with a parseable first
   record), and skip cleanly — no error, no file — when the parse fails. Without this
   flag, a partial-write failure would leave a JSON whose default report nothing can
   rebuild.

8. **A report-write failure never loses the JSON.** `dump_artifacts` writes the JSON
   first (it is the source of truth), then renders and writes the markdown report;
   a renderer exception or write failure in the report step is caught and logged
   clearly, the JSON survives, and `--regenerate-md` is the recovery path. This
   preserves the current safety property (today `dump()` runs before `dump_html()`,
   so a report failure already leaves the JSON on disk).

## Risks / Trade-offs

- [An operator's habit or a personal script expects `_report.html` from a run] →
  Mitigation: the proposal marks the flip **BREAKING**, the JSON is unchanged, and one
  CLI command regenerates the HTML view. Verified that nothing in the repo consumes
  the HTML.
- [`src/bin/service_benchmark.py` is large; an in-place edit can trip the black-churn
  diff-coverage trap] → Mitigation: pre-edit seam check; keep the edit to the method
  body and the one call site; unit tests exercise `ResultHandler` already.
- [Markdown output diverges from HTML content over time] → Accepted: the two
  formatters share `parse_benchmark_results` and `extract_context_text`, and the spec
  pins the section list.
- [The archived Argilla-adoption change pinned evaluate-without-`--argilla` to "JSON +
  HTML output only"] → That clause was a non-regression guard for the Argilla adoption,
  frozen to 2026-06 behavior, and was never synced to a live spec. The proposal names
  it and supersedes it explicitly; the new `benchmark-report-rendering` spec is the
  live contract.

## Migration Plan

Code-only change; no deploy step. Operators who want HTML for an existing run use
`python src/utils/generate_benchmark_report.py <run>.json --html_output <run>_report.html`.

## Open Questions

None.
