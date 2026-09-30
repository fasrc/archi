# Tasks: add-markdown-benchmark-report

Model tags record the Loop 2 routing decision per the spec-driven workflow. This change
is executed in one background session, so the tags record intent and escalation points
rather than live model switches.

## 1. Markdown formatter

- [x] 1.1 `model: sonnet` — RED: add `tests/unit/test_benchmark_report_markdown.py`
      with failing tests: header fields, aggregate RAGAS table, and per-question
      answer/expected-answer/scores on a minimal parsed fixture (RAGAS mode).
- [x] 1.2 `model: sonnet` — GREEN: implement `fence`, `format_provenance_markdown`,
      `format_version_markdown`, and `format_markdown_output` in
      `src/utils/generate_benchmark_report.py` (design decisions 1–3).
- [x] 1.3 `model: sonnet` — RED→GREEN: edge tests — content with backtick runs and
      `<jobid>` placeholders renders literally; badge thresholds at 0.49/0.5/0.7;
      `provenance=None` and missing keys render "not recorded", no crash; SOURCES-mode
      retrieval-accuracy section; adversarial payloads (markdown syntax and raw HTML)
      in the config name, question text, and source names are escaped by `md_escape`
      and cannot restructure the report.
- [x] 1.4 `model: opus` — RED→GREEN: extract `extract_context_text(ctx)` and switch
      both formatters to it (design decision 6); direct tests for embedded quotes and
      non-`page_content=` shapes; assert the HTML output for a context fixture is
      unchanged before/after the switch.

## 2. CLI default flip

- [x] 2.1 `model: sonnet` — RED: CLI tests — no flag writes `<stem>_report.md` only;
      `--html_output` alone writes only HTML; both flags write both files.
- [x] 2.2 `model: sonnet` — GREEN: add `--markdown_output`, flip the default in
      `main()` (design decision 5).

## 3. End-of-run dump flip

- [x] 3.1 `model: opus` — RED: tests against `dump_artifacts` — one call writes
      `<name>-<ts>.json` AND `<name>-<ts>_report.md` with the same stem, the report
      rendered by `format_markdown_output`; force a clock rollover between the two
      writes (a fake `datetime` whose `now()` advances per call) and assert the stems
      still match (monkeypatch `OUTPUT_DIR`; `src/bin/service_benchmark.py` is a known
      large-file gate risk — respect the seam-scout verdict before the in-place edit).
      Also RED: a failure test — the markdown render raises after the JSON write, the
      JSON stays on disk, the failure is logged, no exception escapes `dump_artifacts`.
- [x] 3.2 `model: opus` — GREEN: add `dump_artifacts(benchmark_name)` that stamps once
      and performs both writes; rename `dump_html` → `dump_report` writing markdown;
      make `timestamp` a required parameter of `dump()` and `dump_report()`; replace
      the two run-tail calls at `src/bin/service_benchmark.py:2012-2013` with the one
      `dump_artifacts` call.

## 4. Backfill markdown path

- [x] 4.1 `model: sonnet` — RED: tests for `--regenerate-md` — an existing `_report.md`
      sibling is re-rendered; a MISSING sibling of a valid artifact is created (the
      recovery path); a foreign JSON with a `metadata` key but no parseable
      `benchmarking_results` is skipped cleanly (no file, no error), as is a plain
      non-dict JSON; `--regenerate-html` behavior is untouched.
- [x] 4.2 `model: sonnet` — GREEN: add `regenerate_md` and the `--regenerate-md` flag
      to `scripts/benchmarking/backfill_report_provenance.py` (design decision 7).

## 5. Gate and review

- [x] 5.1 `model: opus` — run `bash scripts/gate.sh` (full toolchain env), fix to green.
- [x] 5.2 `model: opus` — pre-PR adversarial review loop (`/codex:adversarial-review`),
      verify each finding, fix or push back, re-run until clean or nits-only.
- [x] 5.3 `model: opus` — push the branch, open the PR to `dev` per `info-pr-overview`,
      request `@codex review` as a comment.
