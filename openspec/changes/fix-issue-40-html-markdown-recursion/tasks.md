## 1. Reproduce (RED)

- [ ] 1.1 In `tests/unit/test_html_to_markdown_processor.py`, add a failing test that builds
  a deeply-nested HTML string (~2000 nested `<div>`s), wraps it as
  `ScrapedResource(suffix="html")`, runs it through `HtmlToMarkdownProcessor().process(...)`,
  and asserts the result is CONVERTED (suffix becomes `md`, `metadata["converted_from"] ==
  "html"`, content is non-empty Markdown) — NOT the original HTML. Confirm it fails on the
  current code (RecursionError → raw-HTML fallback) before changing production code.

## 2. Fix (GREEN)

- [ ] 2.1 In `src/data_manager/collectors/processing.py` `HtmlToMarkdownProcessor.process`,
  run the `markdownify` conversion segfault-safely: bound `sys.setrecursionlimit(max(current,
  N))` and/or run the conversion in a worker thread created with an enlarged
  `threading.stack_size(...)`; restore any process-global recursion limit in `try/finally`.
  No new third-party dependency (stdlib `sys`/`threading` only).
- [ ] 2.2 Keep the existing fallbacks intact: a genuine conversion exception OR
  blank/whitespace output still returns the original resource (never block ingest).

## 3. Verify

- [ ] 3.1 The new deep-nesting test passes; existing
  `tests/unit/test_html_to_markdown_processor.py` cases still pass (raise-fallback and
  blank-output-fallback behaviors unchanged).
- [ ] 3.2 `bash scripts/gate.sh` is green (diff-cover ≥ 80% on changed lines); `git diff
  origin/dev -- pyproject.toml requirements/requirements-base.txt` shows no added package.
